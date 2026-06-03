---
title: "ベクトルDB 完全比較：RAG基盤の技術選定ガイド（11製品）"
emoji: "📊"
type: "tech"
topics: ["PostgreSQL", "oracle", "rag", "embedding", "pgvector"]
published: true
---

# ベクトルDB 完全比較：RAG基盤の技術選定ガイド（11製品）

> 実測データ（167,173チャンク / 11,871 PDF / 3,072次元Gemini embedding）を基に比較。
> Oracle 23ai / pgvector / Snowflake / BigQuery / Databricks / Pinecone / Qdrant / Weaviate / Milvus / Redis / Elasticsearch

---

## 目次

1. 1. ベクトルDBとは何か（初学者向け）
2. 2. 次元数という壁：なぜこれがRAG精度を決めるか
3. 3. モデル移行のリアルコスト：高次元化したとき何が起きるか
4. 4. ハイブリッド検索：「SQL一発」vs「アプリ層マージ」の差
5. 5. セキュリティ統合：機密文書RAGの選定基準
6. 6. RAGパイプライン完結度の比較
7. 7. 11製品スペック横断比較
8. 8. コスト：Embedding API × ベクトルDB 課金比較
9. 9. DB選定フローチャート
10. 10. ユースケース別最終選定マトリクス
11. 11. 付録：セキュリティ詳細（Oracle vs pgvector）

---

## 1. ベクトルDBとは何か（初学者向け）

### ベクトルとは

文章・画像・音声を「数値の配列」に変換したもの。embeddingモデルが変換を担う。

```
「今日は晴れ」→ [0.12, -0.34, 0.89, 0.21 ... × 1024個]
「今日は曇り」→ [0.11, -0.33, 0.87, 0.20 ... × 1024個]  ← 近い
「量子コンピュータ」→ [0.91, 0.67, -0.12, 0.55 ... × 1024個]  ← 遠い
```

ベクトルDBはこの「数値の近さ（距離）」で検索するデータベース。キーワード完全一致ではなく**意味の近さ**で検索できるのがRAGの核心。

### 距離の種類

| 距離 | 意味 | 使いどころ |
|------|------|-----------|
| **コサイン距離** | ベクトルの「向き」の近さ（大きさは無視） | RAGで最もよく使われる。文章の意味比較に最適 |
| **L2距離（ユークリッド）** | 2点間の直線距離 | 画像・音声など大きさも意味を持つ場合 |
| **内積（dot product）** | 向きと大きさ両方を考慮 | 正規化済みベクトルではコサインと同等 |

### 次元数とは

ベクトルの「数値の個数」。次元数 = 意味空間の解像度。

```
384次元  = SD画質（大まかな意味の近さはわかる）
1,024次元 = HD画質（専門用語・多言語もある程度区別できる）
3,072次元 = 4K画質（微妙なニュアンスの違いも識別できる）
```

---

## 2. 次元数という壁：なぜこれがRAG精度を決めるか

### pgvectorの次元制限：2,000の壁と4,000の天井

pgvectorには**HNSWインデックスを張れる次元数の上限**がある。

```
text-embedding-3-large（OpenAI最高精度）→ 3,072次元
Gemini text-embedding-004              → 3,072次元  ← PJ19で使用中
NVIDIA NV-Embed-v2                     → 4,096次元
Cohere Embed v4                        → 4,096次元

                ↓ pgvectorにHNSWインデックスを張ろうとすると

⚠️ 2,000次元超（float32）: インデックス不可 → halfvecキャストで回避可能
❌ 4,001次元超（halfvec）: インデックス不可・回避策なし
```

> **重要**: 制限があるのは**インデックス構築時のみ**。カラム定義（`vector`型）は次元数フリーで任意の次元を格納できる。

#### なぜ2,000次元／4,000次元が壁なのか

PostgreSQLのアーキテクチャに起因する**物理的な制約**。設計上の選択ではなくOSの限界に近い問題。

```
PostgreSQLの1ページ = 8KB（8,192バイト）固定

float32（4バイト） × 2,000次元 = 8,000バイト → ほぼ満杯 → インデックス不可
float16（2バイト） × 4,000次元 = 8,000バイト → halfvecでギリギリ

4,096次元（Cohere/NVIDIA）はfloat16でも 2バイト × 4,096 = 8,192バイト → 超過
```

pgvectorは後付け拡張のためPostgreSQLの8KB制約をそのまま引き継ぐ。
Oracleは23aiでベクトル専用ストレージエンジンをゼロから設計し、この制約が存在しない。

**この壁はpgvectorのバージョンを上げても解決しない。PostgreSQLのページサイズ設計そのものだから。**

#### 3,072次元の回避方法（実装）

カラム定義は `vector`（float32）のまま変更せず、**インデックス構築時だけ `::halfvec` キャストする**：

```sql
-- カラムはvector型のまま（float32で保存・精度ロスなし）
CREATE INDEX idx_chunks_embedding
ON chunks USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);

-- 検索クエリもキャスト
SELECT * FROM chunks ORDER BY embedding::halfvec(3072) <=> $1::halfvec(3072) LIMIT 10;
```

