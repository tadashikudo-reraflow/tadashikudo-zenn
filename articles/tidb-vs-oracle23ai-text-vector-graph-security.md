---
title: "TiDB vs Oracle 23ai——テキスト・ベクトル・グラフ・セキュリティ4軸で本音比較【2026年版】"
emoji: "🏛️"
type: "tech"
topics: ["Oracle", "TiDB", "データベース", "ベクトル検索", "AI"]
published: true
---

# TiDB vs Oracle 23ai——テキスト・ベクトル・グラフ・セキュリティ4軸で本音比較【2026年版】

## はじめに

「HTAPはTiDBが最強」という言説が2023〜2024年に広まった。

HTAPとは、トランザクション処理（OLTP）と分析処理（OLAP）を同一エンジンで扱う設計思想だ。確かにTiDBはMySQLと互換性を保ちながらスケールアウトできるという強みがある。しかし2026年の観点から見ると、**AIワークロードの4つの核心要素**——テキスト検索・ベクトル検索・グラフ・セキュリティ——においてはOracle 23aiが組織的・機能的に数周先を走っている。

筆者はAI競艇予想モデルとAI競馬予想モデルという2つの本番AIサービスでOracle 23aiを最大限活用してきた。日次でレースデータを収集・特徴量エンジニアリングし、LightGBMモデルを訓練・推論する全パイプラインがOracleの上で動く。その実体験から、TiDBとOracleを正直に比較する。

---

## サマリー比較表

| 機能軸 | TiDB (Serverless) | Oracle 23ai (ADB) | 勝者 |
|--------|------------------|-------------------|------|
| **テキスト検索** | MySQLのFULLTEXT (BM25なし) | Oracle Text (30年の成熟) | 🏛️ Oracle |
| **ベクトル検索** | VECTOR型 + HNSW (2024〜) | AI Vector Search + ONNX in-DB | 🏛️ Oracle |
| **グラフ対応** | なし（外部連携必須） | SQL/PGQ + Property Graph (23ai) | 🏛️ Oracle |
| **セキュリティ** | RBAC + TLS | VPD + OLS + TDE + Audit Vault | 🏛️ Oracle |
| **スケールアウト** | ✅ シャーディング自動 | △ RAC/Sharding（複雑） | ✅ TiDB |
| **MySQL互換** | ✅ ほぼ完全 | × | ✅ TiDB |
| **コスト（小規模）** | Serverless無料枠あり | Oracle 23ai Free（ローカル） / ADB Always Free（クラウド）| 🏛️ Oracle |
| **ドキュメント深度** | 浅い（歴史が短い） | 30年分の事例・MOS Notes | 🏛️ Oracle |

> **コスト補足**: Oracle 23ai Freeはローカルインストール版（フル機能・無料）。OCI ADB Always Freeはクラウド版（2インスタンス・2OCPU・20GB永久無料）。TiDB Serverlessは月25GBのストレージと500万リクエストまで無料。小規模開発では実質どちらも無料で始められるが、AIフル機能（DBMS_VECTOR・Oracle Text・SQL/PGQ）がそのまま使えるのはOracle 23ai Freeの強みだ。

---

## 1. テキスト検索——30年の差

### TiDBの現実

TiDBはMySQL互換なので `FULLTEXT INDEX` が使える。しかし**BM25スコアリングが実装されていない**。MySQLのFULLTEXTは「単語が出現するか否か」の粗い全文検索であり、TF-IDFすら正確には計算されない。

```sql
-- TiDB: 動くが精度は低い
SELECT * FROM races WHERE MATCH(race_comment) AGAINST('差し馬 内枠' IN BOOLEAN MODE);
```

RAGやセマンティック検索の補完として「キーワードにもヒットさせたい」ケースでは、TiDBのFULLTEXTは実用上力不足になる。

### Oracleの現実（競馬予想モデルでの実体験）

Oracle Textは1999年から存在し、`CONTAINS`演算子・CTXCATインデックス・テーマ検索・近傍検索など、単純な全文検索をはるかに超えた機能群を持つ。

```sql
-- Oracle Text: BM25スコア付き検索
SELECT horse_name, SCORE(1) AS relevance
FROM race_horses
WHERE CONTAINS(race_memo, 'ABOUT(差し 内枠)', 1) > 0
ORDER BY SCORE(1) DESC;
```

競馬予想モデルでは馬の調教コメントや厩舎コメントをOracle Textでインデックス化し、特定パターンの「強調コメント」を特徴量として抽出している。**30年分のチューニングノウハウ**がMOS（My Oracle Support）に蓄積されており、本番トラブル時に立ち返れる情報量が段違いだ。

---

## 2. ベクトル検索——「DB外でembedding」時代の終焉

### TiDBのアプローチ

TiDB Serverlessは2024年から `VECTOR(768)` 型と`HNSW`インデックスをサポートし始めた。MySQLユーザーが追加インフラなしでベクトル検索できる点は評価できる。

