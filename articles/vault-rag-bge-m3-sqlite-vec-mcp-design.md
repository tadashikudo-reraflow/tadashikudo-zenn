---
title: "bge-m3 + sqlite-vec で作るObsidian Vault検索基盤の設計：MCP統合まで"
emoji: "🗄️"
type: "tech"
topics: ["Obsidian", "sqlitevec", "bgem3", "RAG", "MCP"]
published: false
---
# bge-m3 + sqlite-vec で作る Obsidian Vault 検索基盤の設計：MCP 統合まで

## はじめに

Obsidian Vault が育ってくると、`grep` だけでは目的のノートに辿り着けない場面が増えてきます。「あの設計判断、どこに書いたっけ？」「過去の似た失敗ノートを引きたい」——表現揺れや概念検索が必要なとき、ベクトル検索は強力な相棒になります。

本記事では、筆者が 1,100 ノート規模（約 2 万チャンク・26 PJ・2026-05 時点）の Obsidian Vault に対して **sqlite-vec + bge-m3** で構築したセマンティック検索基盤を、Claude Code から呼べる **MCP サーバー** として公開するまでの設計を整理します。

ゴールは次の 3 点です。

- Vault を SSOT（Single Source of Truth）として一切壊さない
- 完全ローカルで動く（外部 API 課金ゼロ）
- Claude Code からシームレスに `search_vault(query)` できる

## なぜ Smart Connections ではなく自作したのか

最初に断っておくと、軽量に試したいだけなら **Obsidian の Smart Connections プラグイン**で十分です。`bge-micro-v2`（384dim）でローカル embedding を作ってくれて、コミュニティ MCP も用意されています。Phase 0 として 10 分でセットアップし、「自分のワークフローに本当に必要か」を実体験するのがおすすめです。

筆者が自作に踏み切った理由は次の 3 つでした。

1. **PJ 横断フィルタが必要**：「特定 PJ のノートだけ」「特定タグだけ」を絞り込みたい
2. **1024dim の精度が欲しい**：384dim では概念近接性の取りこぼしが目立った
3. **Claude Code から MCP で呼びたい**：エージェントのトークン消費を 50% 以上削減したい

「不便を 3 つ言語化できないなら自作しない」を判断基準にすると失敗が減ります。

## アーキテクチャ全体図

```
┌─ Google Drive ──────────────────────┐
│  Obsidian Vault (SSOT)              │
│  1,100+ .md / 26 PJ                 │
└──────────┬──────────────────────────┘
           │ 読み取りのみ
           ▼
┌─ Mac ローカル ──────────────────────┐
│  ~/.local/lib/vault-rag/            │
│  ├── indexer.py   (Vault→chunk→emb) │
│  ├── mcp_server.py (検索API)        │
│  └── config.toml                    │
│                                     │
│  ~/.local/share/vault-index/        │
│  └── vault.db (sqlite-vec)          │
│                                     │
│  sentence-transformers (bge-m3)     │
│                                     │
│  Claude Code ── MCP ──> vault.db    │
└─────────────────────────────────────┘
```

ポイントは **Vault に書き戻さない**こと。Vault は読み取り専用、index DB はローカルに完全分離します。撤退も `rm -rf` 一発でゼロリスクです。

## Phase 0: 動作確認

最初に依存だけ通しておきます。

```bash
# 仮想環境
python3 -m venv ~/.local/lib/vault-rag/.venv
source ~/.local/lib/vault-rag/.venv/bin/activate

# 依存
pip install sqlite-vec sentence-transformers mcp watchdog pyyaml

# bge-m3 モデルの動作確認（初回はモデルDLで2-3分）
python3 -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('BAAI/bge-m3')
v = m.encode(['テスト文字列'])
print(v.shape)  # (1, 1024) なら OK
"
```

`(1, 1024)` が出れば準備完了です。

## Phase 1: インデクサの設計

### チャンク分割

Obsidian の `.md` は見出し構造が明確なので、**`##` / `###` 単位で分割し、長すぎるブロックは文単位でサブ分割**するだけで十分な検索精度が出ます。スライディングウィンドウのオーバーラップは入れていません（見出し境界＝意味境界として効くので、固定オーバーラップより素直）。

```python
import re

SENT_SPLIT = re.compile(r'(?<=[。．！？\n])')

def chunk_markdown(text: str, max_chars: int = 800) -> list[str]:
    """`##` / `###` で分割。長すぎるセクションは文単位でサブ分割。"""
    sections = re.split(r'(?m)^#{2,3} ', text)
    chunks: list[str] = []
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(sec) <= max_chars:
            chunks.append(sec)
            continue
        # 文単位で詰めて max_chars に収める
        buf = ""
        for sent in SENT_SPLIT.split(sec):
            if len(buf) + len(sent) > max_chars and buf:
                chunks.append(buf.strip())
                buf = sent
            else:
                buf += sent
        if buf.strip():
            chunks.append(buf.strip())
    return chunks
```

YAML frontmatter は除外してから渡します（embedding にメタタグ列が混じるとノイズ源になる）。

### sqlite-vec へ書き込み

`sqlite-vec` は SQLite 拡張として動くため、既存の `sqlite3` モジュールがそのまま使えます。

```python
import sqlite3
import sqlite_vec
from sentence_transformers import SentenceTransformer