この方法の特徴：
- ストレージはfloat32フル精度で保存（Oracleと同等）
- インデックス検索はfloat16精度（Oracleより劣る）
- recall（再現率）は99%以上維持（pgvector公式）

#### 4,096次元では完全に詰む

Cohere Embed v4・NVIDIA NV-Embed-v2（4,096次元）をpgvectorで使う場合：

| 方法 | 実態 |
|------|------|
| **halfvecインデックス** | ❌ 4,096次元はhalfvecの上限（4,000次元）超え |
| **bit型（binary量子化）** | ⚠️ 64,000次元まで対応だが精度劣化が大きい |
| **次元削減（4,000以下に）** | ⚠️ Matryoshka対応モデルのみ・情報ロスあり |
| **インデックスなし（全件スキャン）** | ⚠️ 10万件超では検索が数秒〜数十秒に |

**実質的に「4,096次元モデルを本格運用するならpgvectorは詰む」が現状。**

#### 回避策とそのデメリット（3,072次元以下向け）

| 回避策 | やること | デメリット |
|--------|---------|-----------|
| **halfvecキャスト（インデックスのみ）** | インデックス構築・検索クエリにキャスト追加 | インデックス精度がfloat16に落ちる（ストレージはfloat32維持） |
| **Matryoshka次元削減** | 3,072→2,000次元に切り詰める | 情報の35%を捨てる。OpenAI 3-large系のみ対応 |
| **PCA次元削減** | 統計的に圧縮する | 情報ロスあり。学習データが必要 |
| **別モデルを使う** | 2,000次元以下のモデルを選ぶ | 精度が上限を超えられない |

### 主要モデルとpgvectorの相性

| モデル | 次元数 | pgvector | Oracle | 備考 |
|--------|--------|----------|--------|------|
| MiniLM-L6 | 384 | ✅ | ✅ | 軽量・低精度 |
| bge-m3 | 1,024 | ✅ | ✅ | 多言語・バランス型。PJ15で使用 |
| text-embedding-ada-002 | 1,536 | ✅ | ✅ | OpenAI旧世代 |
| **text-embedding-3-large** | **3,072** | ⚠️ halfvecキャスト必須 | ✅ | OpenAI現行最高精度 |
| **Gemini text-embedding-004** | **3,072** | ⚠️ halfvecキャスト必須 | ✅ | PJ19で使用中 |
| **NVIDIA NV-Embed-v2** | **4,096** | ❌ インデックス不可 | ✅ | MTEB最上位クラス |
| **Cohere Embed v4** | **4,096** | ❌ インデックス不可 | ✅ | マルチモーダル対応 |

**トレンド**: 最新の高性能モデルは3,072〜4,096次元が主流。pgvectorは3,072次元まではhalf vecキャストで対応できるが、4,096次元（Cohere/NVIDIA）には実質的に対応不可。モデル選定の自由度でOracleが優位。

---

## 3. モデル移行のリアルコスト：高次元化したとき何が起きるか

### 全件再ベクトル化は全DBで必ず必要

embeddingモデルを変えると**既存の全データを作り直さなければならない**。これはどのDBでも避けられない。

```
旧モデル（1,024次元）のベクトル ≠ 新モデル（3,072次元）のベクトル

モデルが違えば意味空間が全く異なる。
混在させると「近い」「遠い」の判定がめちゃくちゃになる。
→ 全件を新モデルで再計算必須（全DB共通）
```

### PJ19（167,173チャンク）での再ベクトル化コスト試算

| モデル | 単価 | 再ベクトル化コスト | 次元数 |
|--------|------|----------------|--------|
| OpenAI 3-small | $0.02/MTok | **~$1** | 1,536 |
| Gemini embedding-004 | $0.15/MTok | **~$7.5** | 3,072 |
| Cohere Embed v4 | $0.12/MTok | **~$6** | 4,096 |

**API課金自体は大した額ではない。** 問題は作業コストとダウンタイム。

### pgvector と Oracle の移行作業の差

pgvectorで **1,024次元 → 3,072次元** に移行する場合：

```
Step 1: 全件再ベクトル化（新モデルで再計算してDBに書き込む）
        ← ここは Oracle も同じ

Step 2: インデックス再構築（halfvecキャスト付き）★pgvectorのみ
  CREATE INDEX ON chunks USING hnsw
    ((embedding::halfvec(3072)) halfvec_cosine_ops);
  → カラム定義の変更は不要（vector型のまま）
  → ただしインデックスはfloat16精度に落ちる

Step 3: インデックス再構築
        ← ここは Oracle も同じ

Step 4: アプリのクエリコードを全箇所修正 ★pgvectorのみ
  -- 旧: ORDER BY embedding <=> $1
  -- 新: ORDER BY embedding::halfvec(3072) <=> $1::halfvec(3072)
  → クエリを書いている全箇所を変更・テスト

Step 5: 新旧ベクトルの混在期間の管理 ★pgvectorのみ
  → 全件置き換えが終わるまで検索精度が不安定
```

