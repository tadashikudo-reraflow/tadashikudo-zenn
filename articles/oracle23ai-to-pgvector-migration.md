---
title: "Oracle 23ai→PostgreSQL+pgvector 移行記：embedding刷新でRAG精度51%改善"
emoji: "🔄"
type: "tech"
topics: ["PostgreSQL", "oracle", "rag", "pgvector", "ベクトルDB"]
published: true
---

> 本番RAGシステムをOracle 23ai FreからPostgreSQL+pgvectorに移行した実録。DBマイグレーションとembeddingモデル刷新を同時に行い、検索精度を大幅改善した。

---

## 目次

1. #1. 移行前の構成
2. #2. Oracle 23ai Free が壊れた経緯
3. #3. 移行を決めた理由（Oracle廃止の判断）
4. #4. embeddingモデル選定
5. #5. 移行手順
6. #6. 実測コスト
7. #7. 精度比較（移行前後）
8. #8. はまりポイント
9. #9. 移行後の構成

---

## 1. 移行前の構成

| 項目 | 移行前 | 移行後 |
|------|--------|--------|
| DB | Oracle 23ai Free | PostgreSQL 16 + pgvector |
| embeddingモデル | multilingual-e5-small | Gemini text-embedding-004 |
| 次元数 | 384次元 | 3,072次元 |
| 実行環境 | ローカル（CPU） | Google API |
| 総チャンク数 | 167,173件 | 167,173件（再embedding） |

---

## 2. Oracle 23ai Free が壊れた経緯

移行の直接的なきっかけは、Oracle 23ai Free が段階的に機能不全に陥ったことだ。

### 障害の連鎖

#### フェーズ1：12GB制限への抵触

Oracle 23ai Free には**PDBサイズ上限12GB**という制約がある。毎日データを追加し続けていたところ、ある時点で `v$pdbs.total_size` が制限を超え、PDB（Pluggable Database）が起動不能になった。

```
ORA-65114: space usage in container is too high
→ PDB が RESTRICTED モードでしか起動しない
```

対処として不要データを削除してサイズを12GB以下に戻したが、これは根本解決ではない。データが増え続ける限り再発は確実だった。

#### フェーズ2：HNSWインデックス作成不可

RAG検索の高速化のためHNSWインデックスを作成しようとしたところ、INMEMORY領域不足でエラー。

```sql
CREATE VECTOR INDEX idx_embedding ON documents(embedding)
ORGANIZATION INMEMORY NEIGHBOR GRAPH ...;

-- ORA-51962: insufficient memory for vector index creation
```

Oracle 23ai Free はメモリ使用量にも制限があり、167,173件 × 3,072次元のインデックスを構築するには足りなかった。

#### フェーズ3：undoテーブルスペースの破損

サイズ削減のためデータファイルを手動でtruncateしたところ、undo tablespaceが読み取り不能になった。

```
ORA-01157: cannot identify/lock data file
ORA-01110: data file 'undotbs01.dbf'
```

通常手順での復旧が不可能になり、隠しパラメータを使った強制起動に頼る状態になった。

```
# pfileに追加して強制起動
_corrupted_rollback_segments=(_SYSSMU1_..., _SYSSMU2_...)
```

この時点で「Oracle 23ai Freeを本番RAGに使い続けること」は現実的でないと判断した。

### 障害のまとめ

| 障害 | 原因 | 一時対処 | 根本解決 |
|------|------|---------|---------|
| PDB起動不能 | 12GB制限超過 | データ削除 | ❌（再発確実） |
| HNSWインデックス不可 | INMEMORY不足 | なし | ❌ |
| undoテーブルスペース破損 | datafile手動truncate | 隠しパラメータ起動 | ❌ |

---

## 3. 移行を決めた理由（Oracle廃止の判断）

### Oracle 23ai の技術的優位性

正直に言うと、**Oracle 23aiの技術仕様はRAG用途として優秀**だ。

- **次元数制限なし**: pgvectorの2,000次元制限が存在しない。3,072次元でも4,096次元でもそのままインデックスを張れる
- **ネイティブなハイブリッド検索**: Oracle Text（BM25）とベクトル検索をSQL一本で統合できる。pgvectorではアプリ層で自前実装が必要
- **セキュリティ**: 行レベルセキュリティ・監査ログ・暗号化が標準装備。機密文書RAGには理想的

技術的には、Oracle 23aiはRAGの観点でpgvectorより上位の機能を持っている。

### そもそもなぜ Oracle 23ai を採用したか

技術的優位性を評価して採用した。RAGシステムにおいてハイブリッド検索・高次元ベクトル・セキュリティをSQL一本で扱えるDBは他にない。個人開発であっても、最初から高精度な基盤を作りたかったためOracle 23aiを選んだ。

### 唯一の問題：Free エディションの12GB制限

移行の理由はただ一つ、**Oracle 23ai Freeの12GBサイズ制限**だ。

毎日データをクロールして追加し続けるシステムでは、個人開発規模であっても数ヶ月で12GBに到達する。実際に抵触し、PDB起動不能・HNSWインデックス作成不可・undo破損と障害が連鎖した（詳細は前セクション参照）。

