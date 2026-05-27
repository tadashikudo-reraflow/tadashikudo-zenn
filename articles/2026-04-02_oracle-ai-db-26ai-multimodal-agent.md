---
title: "AIエージェント時代の「ファイルシステム終焉」論——Oracle AI Database 26ai"
emoji: "🗄️"
type: "tech"
topics: ["Oracle", "AI", "LLM", "Database"]
published: true
---
# AIエージェント時代の「ファイルシステム終焉」論——Oracle AI Database 26ai

## はじめに

「なぜファイルをS3に保存するのか？」——この問いに答えられるエンジニアは多いが、「では2026年以降もそれが最適解か？」と問い直すと途端に静まり返る。

Oracle Database 26aiは、その問いに対してひとつの挑発的な回答を提示する。ドキュメント、画像、音声、テキストをデータベースに直接格納し、LLMとベクトル検索とSQLを同一トランザクション内で扱う——という設計だ。

本記事では、Oracle AI Database 26aiの主要機能とアーキテクチャ、そして「AIエージェントが主役になる時代のデータ管理」という観点からその意味を掘り下げる。

---

## Oracle AI Database 26aiとは

Oracle Database 26aiは、Oracle Database 23aiの後継として2025〜2026年にかけてリリースされたバージョンで、AI機能を**ファーストクラスの市民**として扱う初めてのOracle DBメジャーバージョンだ。

従来のOracle DBとの主な違いは以下の3点：

| 機能 | 23aiまで | 26aiで追加・強化 |
|------|----------|----------------|
| ベクトル検索 | VECTOR型（23ai追加） | ハイブリッド検索（全文+意味）統合 |
| LLM呼び出し | 外部APIへのプロシージャ | `DBMS_VECTOR_CHAIN`でRAGパイプライン内蔵 |
| マルチモーダル | 非対応 | 画像・PDF・音声のネイティブAIチャンク化 |
| AIエージェント連携 | 非対応 | Tool Use / Function Callingレイヤ |

---

## 主要機能の深堀り

### 1. VECTOR型とハイブリッド検索

Oracle 23aiで導入された`VECTOR`型は26aiで大幅に強化された。最大の変更は**ハイブリッド検索**の統合だ。

```sql
-- ハイブリッド検索：全文スコア × ベクトル類似度を組み合わせる
SELECT doc_id, title, content,
       VECTOR_DISTANCE(embedding, :query_vec, COSINE) AS vec_score,
       CONTAINS(content, :keyword)                    AS fts_score
FROM   documents
WHERE  CONTAINS(content, :keyword) > 0
   OR  VECTOR_DISTANCE(embedding, :query_vec, COSINE) < 0.3
ORDER  BY (vec_score * 0.7 + (1 - fts_score/100) * 0.3);
```

全文検索（Oracle Text）とベクトル検索をSQLの`WHERE`句で混在できるため、「キーワードで絞り込みつつ意味的に近いドキュメントを優先する」RAGパターンがSQL一本で実現できる。

### 2. DBMS_VECTOR_CHAINによるRAGパイプライン

26aiの最大の特徴の一つが`DBMS_VECTOR_CHAIN`パッケージだ。埋め込み生成→検索→LLM呼び出し→回答生成を**DB内部で完結**させるパイプラインを定義できる。

```sql
-- RAGパイプライン全体をPL/SQLで定義
DECLARE
  v_params CLOB := '{
    "provider": "openai",
    "model": "text-embedding-3-small",
    "credential_name": "OPENAI_CRED"
  }';
  v_query    VARCHAR2(500) := '契約書の解約条件を教えて';
  v_context  CLOB;
  v_answer   CLOB;
BEGIN
  -- Step 1: クエリをベクトル化
  -- Step 2: 類似チャンクを検索
  -- Step 3: LLMに問い合わせ
  DBMS_VECTOR_CHAIN.UTL_TO_GENERATE_TEXT(
    p_query         => v_query,
    p_vector_store  => 'CONTRACT_CHUNKS',
    p_credential    => 'OPENAI_CRED',
    p_generate_spec => '{"provider":"openai","model":"gpt-4o"}',
    p_answer        => v_answer
  );
  DBMS_OUTPUT.PUT_LINE(v_answer);
END;
```

PythonアプリからはLangChainやLlamaIndexのOracle DBコネクタを使って同じパイプラインを呼び出せる。

### 3. マルチモーダルドキュメント処理

26aiで新たに加わった`DBMS_VECTOR_CHAIN.UTL_TO_CHUNKS`は、PDFや画像をDBに格納しながらAIチャンク化を自動で行う。

```sql
-- PDF/画像を直接格納してチャンク化
INSERT INTO document_store (doc_id, doc_name, doc_content)
VALUES (1, '契約書.pdf', TO_BLOB(bfilename('DOC_DIR','contract.pdf')));

-- AIチャンク化（langchain形式で自動分割）
BEGIN
  DBMS_VECTOR_CHAIN.UTL_TO_CHUNKS(
    p_data      => (SELECT doc_content FROM document_store WHERE doc_id = 1),
    p_params    => '{"by":"words","max":200,"overlap":20}',
    p_chunks    => :chunk_output
  );
END;
```