Oracleでの移行：

```
Step 1: 全件再ベクトル化（新モデルで再計算してDBに書き込む）
        ← pgvectorと同じ

Step 2: インデックス再構築
        ← pgvectorと同じ

以上。スキーマ変更なし・コード修正なし・ロックなし。
```

**なぜOracleはスキーマ変更不要か**：`VECTOR`型を次元数フリーで定義できるため。

```sql
-- 固定次元（pgvectorと同じ書き方）
embedding VECTOR(1024, FLOAT32)

-- 可変次元（Oracleだけの書き方）
embedding VECTOR  ← 次元数を指定しない。どの次元数でも書き込める
```

可変次元で定義しておけば1,024次元のデータも3,072次元のデータも同じカラムに入る。

### 規模別の移行コスト差

| データ規模 | pgvectorの追加作業 | Oracleの追加作業 |
|-----------|-----------------|----------------|
| 10万件 | スキーマ変更30分+コード修正+テスト | インデックス再構築のみ |
| 100万件 | スキーマ変更数時間+ダウンタイム設計 | インデックス再構築のみ |
| 1,000万件 | **大規模マイグレーション計画が必要**（Blue/Green deploy等） | インデックス再構築のみ |

年に1〜2回モデルを更新するなら、この差は累積で大きくなる。

---

## 4. ハイブリッド検索：「SQL一発」vs「アプリ層マージ」の差

ハイブリッド検索 = 全文検索（キーワード一致）+ ベクトル検索（意味の近さ）を組み合わせる。純粋なベクトル検索より精度が高いことが多い。

### pgvector（アプリ層マージ）の構造

```
ユーザーの質問
    │
    ├─① BM25検索クエリ → PostgreSQL → 結果A（順位付き10件）
    └─② ベクトル検索クエリ → PostgreSQL → 結果B（順位付き10件）

    → Pythonで A+B をRRF等でマージ → 最終順位決定
```

### Oracle 23ai（SQL一発）の構造

```
ユーザーの質問
    └─ 1つのSQL → Oracle内部で全文スコア×ベクトル距離を同時計算 → 最終順位
```

### 「SQL一発」の何が違うか

**① データの一貫性**：pgvectorは①と②の間に時間差がある。その間にデータが更新されると新旧混在の結果になる可能性がある。Oracleは1トランザクションなので起きない。

**② スコアの正規化問題**：BM25スコア「0.8」とコサイン類似度「0.8」は別物。アプリ層マージには正規化が必要で、この実装次第で精度がブレる。

```python
# pgvector: この2つをどう合算するか自分で実装が必要
bm25_results = db.query("SELECT ..., ts_rank(...) as score")
vec_results  = db.query("SELECT ..., 1-cosine_distance(...) as score")
# RRF? Linear Combination? Cross-Encoder? → チューニングが必要
```

Oracleは内部で統合されているためこのチューニング工数がゼロ。

**③ 複合フィルタの適用**：

```sql
-- Oracle: セマンティック検索 + ビジネスルール + アクセス制御を1クエリで
SELECT chunk_text
FROM chunks c JOIN documents d ON c.doc_id = d.id
WHERE d.region = 'APAC'
  AND d.valid_until > SYSDATE        -- 有効期限切れ除外
  AND d.classification <= :user_level -- 機密レベルフィルタ
ORDER BY VECTOR_DISTANCE(embedding, :qvec, COSINE)
FETCH FIRST 5 ROWS ONLY;
```

pgvectorでも WHERE 句は書けるが、VPD/OLSと組み合わせた**暗黙フィルタ**（WHERE句を書かなくても自動で適用される）はOracleだけの機能。

---

## 5. セキュリティ統合：機密文書RAGの選定基準

### アクセス制御の比較

| 機能 | Oracle 23ai | pgvector | Snowflake | BigQuery | Databricks |
|------|------------|----------|-----------|----------|------------|
| **行レベルセキュリティ(RLS)** | ✅ VPD自動適用 | ✅ RLS自動適用 | **⚠️ Owner権限問題** | ✅ 自動適用 | **❌ RLSありはインデックス作成不可** |
| **階層型ラベルセキュリティ** | ✅ OLS | ❌ | ❌ | ❌ | ❌ |
| **ベクトル列の暗号化** | ✅ TDE（演算に影響なし） | ❌ 暗号化すると検索不可 | AES-256 | AES-256 | AES-256 |
| **細粒度監査（FGA）** | ✅ 列・条件単位で記録 | ❌ | △ | △ | △ |

### AES-256とは

**業界標準の保存時暗号化方式**。256ビット鍵長で現実的な時間では解読不可能。DWH各社の「AES-256対応」= ストレージに書く前に自動で暗号化する、という意味。