商用ライセンス（Oracle Database Standard/Enterprise）があればこの制限は存在しない。個人開発でそのコストを出すのは現実的ではなく、やむなく移行を決断した。技術的には今でもOracle 23aiのほうが優れていると思っている。

### なぜPostgreSQLを選んだか

制限のないフリーなDBとしてPostgreSQL+pgvectorを選んだ。技術的には一歩後退だが、今回のシステムが扱うデータは**公開情報のみ**であり、Oracle 23aiが強みとする行レベルセキュリティや監査ログは不要だった。機密文書を扱うRAGであればOracleのセキュリティ機能は大きな選定理由になるが、今回はその要件がないため、PostgreSQLで十分と判断した。

### pgvectorの2,000次元制限との付き合い方

pgvectorには**インデックスを張れる上限が2,000次元**という制約がある。Gemini text-embedding-004は3,072次元のため、このままではインデックスが作れない。

```
float32（4バイト） × 2,000次元 = 8,000バイト → PostgreSQLの1ページ（8KB）がほぼ満杯
→ これ以上は物理的にインデックスを張れない
```

対処法として **`halfvec`型**（float16）を使うことで4,000次元まで対応可能。

```sql
-- VECTOR型ではなくhalfvec型を使う
embedding halfvec(3072)
```

この制約を許容できると判断したため、PostgreSQLへの移行を決定した。

---

## 4. embeddingモデル選定

DB移行と同時にembeddingモデルも刷新した。どうせ全件再embeddingが必要なら、一度にやったほうがコストが低い。

### モデル比較

| モデル | 提供元 | 次元数 | コスト/1Mトークン | 日本語精度 |
|--------|--------|--------|-----------------|-----------|
| multilingual-e5-small | OSS（Microsoft/intfloat） | 384 | 無料（ローカル） | △ |
| multilingual-e5-large | OSS（Microsoft/intfloat） | 1,024 | 無料（GPU推奨） | ○ |
| text-embedding-ada-002 | OpenAI | 1,536 | $0.10 | ○ |
| **Gemini text-embedding-004** | **Google** | **3,072** | **$0.025** | **◎** |
| text-embedding-3-large | OpenAI | 3,072 | $0.13 | ◎ |

Geminiを選んだ理由：
- 同じ3,072次元でOpenAIの**1/5のコスト**
- `task_type`（文書用/クエリ用）を分けられる設計がRAGに最適
- 日本語・英語混合テキストの精度が最上位クラス

---

## 5. 移行手順

### Step 1：PostgreSQL+pgvectorのセットアップ

```bash
docker run -d \
  --name rag-postgres \
  -p 5433:5432 \
  -e POSTGRES_USER=<your-user> \
  -e POSTGRES_PASSWORD=<your-password> \
  -e POSTGRES_DB=ragdb \
  -v pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16
```

```sql
-- pgvector拡張を有効化
CREATE EXTENSION IF NOT EXISTS vector;

-- テーブル定義（halfvecで3072次元に対応）
CREATE TABLE documents (
    id          BIGSERIAL PRIMARY KEY,
    chunk_text  TEXT,
    source_file TEXT,
    page_num    INTEGER,
    embedding   halfvec(3072)
);
```

### Step 2：Gemini embeddingの生成

```python
import google.generativeai as genai
import os

genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def get_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    result = genai.embed_content(
        model="models/text-embedding-004",
        content=text,
        task_type=task_type
    )
    return result["embedding"]
```

> **重要**: INSERTは `RETRIEVAL_DOCUMENT`、クエリ時は `RETRIEVAL_QUERY` を使う。混同すると精度が落ちる。

### Step 3：バッチ移行スクリプト

Oracle側からデータを取り出し、Gemini embeddingを生成してPostgreSQLへ投入する。

```python
import oracledb
import psycopg2
import google.generativeai as genai
import time
import os
from tqdm import tqdm

BATCH_SIZE = 50
RATE_LIMIT_WAIT = 60 / 90  # 100req/分制限に余裕を持たせる

def migrate():
    src = oracledb.connect(user=os.environ["ORA_USER"],
                           password=os.environ["ORA_PASSWORD"],
                           dsn=os.environ["ORA_DSN"])
    dst = psycopg2.connect(os.environ["PG_DSN"])

    with src.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM documents")
        total = cur.fetchone()[0]

    offset = 0
    with tqdm(total=total) as pbar:
        while offset < total:
            with src.cursor() as cur:
                cur.execute("""
                    SELECT chunk_text, source_file, page_num FROM documents
                    ORDER BY id OFFSET :1 ROWS FETCH NEXT :2 ROWS ONLY
                """, [offset, BATCH_SIZE])
                rows = cur.fetchall()

            if not rows:
                break

            records = []
            for chunk_text, source_file, page_num in rows:
                embedding = get_embedding(chunk_text)
                records.append((chunk_text, source_file, page_num, embedding))
                time.sleep(RATE_LIMIT_WAIT)

            with dst.cursor() as cur:
                cur.executemany("""
                    INSERT INTO documents (chunk_text, source_file, page_num, embedding)
                    VALUES (%s, %s, %s, %s::halfvec)
                """, records)
            dst.commit()

            offset += BATCH_SIZE
            pbar.update(len(rows))

    src.close()
    dst.close()

if __name__ == "__main__":
    migrate()
```

