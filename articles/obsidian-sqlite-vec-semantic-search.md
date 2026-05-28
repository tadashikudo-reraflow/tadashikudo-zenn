---
title: "sqlite-vec + bge-m3 でObsidianにセマンティック検索を実装した話"
emoji: "🗄️"
type: "tech"
topics: ["Obsidian", "sqlite", "ベクトル検索", "Python", "AI"]
published: false
---
# sqlite-vec + bge-m3 でObsidianにセマンティック検索を実装した話

## はじめに

ObsidianはMarkdownベースの知識管理ツールとして非常に優秀ですが、標準の検索機能はキーワード一致のみです。「〜に関するノート」「あの議論をしたときの判断メモ」のような曖昧な検索はできません。

そこで、次の構成でセマンティック検索を実装しました。

- **sqlite-vec**: SQLiteの拡張でベクトル類似検索を実現
- **bge-m3**: 日英バイリンガル対応の高品質Embeddingモデル（BAAI製）
- **FastMCP**: MCPサーバー化してAIエージェントから直接呼べる形に

この記事では、実装の全容をコードとともに解説します。

## システム設計の全体像

```
Obsidian Vault (.md / .canvas)
        ↓ 15分ごとに差分インデックス（launchd）
  chunker.py（見出し単位でチャンク分割）
        ↓
  embedder.py（bge-m3で1024次元ベクトル生成）
        ↓
  db.py（sqlite-vecに保存）
        ↓
  mcp_server.py（FastMCPでMCPサーバー公開）
        ↓
 Claude Code / AIエージェントからsearch_vault()呼び出し
```

実績値：

| 指標 | 結果 |
|------|------|
| 全件インデックス | 約143秒（300件以上のファイル→約2,900チャンク） |
| 差分インデックス | 約20秒（5件更新時） |
| 検索速度 | 100ms以下 |
| DB容量 | 約16MB |

## Step 1: sqlite-vec の初期化とスキーマ設計

sqlite-vecはSQLiteのロード可能拡張として提供されています。Pythonパッケージ `sqlite-vec` をインストールすると、拡張ファイルが同梱されます。

```bash
uv add sqlite-vec sentence-transformers
```

DBスキーマは `notes`（ファイルメタデータ）・`chunks`（チャンクテキスト）・`chunks_vec`（ベクトル仮想テーブル）の3テーブルで構成します。

```python
import sqlite3
import struct
import sqlite_vec

EMBEDDING_DIM = 1024  # bge-m3の次元数

def get_connection(readonly: bool = False) -> sqlite3.Connection:
    """sqlite-vecをロードした接続を返す。"""
    con = sqlite3.connect("vault.db")
    con.row_factory = sqlite3.Row
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")  # 読み書き並列対応
    return con

def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            title TEXT,
            pj_tag TEXT,
            mtime REAL,
            content_hash TEXT,
            indexed_at REAL
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            heading_context TEXT,
            FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
        );

        -- sqlite-vecの仮想テーブル（float[1024]がベクトル型）
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
            embedding float[{EMBEDDING_DIM}]
        );
    """)
    con.commit()
```

ポイントは `vec0` 仮想テーブルです。`float[1024]` のようにベクトルの次元数を型として指定するだけで、ANN（近似最近傍）検索が有効になります。WALモードを有効にすることで、インデックス更新と検索を並列実行できます。

ベクトルの挿入時にはバイト列へのシリアライズが必要です：

```python
def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)
```

## Step 2: bge-m3 でEmbeddingを生成する

bge-m3は中国・英語・日本語に強いバイリンガルモデルです。Obsidianのノートは日英混在が多いため、これを選びました。

```python
from sentence_transformers import SentenceTransformer

EMBEDDING_MODEL = "BAAI/bge-m3"

_model: SentenceTransformer | None = None

def get_model() -> SentenceTransformer:
    """シングルトンでモデルをロード（遅延初期化）。"""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model

def embed_texts(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    model = get_model()
    embeddings = model.encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=len(texts) > 20,
        normalize_embeddings=True,  # コサイン類似度のために正規化
    )
    return [e.tolist() for e in embeddings]

def embed_query(text: str) -> list[float]:
    model = get_model()
    return model.encode(text, normalize_embeddings=True).tolist()
```

`normalize_embeddings=True` を指定することで、ベクトルをL2正規化します。これにより内積がコサイン類似度と等価になり、sqlite-vecのANN検索と整合が取れます。

初回ロードは数秒かかりますが、シングルトンパターンで一度だけ実行されます。

## Step 3: Markdownをチャンクに分割する

Obsidianの1ファイルをそのままEmbeddingに投入するとコンテキストが混在して検索精度が落ちます。見出し（`##`/`###`）単位でチャンク分割し、大きいセクションはさらに文境界で分割します。