Oracleの TDE（Transparent Data Encryption）との違い：
- 一般的なAES-256: ストレージ全体を暗号化
- Oracle TDE: ストレージレベルで暗号化するため、**DB内のベクトル演算には影響しない**
- pgcrypto: 列単位で暗号化するため、ベクトル列を暗号化すると類似検索が不可能になる

### DWH3社のセキュリティ制約（致命的）

これはアーキテクチャの必然。偶然ではない。

| 製品 | 問題 | 根本原因 |
|------|------|---------|
| **Snowflake** | Cortex SearchがOwner権限で動作 → 動的マスキングが実質無効化 | サービス実行権限の設計 |
| **Databricks** | RLS付きテーブルはベクトルインデックス作成不可（※） | DeltaLakeとVector Searchの独立設計 |
| **BigQuery** | RLS自動適用されるがstored_columnsが使えずJOINフォールバック | ストレージとインデックスの分離 |

> **※ RLS（Row Level Security）とは**
> データベースの「行単位アクセス制御」。例えば「一般社員はチャンクIDが自分の部署のものしか読めない」というルールをテーブルに設定すると、SQLで全件SELECT しても自動的に自分に見える行だけが返ってくる仕組み。
> DatabricksではRLSを設定したテーブルにベクトルインデックスを追加しようとするとエラーになる。つまり「セキュリティ制御 **か** 高速ベクトル検索 **か** どちらか一方しか選べない」状態になる。

**なぜDWH系はこうなるか**：DWHはバッチ分析に最適化された設計で、RLSは行単位フィルタ。ベクトルインデックスは事前計算された空間構造のため、RLS適用=インデックスの動的フィルタリングが必要になりコスト爆発する。Oracleは30年のRDBMS経験でVPD（1999年〜）とインデックスの共存を設計済み。

### 機密文書RAG選定フローチャート

```
Q1. 文書に機密レベルの区分があるか？
  └─ NO → pgvector で十分（コスト優先）
  └─ YES
      Q2. 機密レベルは2段階（公開/非公開）で済むか？
        └─ YES → pgvector RLS で対応可能
        └─ NO（3段階以上、部署×役職の組合せ等）
            Q3. 監査証跡の法的要件があるか？
              └─ NO → pgvector RLS + アプリ層で構築可能（工数は増える）
              └─ YES → Oracle 23ai 推奨（OLS + VPD + FGA + TDE 統合）
```

---

## 6. RAGパイプライン完結度の比較

```
Oracle 23ai      ████████████████  チャンキング+Embedding+LLM推論+ハイブリッド全てSQL完結
Snowflake Cortex ████████████░░░░  チャンキング+Embedding+LLM対応。RLSリスクあり
BigQuery         ████████░░░░░░░░  Embedding+LLM(AI.EMBED/AI.SEARCH)。チャンキングなし
Databricks       ███████░░░░░░░░░  ManagedEmbedding+LLM。チャンキングなし、RLS不可
Milvus           ████░░░░░░░░░░░░  BM25内蔵。Embeddingは外部モデル必要
Elasticsearch    ████░░░░░░░░░░░░  ELSER内蔵。チャンキングなし
pgvector         ██░░░░░░░░░░░░░░  全て外部(Python)必要
Pinecone/Qdrant  ██░░░░░░░░░░░░░░  ベクトル格納のみ。パイプラインは全て外部
```

### Oracle DB内RAGパイプラインの実例

```sql
-- PDF取り込みから検索まで全てSQL完結
SELECT dt.chunk_text, VECTOR_DISTANCE(dt.embedding, :qvec, COSINE) AS dist
FROM TABLE(
  DBMS_VECTOR_CHAIN.UTL_TO_CHUNKS(
    DBMS_VECTOR_CHAIN.UTL_TO_TEXT(:pdf_blob),  -- PDF→テキスト
    JSON('{"split":"sentence","max":"200"}')    -- チャンク分割
  )
) dt
WHERE VECTOR_DISTANCE(dt.embedding, :qvec, COSINE) < 0.3
ORDER BY dist
FETCH FIRST 5 ROWS ONLY;
```

**注意**：DB内蔵のembeddingモデルは汎用英語モデル。日本語RAGでは外部API（Gemini/OpenAI）推奨。

---

## 7. 11製品スペック横断比較

### 次元数・量子化・インデックス

