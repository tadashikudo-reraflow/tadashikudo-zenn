---
title: "全PJのコードを横断ベクトル検索: SQLite-vec + bge-m3 で自前code-ragを作った（vault-rag兄弟版）"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "RAG", "SQLite", "MCP", "ベクトル検索"]
published: true
---
# 全PJのコードを横断ベクトル検索: SQLite-vec + bge-m3 で自前code-ragを作った（vault-rag兄弟版）

Claude Code の使用ログを集計したら、Bash 82% / Read 18% という数字が出た。

grep で50ヒット → 5ファイル全文 Read を繰り返していた結果だ。「`embedding` 実装ってどのPJで書いたっけ」と探すだけで 80〜100K トークン消費するケースが出ていた。これを **2〜3K トークンに圧縮する**仕組みを、課金ゼロ・OSSだけで作った話を書く。

**結果**: 20リポ / 757ファイル / 2,609チャンク / 19MB DB。検索レイテンシ < 1秒。毎日3:00 JSTに LaunchAgent が差分インデックスを実行している。

---

## なぜ自前で作ったか

まず「claude-context」（GitHub ★67k）という有名ツールがある。Claude にコードコンテキストを渡すためのツールで、OpenAI Embedding API を使う。Ollama で代替もできるが、そもそも私の環境には vault-rag というものがすでに動いていた。

vault-rag は ObsidianVault（1.5万件のノート）を SQLite-vec + bge-m3 でベクトル索引し、Claude Code から MCP 経由でセマンティック検索できる自前実装だ。これを `~/workspace/pj/` 以下の全コードに横展開するだけならコストほぼゼロで作れる、と判断した。

### 3層 RAG 構成

```
┌─────────────────────────────────────────────────────────────┐
│  3層RAG構成（適材適所の分離）                                  │
├─────────────────────────────────────────────────────────────┤
│  vault-rag        → ObsidianVault（知識ノート 1.5万件）        │
│  (SQLite-vec)       bge-m3 / 160MB / 15分毎差分               │
│                                                              │
│  digital-go-jp-rag → 政策文書RAG（28万チャンク）               │
│  (Docker Postgres)  Gemini 3072次元 / 6.4GB / pgvector HNSW  │
│                                                              │
│  ★ code-rag (NEW) → ~/workspace/pj/ 全コード（757ファイル）   │
│  (SQLite-vec)       bge-m3 / 19MB / 毎日3:00 JST差分          │
└─────────────────────────────────────────────────────────────┘
```

文書の大きさと用途が全く異なるので、3つを統合せず分離した。コードRAGはノート検索と索引特性が異なる（識別子・シンボル中心）し、政策文書RAGの28万チャンクに混ぜると横断クエリが複雑になる。

---

## 実装：vault-rag を fork して 500 行で動かす

ディレクトリ構成はこうなっている：

```
~/.local/lib/code-rag/
  code_rag/
    config.py      # 拡張子・除外パターン・リポマーカー
    db.py          # repos / files / chunks / chunks_vec スキーマ
    chunker.py     # TS/JS regex + 中括弧追跡 / Python インデント追跡
    embedder.py    # bge-m3 CPU singleton（torch.no_grad / batch=4）
    indexer.py     # monorepo対応（1-2階層）/ 差分（content_hash）
    search.py      # repo/language フィルタ・top-2/file dedup
    mcp_server.py  # FastMCP 5ツール公開

~/.local/share/code-index/code.db   # 単一DB（全PJ統合）
~/Library/LaunchAgents/com.tadkud.code-rag-index.plist
```

### chunker.py — regex + ブレース追跡

TS/JS はブレースの深さを追跡してトップレベル定義を切り出す。Python はインデント 0 の `def`/`class` が対象。

```python
# TypeScript/JavaScript のパターン例
TS_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[:=].*?(?:=>|function)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)"), "interface"),
    # Next.js route handlers
    (re.compile(r"^\s*export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\s*\("), "route"),
]
```

定義が見つかれば開き中括弧から閉じ中括弧までを一つのチャンクとする。構造が検出できないファイルは 200 行のスライディングウィンドウにフォールバック。tree-sitter はコストが高いので Phase 3 のスコープにした。