```sql
-- TiDB: ベクトル検索（MySQL互換環境から移行しやすい）
SELECT id, VEC_COSINE_DISTANCE(embedding, '[0.1, 0.2, ...]') AS dist
FROM documents
ORDER BY dist LIMIT 10;
```

ただしONNXモデルのDB内実行・バッチembedding生成・ハイブリッドサーチとの統合については、まだ外部アプリ層に依存する部分が多い。

### Oracle AI Vector Searchの本領（競馬予想モデルでの実体験）

Oracle 23aiの目玉機能が`DBMS_VECTOR`だ。**ONNXモデルをDB内部に直接ロードして推論できる**。つまりPythonアプリを介さず、SQL一本で「テキスト → embedding → ベクトル検索」が完結する。

```sql
-- ONNXモデルをDBにロード
BEGIN
  DBMS_VECTOR.LOAD_ONNX_MODEL(
    'MY_MODELS', 'bge-m3.onnx', 'BGE_M3_MODEL',
    JSON('{"function": "embedding", "embeddingOutput": "embedding"}')
  );
END;

-- DB内でembedding生成 → 類似検索を1クエリで
SELECT horse_name, vec_distance(embedding, 
  VECTOR_EMBEDDING(BGE_M3_MODEL USING '逃げ馬 先行有利コース' AS data),
  COSINE
) AS dist
FROM horse_profiles
ORDER BY dist FETCH FIRST 10 ROWS ONLY;
```

競馬予想モデル開発でハマったポイントを共有する。**ONNX出力のshapeは`[batch_size, dim]`（動的）でなければならない**。`[1, dim]`固定でエクスポートすると`ORA-54423`で拒否される。Hugging Faceからダウンロードしたモデルをそのまま使おうとすると高確率でこれに当たる。

```python
# NG: static batch size
# torch.onnx.export(model, dummy, "model.onnx", 
#   dynamic_axes=None)  ← これがダメ

# OK: dynamic batch size
torch.onnx.export(
    model, dummy_input, "model.onnx",
    opset_version=17,
    dynamic_axes={
        "input_ids": {0: "batch_size"},
        "attention_mask": {0: "batch_size"},
        "embedding": {0: "batch_size"}  # 出力も動的に
    }
)
```

この「DB内推論」ができることの意義は大きい。アプリサーバーとDBの間でembeddingを転送するネットワーク往復がなくなり、レイテンシが劇的に下がる。

---

## 3. グラフ対応——SQL/PGQが変えるもの

### TiDBのグラフ対応：ゼロ

TiDBにはネイティブのグラフ機能がない。Neo4jやNeptune等の専用グラフDBを外部に立てるか、アプリ側でグラフ構造を再現するしかない。

### Oracle 23ai SQL/PGQ（競馬予想モデルでの実体験）

Oracle 23aiはISO標準の**SQL/PGQ（Property Graph Query）**を実装している。リレーショナルテーブルの上にグラフビューを定義し、SQLから直接グラフ走査・パスクエリができる。

```sql
-- 馬の「血統グラフ」定義
CREATE PROPERTY GRAPH horse_lineage_graph
  VERTEX TABLES (horses KEY(horse_id))
  EDGE TABLES (
    lineage KEY(lineage_id)
    SOURCE KEY(sire_id) REFERENCES horses(horse_id)
    DESTINATION KEY(offspring_id) REFERENCES horses(horse_id)
  );

-- 3世代以内の父系をたどる（到達可能性クエリ）
SELECT *
FROM GRAPH_TABLE(horse_lineage_graph
  MATCH (ancestor IS horses) -[e IS lineage]->{1,3} (h IS horses WHERE h.horse_id = :target_id)
  COLUMNS (ancestor.horse_name, COUNT(e) AS distance)
);
```

ただし正直に書く。**PageRank等の中心性指標はOracle ADB上では直接計算できない**（Graph Studio WebUIは非対応。SQL/PGQはパス・到達可能性クエリのみ）。競馬予想モデルではNetworkXで計算してからOracleにMERGEで書き戻す構成を取った。

```python
# PageRankはNetworkX → Oracle書き戻しが現実解
import networkx as nx
G = nx.DiGraph()
# Oracleからエッジを取得してグラフ構築
G.add_edges_from(edges)
pr = nx.pagerank(G)

# Oracle側にMERGEで書き戻し
with conn.cursor() as cur:
    cur.executemany(
        "MERGE INTO horse_pagerank USING dual ON (horse_id = :1) "
        "WHEN MATCHED THEN UPDATE SET pr_score = :2 "
        "WHEN NOT MATCHED THEN INSERT VALUES (:1, :2)",
        [(k, v) for k, v in pr.items()]
    )
```

それでも「グラフビュー＋通常テーブル＋ベクトル検索」を同一トランザクション内で扱えるのはOracle固有の強みだ。TiDB + Neo4j + pgvectorの3DB構成に比べて運用コストが圧倒的に低い。