| 製品                    | 最大次元数                 | float32（デフォルト） | float16     | int8     | binary        | インデックス                            |
| --------------------- | --------------------- | -------------- | ----------- | -------- | ------------- | --------------------------------- |
| **Oracle 23ai/26ai**  | **65,535**            | ✅              | ✅(26ai)     | ✅        | ✅             | HNSW / IVF                        |
| **Milvus v2.5**       | 32,768                | ✅              | ✅(bfloat16) | ✅        | ✅             | HNSW / IVF系 / DiskANN / GPU-CAGRA |
| **Redis 8**           | 32,768                | ✅              | ✅           | ✅(uint8) | △             | HNSW / FLAT / SVS-VAMANA          |
| **Qdrant**            | 制限なし※                 | ✅              | △           | ✅(SQ8)   | ✅(BQ)         | HNSW / FLAT                       |
| **Weaviate**          | 制限なし※                 | ✅              | ❌           | △(SQ)    | ✅(BQ)         | HNSW / Flat / Dynamic             |
| **BigQuery**          | 未公表                   | ✅              | ❌           | ❌        | △(TreeAH内部PQ) | IVF / TreeAH                      |
| **Pinecone**          | 20,000                | ✅              | ❌           | △(内部自動)  | ❌             | 独自(HNSWベース)                       |
| **Elasticsearch**     | 4,096                 | ✅              | ❌           | △(SQ)    | ✅(BBQ)        | HNSW(Lucene)                      |
| **Snowflake Cortex**  | 4,096                 | ✅              | ❌           | ❌        | ❌             | サービス抽象化                           |
| **Databricks Mosaic** | 4,096                 | ✅              | ❌           | ❌        | △(PQ内部)       | HNSW                              |
| **pgvector 0.8**      | 2,000(halfvec: 4,000) | ✅              | ✅(halfvec)  | ✅        | ✅             | HNSW / IVFFlat                    |

※ Qdrant/Weaviateの「制限なし」はメモリ依存。実用上は数千〜数万次元がターゲット。

**次元数の序列**：Oracle(65,535) >> Milvus/Redis(32,768) > Qdrant/Weaviate(制限なし※) > BigQuery(未公表) > Pinecone(20,000) > Elasticsearch/Snowflake/Databricks(4,096) > pgvector(2,000)

### float16 / int8 / binary（量子化）とは

ベクトルの各次元の数値をどれだけ細かく表現するかの違い。精度を落とす代わりにメモリを節約する技術を**量子化**と呼ぶ。

```
float32（標準）:   1次元 = 4バイト    例: 0.71234567     精度劣化 0%
float16（半精度）:  1次元 = 2バイト    例: 0.7124         精度劣化 <0.3%（実用上無視）
int8:              1次元 = 1バイト    例: 91（整数に変換） 精度劣化 ~1.5%
binary:            1次元 = 1/8バイト  例: 1（正か負だけ）  精度劣化 大（モデル依存）
```

**メモリ削減率**：
- **float16**: float32の半分（2倍のベクトルを同じメモリに格納できる）
- **int8**: float32の4分の1（75%削減）
- **binary**: float32の32分の1（97%削減）

**各型の使いどころ**：

| 型 | 使いどころ | 注意点 |
|----|-----------|--------|
| **float16** | pgvectorで3,072次元超を使いたい場合（`halfvec`型） | 型変換・コード修正が必要 |
| **int8（SQ8）** | 本番運用でのコスト削減。Qdrant・Milvus・Oracleが対応 | 精度1.5%劣化。事前検証推奨 |
| **binary（BBQ/BQ）** | 大規模スケール（億件超）での検索高速化。Elasticsearch・Qdrant対応 | 精度劣化が大きい。1,024次元以上のモデルで効果が出やすい |

### ハイブリッド検索・スパースベクトル対応

**ハイブリッド検索とは**：キーワード検索（全文一致）とベクトル検索（意味の近さ）を組み合わせる検索方式。どちらか単体より精度が高いことが多い。

```
キーワード検索（BM25）: 「Oracle」という単語が含まれる文書を探す
ベクトル検索:          「データベースの選び方」に意味が近い文書を探す
ハイブリッド:          両方のスコアを統合して最終ランキングを決める
```

**スパースベクトルとは**：ほとんどの次元が0で、一部だけ値を持つベクトル。BM25やSPLADE等のキーワード重み付けモデルが出力する。「どの単語が重要か」を数値化したもの。通常のembedding（密ベクトル）とは別物。

```
密ベクトル（Dense）:  [0.12, -0.34, 0.89, 0.21, ...]  全次元に値がある
スパースベクトル:     [0, 0, 0, 0.95, 0, 0, 0, 0.72, 0, ...]  ほぼ0、一部だけ値
```

**統合方式の種類**：
- **RRF（Reciprocal Rank Fusion）**: キーワード検索の順位とベクトル検索の順位を数式で統合。シンプルで安定
- **alpha パラメータ**: `alpha=0` で純キーワード、`alpha=1` で純ベクトル、`0.5` で中間
- **SQL統合（Oracle）**: DB内部でスコアを計算。一貫性が高くチューニング工数が最小