### embedder.py — bge-m3 CPU singleton

bge-m3 は 1024 次元のローカルモデル。課金ゼロでコード検索に十分な精度が出る。

```python
DEFAULT_BATCH_SIZE = 4
MAX_SEQ_LENGTH = 512

def get_model() -> SentenceTransformer:
    """Lazy-load the embedding model (singleton, CPU only)."""
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL, device="cpu")
        _model.max_seq_length = MAX_SEQ_LENGTH
    return _model

def embed_texts(texts: list[str], batch_size: int = DEFAULT_BATCH_SIZE) -> list[list[float]]:
    model = get_model()
    with torch.no_grad():
        embeddings = model.encode(
            texts,
            batch_size=batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
    return embeddings.tolist()
```

### mcp_server.py — FastMCP 5ツール

Claude Code から直接呼び出せる MCP ツールを FastMCP で公開している：

```python
mcp = FastMCP(
    "code-rag",
    instructions="Local code search across ~/workspace/pj/ powered by bge-m3 + sqlite-vec",
)

@mcp.tool()
def search_code_tool(
    query: str,
    top_k: int = 5,
    repo: str | None = None,
    language: str | None = None,
) -> list[dict]:
    """Search code repos semantically.

    Args:
        query: Natural language query (JP or EN).
        repo: Optional repo filter (e.g. "PJ19").
        language: Optional language filter ('ts'/'tsx'/'js'/'py').
    """
    return search_code(query, top_k=top_k, repo=repo, language=language)
```

5ツール全部無料・全部ローカル:
- `search_code_tool` — 自然言語コード検索
- `find_similar_code_tool` — 類似コード発見
- `list_indexed_repos` — 索引済みリポ一覧
- `code_index_stats` — 集計統計
- `reindex_code` — 差分 or 全再構築

`.mcp.json` に登録するとセッション起動時に自動接続される：

```json
{
  "mcpServers": {
    "code-rag": {
      "command": "python3",
      "args": ["-m", "code_rag.mcp_server"],
      "cwd": "~/.local/lib/code-rag"
    }
  }
}
```

---

## メモリ最適化の戦い

最初の実装を動かした直後、Python のメモリ使用量が 5〜10 GB まで膨らんだ。Apple Silicon の MPS バックエンドが自動選択されていたことが原因だった。

| 項目 | 変更前 | 変更後 |
|------|--------|--------|
| デバイス | MPS 自動選択 | CPU 固定 |
| バッチサイズ | 8 | 4 |
| max_seq_length | 8192 | 512 |
| Flush タイミング | 200 chunks まとめて | ファイル毎 |
| autograd | ON | `torch.no_grad()` |
| スレッド数 | フル | OMP=4 / MKL=4 |
| GC | なし | バッチ毎 `gc.collect()` |

結果: **Peak RSS 5〜10 GB → 1.7 GB**。インデックス完了後はプロセスが終了するので、MCP サーバ常駐分は数百 MB に落ち着く。

保険として `~/.zshenv` に環境変数を追加した：

```bash
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export TOKENIZERS_PARALLELISM=false
```

---

## 横断発見の体験（クライマックス）

実際に動かしてみると、想定以上の体験だった。

クエリ：「embedding 生成 バッチ処理 リトライ」

```
Rank  Repo                      File                    Symbol                      Score
#1    digital-go-jp-rag         lib_embed_gemini.py     embed_texts (Gemini+retry)  0.1446
#2    PJ22_hokkaido-reform-navi scripts/build-rag.py    embed (Gemini+retry)        0.1263
#3    PJ19_GCInsight/gcportal   lib/rag.ts              fetchEmbeddingWithBackoff   0.1213
#4    digital-go-jp-rag         lib_embed_gemini.py     embed_texts_batched         0.1204
#5    PJ19_GCInsight/gcportal   lib/rag.ts              getEmbeddingBatch           0.1168
```

Python（Gemini API）と TypeScript（OpenAI API）の「同じ概念」を同一クエリで横断発見した。「PJ22 ではバックオフをどう書いてたっけ」という問いに、PJ19 と digital-go-jp-rag のコードが同時に出てくる。