---

## 4. セキュリティ——エンタープライズの厚み

### TiDBのセキュリティ

TiDBはRBAC・TLS・監査ログをサポートする。オープンソースプロジェクトとして急速に整備されているが、エンタープライズ向けの細粒度アクセス制御はまだ発展途上だ。

### Oracleのセキュリティ深度

**VPD（Virtual Private Database）**はOracleが誇る行レベルセキュリティの象徴だ。

```sql
-- ユーザーごとに自動でWHERE句を付加するポリシー
CREATE OR REPLACE FUNCTION racing_data_filter(
  schema_name IN VARCHAR2, table_name IN VARCHAR2
) RETURN VARCHAR2 AS
BEGIN
  -- 一般ユーザーは公開レースデータのみ
  IF SYS_CONTEXT('USERENV', 'SESSION_USER') != 'ADMIN' THEN
    RETURN 'race_status = ''PUBLIC''';
  END IF;
  RETURN NULL; -- ADMINは全件
END;

EXEC DBMS_RLS.ADD_POLICY(
  object_schema => 'RACING',
  object_name => 'RACES',
  policy_name => 'RACING_VISIBILITY',
  function_schema => 'RACING',
  policy_function => 'RACING_DATA_FILTER'
);
```

アプリコードを一切変えずに、セッションコンテキストに応じてSELECT結果を動的にフィルタリングできる。競艇予想モデルでは内部の予測スコアと公開データを同一テーブルで管理しながら、この仕組みで分離している。

**TDE（Transparent Data Encryption）**・**Oracle Label Security（OLS）**・**Audit Vault**を組み合わせれば、金融・医療・公共セクターの要件を追加コードなしにクリアできる。これがOracle 30年の蓄積だ。

---

## 実装で学んだ「Oracleの地雷」

正直に書く。Oracleは強力だが、ハマりポイントも独自だ。

**1. macOSでの thin mode**

macOS上でPython oracledbを使う場合、`init_oracle_client()`は不要。thin modeが自動で使われる。なぜかドキュメントが分かりにくく、「Oracle Clientをインストールしなければ動かない」という古い情報に引きずられがち。

```python
# NG: macOSではOracle Client不要
# oracledb.init_oracle_client(lib_dir="/opt/oracle/instantclient")

# OK: thin modeで即接続
conn = oracledb.connect(user="user", password="pass", dsn="host:1521/service")
```

**2. BLOB送信は setinputsizes 必須**

大容量データ（モデルのpklなど）をBLOBカラムに送信する場合、`setinputsizes`でDB_TYPE_BLOBを明示しないと失敗する。競馬予想モデルでは548MBのpklファイルをこれで安定送信している。

```python
with conn.cursor() as cur:
    cur.setinputsizes(pkl_data=oracledb.DB_TYPE_BLOB)
    cur.execute(
        "INSERT INTO model_artifacts (version, pkl_data) VALUES (:version, :pkl_data)",
        version="v2026_35no", pkl_data=pkl_bytes
    )
```

**3. ADB Free tier のGraph Studio制限**

Oracle ADB Free tierではGraph Studio（WebUI）が非対応。SQL/PGQのCREATE PROPERTY GRAFTには権限が必要で、ADB上ではCLOUD_ADMIN等への付与が必要になる。「Oracleのグラフが使えない」という誤解はたいていこれが原因。SQL/PGQ自体は動く。

---

## どちらを選ぶべきか

| ユースケース | 推奨 |
|------------|------|
| MySQLからスケールアウトしたい | **TiDB** |
| スタートアップ・小規模サービス（MySQL互換必須） | **TiDB** |
| AI推論・ベクトル検索・グラフを同一DBで扱いたい | **Oracle 23ai** |
| 金融・医療・公共セクターのコンプライアンス要件 | **Oracle 23ai** |
| 本番AI予測パイプライン（モデル管理込み） | **Oracle 23ai** |
| チームのSQLスキルがMySQL依存 | **TiDB** |

---

## おわりに

「TiDBはスケールする。Oracleはインテリジェントだ。」

この一言に尽きる。

水平スケールアウトが最優先課題ならTiDB一択だ。しかしAIワークロードを本番に載せるとき——embedding・全文検索・血統グラフ・セキュリティポリシー——が同一エンジンで完結することの価値は計り知れない。筆者が競艇・競馬予想モデルでOracleを選んだのは「デファクトスタンダードだから」ではなく、**この4軸を1つのDBで完結できるDBが他になかったから**だ。

ローカル開発には**Oracle Database 23ai Free**（無料・フル機能）、クラウドにはOCI ADB Always Freeと、無料で始めるルートが両方そろっている。「OracleはHighコスト」という認識は2026年時点でもはや古い。

---

*著者: K.D*
*競艇予想モデル: Oracle ADB + LightGBM / 競馬予想モデル: Oracle 23ai + DBMS_VECTOR + LightGBM*