| 製品 | ハイブリッド検索 | スパースベクトル | 統合方式 |
| -------------------- | ------- | ------------ | --------------------------------- |
| **Milvus v2.5**      | ✅ ネイティブ | ✅ ネイティブ      | Sparse-BM25内蔵                     |
| **Qdrant**           | ✅ ネイティブ | ✅ ネイティブ      | RRF標準対応                           |
| **Elasticsearch**    | ✅ 最成熟   | ✅ ELSER      | BM25+kNN(RRF)。業界最長歴史              |
| **Weaviate**         | ✅       | △ BM25のみ     | alphaパラメータで統合                     |
| **Pinecone**         | ✅       | ✅ SPLADE対応   | alphaパラメータ                        |
| **Redis 8.4**        | ✅       | △ BM25のみ     | FT.HYBRIDコマンド（サブミリ秒）              |
| **Snowflake**        | ✅       | ❌            | Cortex Searchが内部で自動統合             |
| **Oracle 23ai/26ai** | ✅       | ✅(26ai〜)     | Oracle Text+Vector。26aiでスパースネイティブ |
| **BigQuery**         | △ 手動SQL | ❌            | AI.Search(2025末)で部分対応             |
| **Databricks**       | ✅ RRF   | ❌            | RRF内蔵。L2距離のみ                      |
| **pgvector**         | △ 外部拡張  | ✅(sparsevec) | pg_bigm/PGroonga要追加               |

### セキュリティ比較

| 製品 | RLS×ベクトル検索 | 階層型ラベル | TDE | 細粒度監査 |
|------|----------------|------------|-----|----------|
| **Oracle 23ai** | ✅ VPD完全統合 | ✅ OLS | ✅ ベクトル演算に影響なし | ✅ FGA |
| **pgvector** | ✅ RLS自動適用 | ❌ | ❌ ベクトル列は不可 | ❌ |
| **BigQuery** | ✅ 自動適用 | ❌ | AES-256 | △ |
| **Qdrant** | △(Cloud) | ❌ | AES-256 | △ |
| **Elasticsearch** | ✅ ドキュメントレベル | ❌ | AES-256 | △ |
| **Snowflake** | ⚠️ Owner権限問題 | ❌ | AES-256 | △ |
| **Databricks** | ❌ RLSありは不可 | ❌ | AES-256 | △ Unity Catalog |
| **Pinecone** | △ Namespace分離 | ❌ | AES-256 | △ |

### スケール・コスト・セルフホスト

| 製品                | スケール上限           | セルフホスト       | ライセンス    |
| ----------------- | ---------------- | ------------ | -------- |
| **Milvus**        | **兆規模**（DiskANN） | ✅ Apache 2.0 | 完全OSS    |
| **Elasticsearch** | ペタバイト級           | ✅ AGPL       | OSS/商用   |
| **Weaviate**      | 数百億              | ✅ BSD-3      | 完全OSS    |
| **Qdrant**        | 数十億              | ✅ Apache 2.0 | 完全OSS    |
| **Redis**         | 10億（実証済み）        | △ SSPL       | 商用注意     |
| **Pinecone**      | 事実上無制限           | **不可**       | プロプライエタリ |
| **Databricks**    | 10億（SO）          | △            | 商用       |
| **BigQuery**      | 2億（TreeAH）       | ❌            | GCPサービス  |
| **Snowflake**     | 1億行              | ❌            | 商用       |
| **Oracle 23ai**   | 未公開              | ✅（Free 12GB） | 商用/無料版   |
| **pgvector**      | インフラ依存           | ✅            | 完全OSS    |

**ライセンス補足**
- **完全OSS**（Apache 2.0, BSD-3）: 商用利用・改変・再配布すべて無制限。リスクなし
- **OSS/商用混在**（AGPL, Elastic License）: SaaSとして外部提供する場合、自社ソースコードの公開義務が発生することがある
- **商用注意**（SSPL = Redis）: OSSに見えるが、クラウドプロバイダーがホスト提供する場合は商用契約が必要。自社でホストするだけなら無料
- **プロプライエタリ**（Pinecone）: ソースコード非公開・APIのみ。ベンダーロックイン・価格改定・サービス終了リスクあり
- **商用**（Snowflake, Databricks）: 従量課金。利用量増加でコストが青天井になりやすい
- **商用/無料版あり**（Oracle 23ai Free）: 無料版は12GB制限。本番移行時に商用ライセンス費用が発生
- **GCPサービス**（BigQuery）: Google管理のマネージドサービス。GCPエコシステムへの依存度が高い

---

## 8. コスト：Embedding API × ベクトルDB 課金比較

### Embedding API コスト早見表（$/ 1Mトークン）

| モデル | 標準 | バッチ | 次元数 | 日本語 |
|--------|------|--------|--------|--------|
| Mistral Embed | **$0.01** | — | 1,024 | △ |
| OpenAI text-embedding-3-small | $0.02 | $0.01 | 1,536 | ○ |
| Voyage AI voyage-3.5-lite | $0.02 | $0.013 | 1,024 | ○ |
| Voyage AI voyage-3.5 | $0.06 | $0.04 | 1,024 | ◎ |
| Cohere Embed v3 | $0.10 | — | 1,024 | ○ |
| AWS Bedrock Titan V1 | $0.10 | — | 1,536 | △ |
| **OpenAI text-embedding-3-large** | **$0.13** | $0.065 | **3,072** | ◎ |
| **Gemini text-embedding-004** | **$0.15** | $0.075 | **3,072** | ◎ |
| Cohere Embed v4 | $0.12(text)/$0.47(image) | — | 4,096 | ◎ |