反証クエリとして「Stripe webhook 決済処理」を試した。PJ19/gcportal には Stripe がそもそも実装されていない。結果は score が 0.0 に近く、関連性が低いとき正直に低スコアを返した。ハルシネーションは起きなかった。

---

## 自動化：LaunchAgent で毎日 3 時に差分インデックス

macOS の LaunchAgent に登録して深夜 3 時に自動実行している：

```xml
<!-- ~/Library/LaunchAgents/com.yourname.code-rag-index.plist -->
<key>StartCalendarInterval</key>
<dict>
  <key>Hour</key>
  <integer>3</integer>
  <key>Minute</key>
  <integer>0</integer>
</dict>
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>-m</string>
  <string>code_rag.indexer</string>
  <string>--all</string>
</array>
```

content_hash による差分検出を実装しているので、変更されたファイルだけを再インデックスする。作業時間帯に重い処理が走ることはない。

---

## トークン削減の実測

3つのクエリで Before/After を計測した：

| クエリ | Grep + Read（従来） | code-rag | 削減倍率 |
|--------|---------------------|---------|---------|
| 「newsletter 配信ロジック」 | 80K tok | 5K tok | **16×** |
| 「auth ミドルウェア」 | 120K tok | 6K tok | **20×** |
| 「embedding RAG 実装」 | 100K tok | 7K tok | **14×** |

Claude Code から `mcp__code-rag__search_code_tool` が自動採用されるようになってから、前述の Bash 82% 問題が改善した。

---

## 設計判断の記録

### 単一 DB vs リポ毎 DB → 単一 DB

横断クエリが最大の価値なので単一 DB にした。`repos` テーブルで `repo_id` を付与し、`chunks` テーブルが FK 参照する。検索時に `WHERE r.name LIKE %?%` で特定リポに絞り込める。

### bge-m3 vs Gemini Embedding

API 課金ゼロと vault-rag での実証済みスタックを優先した。1024 次元は Gemini（3072次元）より情報量は少ないが、コード検索では識別子の一致が多いため実用精度は十分。

---

## FAQ

**Q. なぜ claude-context を使わないのか？**
① OpenAI Embedding 課金が必須（Ollama 代替はあるが構築コスト発生）／② vault-rag で同等スタックが動いている（保守コスト最小）／③ コード特化の拡張がしやすい（自前実装なので）

**Q. tree-sitter は使わないのか？**
Phase 1 は規模より実用優先。207 ファイルで認証・RAG・Newsletter などの実クエリを確認した。完璧より「毎日使える」を優先。Phase 3 で移行を検討する。

**Q. メモリ 1.7 GB は重くない？**
ピークのみ。インデックス完了後はプロセスが終了する。MCP サーバ常駐分は数百 MB。LaunchAgent で深夜 3 時実行なので作業時の負荷はゼロ。

**Q. bge-m3 の精度は Gemini より劣るのでは？**
原理的に情報量は少ない。ただし実測 score 0.15 台で実用検索が成立した。コード検索は識別子マッチが多く、超高精度 embedding は不要。

---

## まとめ

vault-rag（ObsidianVault 索引）の兄弟として code-rag（コードリポ索引）を作った。同じ SQLite-vec + bge-m3 スタックで、課金ゼロ・全 20 リポ横断・毎日自動更新が動いている。

Claude Code のコンテキストを 80〜120K → 5〜7K トークンに圧縮したことで、「あのPJのあの実装はどうやってたっけ」という問いを自然言語で解決できるようになった。

コードを書いた記憶を外部化するのではなく、コードそのものを意味で繋げる。それが今のところの答えだ。

---

## 関連記事

- [ObsidianVault を丸ごと RAG 索引した話（vault-rag）](https://qiita.com/Tadashi_Kudo/items/c08770401408e7e1c54b)
- [Claude Code で「第二の脳」を作る](https://qiita.com/Tadashi_Kudo/items/c35c0aaed00878d88b05)
- [Claude Code の Agent Team でコーディングを並列化した](https://qiita.com/Tadashi_Kudo/items/96422f7c2048a7236be8)