DB_PATH = "~/.local/share/vault-index/vault.db"
MODEL = SentenceTransformer("BAAI/bge-m3")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY,
            path TEXT UNIQUE,
            pj TEXT,
            mtime REAL,
            content_hash TEXT
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,
            chunk_idx INTEGER,
            text TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vec USING vec0(
            embedding float[1024]
        );
    """)
    return conn

def upsert_note(conn, path: str, pj: str, content: str):
    chunks = chunk_markdown(content)
    embeddings = MODEL.encode(chunks, normalize_embeddings=True)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO notes(path, pj, mtime) VALUES (?, ?, ?)",
                (path, pj, os.path.getmtime(path)))
    note_id = cur.lastrowid
    for idx, (text, vec) in enumerate(zip(chunks, embeddings)):
        cur.execute("INSERT INTO chunks(note_id, chunk_idx, text) VALUES (?, ?, ?)",
                    (note_id, idx, text))
        chunk_id = cur.lastrowid
        cur.execute("INSERT INTO chunk_vec(rowid, embedding) VALUES (?, ?)",
                    (chunk_id, vec.tobytes()))
    conn.commit()
```

### 差分更新

`mtime` と `content_hash`（SHA-256）を比較して未変更ファイルをスキップします。筆者の環境では全件 143 秒・差分 20 秒（5 ファイル更新時）まで縮みました。

## Phase 2: 検索クエリと MCP サーバー

### 検索の核

`vec0` は `MATCH` で k-NN を直接引けます。

```python
def search(conn, query: str, top_k: int = 5, pj_filter: str | None = None):
    qvec = MODEL.encode([query], normalize_embeddings=True)[0]
    sql = """
        SELECT n.path, n.pj, c.text, v.distance
        FROM chunk_vec v
        JOIN chunks c ON c.id = v.rowid
        JOIN notes  n ON n.id = c.note_id
        WHERE v.embedding MATCH ? AND k = ?
    """
    params: list = [qvec.tobytes(), top_k * 3]
    if pj_filter:
        sql += " AND n.pj = ?"
        params.append(pj_filter)
    sql += " ORDER BY v.distance LIMIT ?"
    params.append(top_k)
    return list(conn.execute(sql, params))
```

`top_k * 3` で取って `pj_filter` 後にスライスするのは、フィルタ後の件数が `top_k` を下回るのを防ぐためです。

### MCP サーバー化

公式 `mcp` Python SDK 同梱の `FastMCP` を使うと、Python 関数に `@mcp.tool()` を付けるだけで Claude Code から呼べる MCP サーバーが立ちます（`pip install mcp` で入る・3rd party の `fastmcp` パッケージとは別物なので注意）。

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("vault-rag")

@mcp.tool()
def search_vault(query: str, top_k: int = 5, pj_filter: str | None = None) -> list[dict]:
    """Vault をセマンティック検索する。query は日本語/英語どちらでも可。"""
    rows = search(get_conn(), query, top_k, pj_filter)
    return [{"path": p, "pj": pj, "snippet": t[:300], "distance": d} for p, pj, t, d in rows]

@mcp.tool()
def find_similar_notes(file_path: str, top_k: int = 5) -> list[dict]:
    """指定ノートと意味的に近いノートを返す。"""
    ...

if __name__ == "__main__":
    mcp.run()
```

`~/.claude/.mcp.json` または各リポジトリの `.mcp.json` に登録すれば完了です。

```json
{
  "mcpServers": {
    "vault-rag": {
      "command": "/Users/yourusername/.local/lib/vault-rag/.venv/bin/python",
      "args": ["-m", "vault_rag.mcp_server"]
    }
  }
}
```

## ベクトル検索を「Grep の置き換え」にしないこと

実運用で一番ハマったのが **「全部ベクトル検索でいいや」と振り切ってしまう罠**でした。bge-m3 は強力ですが、関数名・固有名詞・ファイル名のような完全一致は Grep のほうが速くて正確です。

筆者は次のテーブルを判断基準として運用しています。

| 検索対象 | 優先方式 | 理由 |
|---------|---------|------|
| 正確なファイル名・キーワード・関数名 | `Grep` / `Glob` | 完全一致・最新性・低コスト |
| 概念・文脈・過去判断の探索 | `search_vault` | 表現揺れ・関連ノート発見に強い |
| 特定ノートの関連探索 | `find_similar_notes` | 近接テーマ発見 |
| 回答根拠の確認 | 原文 Read | ベクトル結果だけで確定しない |

ベクトル検索は **「概念の入り口」** であり、最終的な根拠確認は必ず原文を `Read` する。これを守るとハルシネーションが激減します。

## まとめ

- **Smart Connections で十分かをまず試す**：自作のコストは「不便を 3 つ言語化できる」ときだけ正当化される
- **Vault は読み取り専用に保つ**：index DB はローカル完全分離・撤退ゼロリスク
- **チャンクは `##` / `###` 分割 + 文単位サブ分割**で十分実用（オーバーラップは入れず、見出し境界に頼る）
- **`mtime` + `content_hash` 差分更新**で全件 143 秒 → 差分 20 秒に短縮
- **MCP は公式 `mcp` SDK の `FastMCP` で `@mcp.tool()` を貼るだけ**、Claude Code 連携は数十行で完結
- **Grep / Glob と役割分担**する：ベクトル検索は「概念の入り口」、根拠は原文 Read

筆者の環境では、エージェントから過去判断を引くトークン消費が体感で半分以下に減り、「あの判断、どこに書いたっけ？」のストレスがほぼ消えました。同じように Vault 検索に困っている方の参考になれば幸いです。