**無料枠**: Voyage AI は最初の200Mトークン無料。検証フェーズはここから始めるのが最安。

### ベクトルDB コスト比較

| DB | ストレージ月額 | 無料枠 | 備考 |
|----|-------------|--------|------|
| **Oracle 23ai Free** | 無料（12GB） | ✅ 永続 | ローカル開発用 |
| **OCI Always Free** | 無料（20GB） | ✅ 永続 | クラウド評価用 |
| **OCI ADB Serverless（Shared）** | ~$0.096/ECPU時間+$0.023/GB/時間 | — | 使った分だけ課金。停止で$0 |
| **OCI ADB Serverless（Dedicated）** | ~$238/月〜（2 ECPU+1TB固定） | — | 専有インフラ |
| **pgvector (Supabase)** | $25〜 | — | 最低コスト本番 |
| **Qdrant Cloud** | $27〜（量子化後） | 1GB | INT8量子化で73%削減（元$102） |
| **Zilliz Cloud** | $0.04/GB | 5GB | 2026年〜87%値下げ |
| **Weaviate Cloud** | $25〜（圧縮後） | 14日 | 圧縮で84%削減（元$153） |
| **Pinecone Serverless** | $0.33/GB | Starter | マネージドのみ |
| **Snowflake Cortex** | 6.3cr/GB（**常時固定・停止不可**） | — | 隠れ固定費に注意 |
| **BigQuery** | 標準BQ料金 | インデックス作成無料 | |
| **Databricks** | DBU課金 | — | Delta統合でデータ移動コストゼロ |

### 量子化によるコスト削減

```
Float32（標準）  ████████████████  ベースライン  精度劣化 0%
Float16 / Float8 ████░░░░░░░░░░░░  75%削減       精度劣化 <0.3%（実用上無視）
Int8（SQ8）      ████░░░░░░░░░░░░  75%削減       精度劣化 ~1.5%
Binary（BBQ）    █░░░░░░░░░░░░░░░  97%削減       精度劣化 大（モデル依存）
```

推奨: Float8 or Int8 が品質×コストのベストバランス。

### ユースケース別コスト試算

| ユースケース | Embedding | DB | 月額目安 |
|-------------|-----------|-----|---------|
| 無料検証 | Voyage AI voyage-3.5（200M無料） | Qdrant無料枠 | **$0** |
| スタートアップ本番 | OpenAI 3-small ($0.02) | Supabase pgvector | **~$30+API** |
| 高精度中コスト | Voyage AI voyage-3.5 ($0.06) | Zilliz Cloud | **~$50+API** |
| 3,072次元フル活用 | Gemini embedding-004 ($0.15) | **Oracle OCI Always Free** | **~$0+API** ← PJ19構成 |
| エンタープライズ機密文書 | Gemini/OpenAI 3-large | Oracle ADB Shared | **~$50+API** |

### 次元数↑ = コスト3方向に増加（全DB共通）

```
次元数↑
  ├─ Embedding APIコスト↑（高精度モデル = 高単価: $0.02 → $0.13 で6.5倍）
  ├─ ストレージコスト↑（float32 × 次元数 × ベクトル数に比例）
  └─ クエリコスト↑（距離計算が次元数に比例）
```

**Oracle 23aiの優位性は「コストが変わらない」ではなく「移行作業コストが発生しない」**：
- pgvector: 次元が上がるたびにスキーマ変更・コード修正・テーブルロックが必要
- Snowflake/Databricks/Elasticsearch: 4,096次元が物理上限。それ以上のモデルは使用不可
- Oracle: インデックス再構築のみ（全DBで共通の作業）

### コスト削減チェックリスト

1. **バッチ処理** → OpenAI -50%、Voyage AI -33%
2. **Matryoshka次元削減** → text-embedding-3-large を 3,072→512次元に。ストレージ6分の1（pgvectorの2,000次元制限もクリア）
3. **Int8量子化** → 精度1.5%劣化でストレージ75%削減
4. **クエリキャッシュ** → 同一クエリの再Embedding回避で劇的削減
5. **チャンクサイズ最適化** → 256→512tokenにするだけでAPI呼び出し半減

---

## 9. DB選定フローチャート

```
START: RAG基盤を構築したい
│
├─ 既存のOracleインフラがあるか？
│   └─ YES → Oracle 23ai 一択（追加ライセンス不要）
│
├─ 機密レベルの区分が3段階以上あるか？
│   └─ YES → Oracle 23ai（OLS + VPD + FGA統合）
│            Databricks / Snowflake は避ける（RLS制約あり）
│
├─ 3,072次元以上のモデルを使いたいか？
│   └─ YES → Oracle 23ai / Milvus / Redis（pgvectorはhalfvec必須）
│            Snowflake / Databricks / Elasticsearch は4,096上限
│
├─ 10億ベクトル以上のスケールが必要か？
│   └─ YES → Milvus / Zilliz Cloud（DiskANN + PQ量子化）
│
├─ 既存DWH（Snowflake/BigQuery/Databricks）と統合したいか？
│   └─ YES → 各プラットフォームネイティブ機能
│            ただしセキュリティ制約を事前確認
│
├─ 全文検索との高精度統合が最優先か？
│   └─ YES → Elasticsearch（最成熟） / Milvus v2.5
│
├─ コスト最小・OSS・セルフホストしたいか？
│   └─ YES → Qdrant / Weaviate / Milvus（Apache 2.0）
│
└─ とにかく最速でスタートしたいか？
    └─ YES → Pinecone（マネージドのみ） / Qdrant Cloud / Supabase pgvector
```