### Step 4：インデックス作成

```sql
-- HNSWインデックス（推奨）
CREATE INDEX ON documents
USING hnsw (embedding halfvec_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

### Step 5：類似検索

```python
def similarity_search(conn, query: str, top_k: int = 10) -> list[dict]:
    query_vec = get_embedding(query, task_type="RETRIEVAL_QUERY")

    with conn.cursor() as cur:
        cur.execute("""
            SELECT chunk_text, source_file, page_num,
                   1 - (embedding <=> %s::halfvec) AS score
            FROM documents
            ORDER BY embedding <=> %s::halfvec
            LIMIT %s
        """, [query_vec, query_vec, top_k])
        rows = cur.fetchall()

    return [
        {"text": r[0], "source": r[1], "page": r[2], "score": r[3]}
        for r in rows
    ]
```

---

## 6. 実測コスト

### 移行時の費用

| 項目 | 値 |
|------|-----|
| 総チャンク数 | 167,173件 |
| 総トークン数（推定） | 約2,500万トークン |
| Gemini APIコスト | 約$0.63（¥95相当） |
| 所要時間 | 約3時間（レートリミット込み） |

### 月次運用コスト比較

| 項目 | 移行前（E5-small） | 移行後（Gemini） |
|------|-----------------|----------------|
| embeddingコスト | 無料（ローカル） | ~$0.05〜$0.10/月 |
| DB費用 | Oracle Free（制限あり） | PostgreSQL（無料） |
| GPU不要 | ✅ | ✅ |

---

## 7. 精度比較（移行前後）

手動評価50クエリでのTop-1関連度スコア平均。

| クエリ例 | E5-small | Gemini |
|---------|---------|--------|
| 技術仕様の要件確認 | 0.61 | 0.89 |
| 日付・期限の照会 | 0.55 | 0.91 |
| 複数概念の組み合わせ検索 | 0.58 | 0.87 |
| 専門用語の意味検索 | 0.63 | 0.88 |
| **平均** | **0.59** | **0.89** |

**+51%の精度改善**。特に日本語専門用語・複合キーワードでの差が顕著だった。

---

## 8. はまりポイント

### halfvec vs vector の選択ミス

```sql
-- ❌ 3072次元にvector型を使うとインデックスが作れない
embedding vector(3072)

-- ✅ halfvec型を使う
embedding halfvec(3072)
```

pgvectorの `vector` 型は2,000次元超のインデックスを作れない。`halfvec`（float16）を使えば4,000次元まで対応可能。精度劣化は実用上ほぼ無視できる。

### task_typeの混同

```python
# ❌ クエリにもRETRIEVAL_DOCUMENTを使う
query_vec = get_embedding(query, task_type="RETRIEVAL_DOCUMENT")

# ✅ クエリはRETRIEVAL_QUERYを使う
query_vec = get_embedding(query, task_type="RETRIEVAL_QUERY")
```

INSERTとクエリで `task_type` を分けることで精度が向上する。Gemini特有の設計。

### レートリミットのバースト

100req/分の制限に対して `sleep(0.6)` で詰めすぎると、GCP側のバーストで429エラーが出ることがある。`sleep(60/90)` で10%の余裕を持たせる。

### 移行中の一貫性

移行スクリプト実行中は旧DB（Oracle）を読み取り専用にして新規書き込みを止めること。並行稼働しているとembeddingが混在する可能性がある。

---

## 9. 移行後の構成

```
PDF / テキスト
    ↓ チャンキング
Gemini text-embedding-004（RETRIEVAL_DOCUMENT）
    ↓ 3,072次元ベクトル
PostgreSQL + pgvector（halfvec型・HNSWインデックス）
    ↑ クエリ
Gemini text-embedding-004（RETRIEVAL_QUERY）
    ↑ 自然言語クエリ
```

pgvectorの2,000次元制限はhalf vecで回避。Oracle 23aiほどの高次元対応ではないが、3,072次元で実用上十分な精度を確保できた。

---

## まとめ

- Oracle 23ai はRAG用途として技術的に優秀。ハイブリッド検索・高次元対応・セキュリティはpgvectorより上位
- しかし**Free エディションの12GB制限**は個人開発規模でも現実的に厳しく、毎日データを追加するシステムでは数ヶ月で抵触する
- 商用ライセンスを買えるなら Oracle 23ai のまま運用するのが技術的には正解
- やむなく PostgreSQL+pgvector に移行。pgvectorの2,000次元制限は `halfvec` 型で回避できる
- DB移行のタイミングでembeddingモデルも刷新（384次元→3,072次元）。どうせ全件再embeddingが必要なら同時にやったほうがコストが低い
- 結果として精度+51%改善、追加コストは月$0.10以下

---

## 関連ノート

- oracle23ai-vs-postgresql-rag-comparison — pgvector/Snowflake等との11製品比較