「S3にPDFを置いてLambdaで前処理してOpenSearch Serverlessに入れて……」という従来のパイプラインが、DBへのINSERTで完結する。これが「ファイルシステム終焉」論の核心だ。

---

## AIエージェント連携レイヤ

26aiが最も野心的なのが**Tool Use / Function Calling**との統合だ。Oracle Select AI機能が拡張され、自然言語クエリをSQLに変換するだけでなく、エージェントがDB内の関数を直接ツールとして呼び出せる。

### Select AIとエージェント統合

```python
import oracledb

conn = oracledb.connect(
    user=os.environ.get("DB_USER"),
    password=os.environ.get("DB_PASSWORD"),
    dsn=os.environ.get("DB_DSN")
)

# Select AI: 自然言語→SQL変換
with conn.cursor() as cur:
    cur.execute("""
        SELECT DBMS_CLOUD_AI.GENERATE(
            prompt       => '先月の売上上位5製品を教えて',
            profile_name => 'OPENAI_PROFILE',
            action       => 'narrate'
        ) FROM DUAL
    """)
    result = cur.fetchone()
    print(result[0])
```

`action => 'narrate'`を指定するとSQLを実行して結果を自然言語で返す。`'runsql'`にすると生のSQLが返るためエージェントの中間ステップとして使える。

### MCP（Model Context Protocol）対応

26aiはOracle DatabaseのMCPサーバ実装を公式サポートしており、Claude CodeやCursorなどのAIエージェントから直接DB操作が可能になっている。

```json
// .mcp.json
{
  "mcpServers": {
    "oracle-db": {
      "command": "npx",
      "args": ["@oracle/mcp-server-oracle"],
      "env": {
        "DB_CONNECTION": "your-connection-string",
        "DB_USER": "your-user"
      }
    }
  }
}
```

---

## 実践：RAGシステムをOracle 26aiで構築する

以下はPythonとOracle 26aiを使ったシンプルなRAGシステムの例だ。LangChainのOracle DBベクトルストアを使う。

```python
from langchain_community.vectorstores.oraclevs import OracleVS
from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_openai import OpenAIEmbeddings
import oracledb

# DB接続
connection = oracledb.connect(
    user=os.environ.get("ORACLE_USER"),
    password=os.environ.get("ORACLE_PASSWORD"),
    dsn=os.environ.get("ORACLE_DSN")
)

# ベクトルストア初期化
embeddings = OpenAIEmbeddings(
    api_key=os.environ.get("OPENAI_API_KEY"),
    model="text-embedding-3-small"
)

vector_store = OracleVS.from_documents(
    documents=docs,
    embedding=embeddings,
    client=connection,
    table_name="MY_RAG_STORE",
    distance_strategy=DistanceStrategy.COSINE
)

# 類似検索
results = vector_store.similarity_search(
    query="解約条件について",
    k=5
)
```

従来のPinecone/Weaviateと比べて、アプリケーションが接続するエンドポイントが**OracleDB一つだけ**になる。トランザクション管理、RBAC、監査ログが既存のDB運用ノウハウでそのまま使える点が大きい。

---

## 「ファイルシステム終焉」論の現実解

タイトルの「ファイルシステム終焉」はやや煽り気味だが、正確には「**AIがデータを消費する経路としてのファイルシステムの退場**」と言うべきだろう。

実際のユースケース別の使い分けを整理すると：

| ユースケース | 推奨アーキテクチャ |
|-------------|-----------------|
| 大容量バイナリ（動画・大規模CSV） | オブジェクトストレージ（S3等）+ Oracle External Tables |
| RAG・AIエージェントの知識ベース | Oracle 26ai VECTOR型に直接格納 |
| LLMコンテキスト管理 | Oracle 26ai + Select AI |
| リアルタイムストリーム | Kafka + Oracle GoldenGate |

「なんでもDBに入れる」ではなく、「AIが推論に使うデータはDBに入れ、AIが消費しないデータはオブジェクトストレージのまま」という棲み分けが現実解だ。

---

## まとめ

Oracle AI Database 26aiのポイントをまとめる：

- **VECTOR型 + ハイブリッド検索**：全文検索とベクトル検索をSQLで統合、RAGパターンのDB内完結が可能
- **DBMS_VECTOR_CHAIN**：埋め込み生成→検索→LLM呼び出しをPL/SQLパイプラインとして定義
- **マルチモーダル対応**：PDF・画像をBLOBで格納し、AIチャンク化を自動実行
- **Select AI**：自然言語→SQL変換とエージェント連携を公式サポート
- **MCP対応**：Claude Code等のAIエージェントから直接DB操作が可能
- **現実解は棲み分け**：AI推論に使うデータはDB、大容量バイナリはオブジェクトストレージ

AIエージェントが業務データに直接アクセスする時代において、データの信頼性・一貫性・セキュリティをDBレイヤで担保する設計は、改めて重要性を増している。