---

## 10. ユースケース別最終選定マトリクス

| ユースケース                    | 最優先                                | 次善                                    | 避けるべき                                                                  |
| ------------------------- | ---------------------------------- | ------------------------------------- | ---------------------------------------------------------------------- |
| **機密文書RAG（階層型セキュリティ）**    | **Oracle 23ai**                    | pgvector + RLS                        | Databricks（RLS不可）, Snowflake（Owner権限問題）                                |
| **高次元モデル（3,072〜4,096次元）** | **Oracle 23ai**                    | Milvus / Redis                        | pgvector（halfvec変換必須）, Elasticsearch / Snowflake / Databricks（4,096上限） |
| **SQL完結RAGパイプライン**        | **Oracle 23ai**                    | Snowflake Cortex                      | pgvector（全て外部実装）, Milvus / Pinecone                                    |
| **規制業種・監査対応（金融・医療・公共）**   | **Oracle 23ai**                    | Elasticsearch                         | Pinecone（監査機能弱）, Databricks                                            |
| **コスト最小で高次元を評価・本番化**      | **Oracle OCI Always Free**（20GB無料） | pgvector + Supabase                   | Snowflake（固定費高）, Pinecone                                              |
| **ハイブリッド検索精度最優先**         | **Oracle 23ai**                    | Elasticsearch / Milvus v2.5           | BigQuery（手動SQL）, Databricks（L2距離のみ）                                    |
| **既存Oracle環境への追加**        | **Oracle 23ai**（追加ライセンス不要）         | —                                     | 他製品（移行コスト発生）                                                           |
| **将来の次元数拡張に備える**          | **Oracle 23ai**（65,535次元）          | Milvus / Redis（32,768次元）              | Snowflake / Databricks / Elasticsearch（4,096上限）                        |
| **兆規模ベクトル（10億+）**         | Milvus / Zilliz                    | **Oracle 26ai**（Globally Distributed） | pgvector, Snowflake（1億上限）                                              |
| **低レイテンシ（<1ms）**          | Redis                              | Qdrant                                | BigQuery, Snowflake                                                    |
| **既存DWH分析との統合**           | 現在使っているDWHのネイティブ機能（Snowflake→Cortex, BigQuery→Vector Search, Databricks→VSS） | 乗り換えコストが低い隣接DWH | 専用ベクトルDB（DWH連携が複雑化）                                                    |
| **MLOps・機械学習パイプライン**      | Databricks                         | BigQuery                              | Oracle（ML統合が弱い）                                                        |
| **セルフホスト・完全OSS**          | Qdrant / Milvus / Weaviate         | pgvector                              | Pinecone（セルフホスト不可）                                                     |

---

## 11. 付録：セキュリティ詳細（Oracle vs pgvector）

### ラベルセキュリティの具体例

```sql
-- チャンクに機密レベルを付与
UPDATE chunks SET sec_label = CHAR_TO_LABEL('DOC_POLICY', '機密:部内:人事部')
WHERE source_name = 'internal-hr-policy';

-- 検索時：WHERE句なしでも自動フィルタ
SELECT chunk_text, VECTOR_DISTANCE(embedding, :qvec, COSINE) AS dist
FROM chunks
ORDER BY dist
FETCH FIRST 5 ROWS ONLY;
-- 部長: 機密チャンク含む全件返却
-- 一般社員: 機密チャンクは自動で不可視
```

PostgreSQL RLS との差：
- **ポリシー定義がアプリ単位**（Oracleはラベル体系をDB横断で一元管理）
- **階層的ラベルの自動継承がない**（アプリ側で実装）
- **監査証跡との統合がない**

### 運用コスト比較（Oracle vs pgvector）

| 項目 | Oracle 23ai | PostgreSQL + pgvector |
|------|------------|----------------------|
| DBA要求スキル | 高い（Oracle固有知識） | 中程度（汎用Linux/SQL） |
| エコシステム | 公共・金融・医療で実績 | スタートアップ・Web系で主流 |
| Python連携 | oracledb（公式） | psycopg2/asyncpg（成熟） |
| pgvector更新頻度 | N/A | 活発（月1-2回リリース） |

---

## 関連ノート

- [[oracle23ai-to-pgvector-migration]] — Oracle 23ai → PostgreSQL+pgvector 移行記（障害経緯・embedding同時移行・精度51%改善）