```python
import re

MAX_CHUNK_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200

def chunk_markdown(text: str, title: str = "") -> list[dict]:
    """見出し単位でチャンク分割し、大きいものは文境界で再分割。"""
    sections = _split_by_headings(text)
    chunks = []

    for heading, section_text in sections:
        if len(section_text) <= MAX_CHUNK_CHARS:
            chunks.append({"content": section_text, "heading": heading})
        else:
            for sub in _split_by_sentences(section_text):
                chunks.append({"content": sub, "heading": heading})

    # タイトル+見出しをprefixとして付加（Embedding品質改善）
    result = []
    for i, chunk in enumerate(chunks):
        prefix = f"{title} | {chunk['heading']}" if chunk["heading"] else title
        content = f"{prefix}\n\n{chunk['content']}" if prefix else chunk["content"]
        result.append({
            "content": content.strip(),
            "heading": chunk["heading"],
            "index": i,
        })
    return result

def _split_by_headings(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [("", text)]
    sections = []
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            sections.append((heading, content))
    return sections
```

`prefix`としてタイトルと見出しをチャンク先頭に付加するのがポイントです。これだけで検索精度が体感できるほど向上しました。理由は、モデルがセクション単体では判断しにくい文脈を、タイトルのヒントで補完できるためです。

## Step 4: セマンティック検索の実行

検索時は `WHERE cv.embedding MATCH ?` 構文でANN検索を行います。

```python
def semantic_search(query: str, top_k: int = 5, pj_filter: str | None = None) -> list[dict]:
    vec = embed_query(query)
    con = get_connection(readonly=True)

    sql = """
        SELECT
            cv.distance,
            c.content,
            c.heading_context,
            n.file_path,
            n.title,
            n.pj_tag
        FROM chunks_vec cv
        JOIN chunks c ON c.id = cv.rowid
        JOIN notes n ON n.id = c.note_id
        WHERE cv.embedding MATCH ?
          AND k = ?
    """
    params = [serialize_f32(vec), top_k * 3]  # 多めに取得して後フィルタ

    if pj_filter:
        sql += " AND n.pj_tag = ?"
        params.append(pj_filter)

    sql += " ORDER BY cv.distance ASC"
    rows = con.execute(sql, params).fetchall()
    con.close()

    # 同一ファイルからのチャンクは最大2件に制限
    results = []
    for row in rows:
        if len(results) >= top_k:
            break
        path_count = sum(1 for r in results if r["path"] == row["file_path"])
        if path_count >= 2:
            continue
        results.append({
            "path": row["file_path"],
            "title": row["title"],
            "chunk": row["content"][:500],
            "heading": row["heading_context"] or "",
            "score": round(1.0 - row["distance"], 4),  # distance → similarity
        })
    return results
```

`cv.distance` はL2距離（またはコサイン距離）で、`1.0 - distance` でコサイン類似度に変換します。同一ファイルから多数ヒットするのを防ぐため、1ファイルあたり最大2チャンクに制限しています。

## Step 5: FastMCPでAIエージェントに公開する

最後にMCPサーバーとして公開することで、Claude CodeなどのAIエージェントから直接呼べるようになります。

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("vault-rag")

@mcp.tool()
def search_vault(query: str, top_k: int = 5, pj_filter: str | None = None) -> list[dict]:
    """Obsidian Vaultをセマンティック検索する。

    Args:
        query: 自然言語クエリ（日本語・英語対応）
        top_k: 返す件数（デフォルト: 5）
        pj_filter: PJタグでフィルタ（例: "PJ03"）
    """
    return semantic_search(query, top_k=top_k, pj_filter=pj_filter)

@mcp.tool()
def reindex_vault(full_rebuild: bool = False) -> dict:
    """差分インデックス更新（full_rebuild=Trueで全件再構築）。"""
    return index_vault(full_rebuild=full_rebuild)

def main():
    mcp.run(transport="stdio")
```

`.mcp.json` に追加するだけで、Claude Codeから `mcp__vault-rag__search_vault` として呼べるようになります。

```json
{
  "mcpServers": {
    "vault-rag": {
      "command": "uv",
      "args": ["run", "--project", "~/.local/lib/vault-rag", "vault-rag-mcp"],
      "env": {}
    }
  }
}
```

## まとめ

sqlite-vec + bge-m3 + FastMCPの組み合わせで、ローカル完結のセマンティック検索基盤を構築しました。実践的なポイントをまとめます。

- **sqlite-vecはSQLiteの拡張なので追加インフラ不要**。`sqlite_vec.load(con)` の一行でANN検索が使える
- **bge-m3は日英バイリンガル対応**。日本語ノートに英語コードが混在する環境でも高精度
- **チャンクの先頭にタイトル+見出しをprefixとして付加**することで検索精度が大幅に向上する
- **WALモードで読み書き並列**。launchdの差分インデックス更新中でも検索レイテンシに影響なし
- **MCPサーバー化することでAIエージェントから直接呼び出し可能**になり、RAGパイプラインが組みやすくなる

完全なソースコードの構成は `embedder.py` / `chunker.py` / `db.py` / `search.py` / `mcp_server.py` の5ファイルで、合計約400行で実現できました。Obsidianユーザーで「あのノート、どこだっけ？」と感じている方にぜひ試してみてください。
