---
title: "SupabaseでAIエージェントの共有記憶を作る——Optimistic Concurrencyで並行書き込み競合を防ぐ実装"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "Supabase", "Python", "OptimisticConcurrenc", "AIAgent"]
published: false
---
# SupabaseでAIエージェントの共有記憶を作る——Optimistic Concurrencyで並行書き込み競合を防ぐ実装

## はじめに

複数のAIエージェントが協調して動く「マルチエージェントシステム」では、エージェント間で状態を共有する**共有記憶（Shared Memory）**が欠かせません。たとえば、

- 調査エージェントが集めた情報を要約エージェントが参照する
- 計画エージェントが立てたタスクリストを実行エージェントが更新する
- 複数のワーカーが同じコンテキストバッファに書き込む

このとき、複数のエージェントが**同時に同じレコードを更新しようとする競合**が起きます。適切な対策なしでは「後勝ち」（last-write-wins）で古いデータが上書きされ、エージェントの作業が消えてしまいます。

この記事では、Supabaseを使ったエージェント共有記憶の実装と、**Optimistic Concurrency Control（楽観的並行性制御）**で競合を検知・リトライする方法を解説します。

---

## Optimistic Concurrency Controlとは

データベースの並行制御には2つのアプローチがあります：

| 方式 | 特徴 | 向いている場面 |
|------|------|--------------|
| **Pessimistic（悲観的）** | 書き込み前にロック取得（`SELECT FOR UPDATE`） | 競合頻度が高い・データ損失が致命的 |
| **Optimistic（楽観的）** | ロックせず書き込み、競合時はリトライ | 競合が稀・高スループットが必要 |

AIエージェントの場合、多くのシナリオで**競合は稀**（各エージェントが異なるドメインを担当）なため、Optimistic CCが適しています。

### 基本的な仕組み

```
1. レコードを読む（version=3）
2. ローカルで変更を加える
3. UPDATE ... WHERE id = ? AND version = 3
4. 更新件数が0 → 誰かが先に更新した → リトライ
   更新件数が1 → 成功、version=4になる
```

`version`カラム（整数または`updated_at`タイムスタンプ）を使い、**読み取り時と書き込み時のバージョンが一致するときだけ**更新を許可します。

---

## Supabaseのスキーマ設計

### テーブル定義

```sql
-- エージェント共有記憶テーブル
CREATE TABLE agent_memory (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id    TEXT NOT NULL,           -- どのエージェントの記憶か
  session_id  TEXT NOT NULL,           -- セッション識別子
  key         TEXT NOT NULL,           -- 記憶のキー（例: "task_list", "summary"）
  value       JSONB NOT NULL,          -- 記憶の内容（JSON）
  version     INTEGER NOT NULL DEFAULT 1,  -- 楽観的ロック用バージョン
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(session_id, key)
);

-- バージョン自動インクリメントのトリガー
CREATE OR REPLACE FUNCTION increment_version()
RETURNS TRIGGER AS $$
BEGIN
  NEW.version := OLD.version + 1;
  NEW.updated_at := NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER auto_increment_version
  BEFORE UPDATE ON agent_memory
  FOR EACH ROW EXECUTE FUNCTION increment_version();
```

### RLSポリシー

```sql
ALTER TABLE agent_memory ENABLE ROW LEVEL SECURITY;

-- service_roleのみ書き込み可能（エージェントはバックエンド経由で操作）
CREATE POLICY "service_role_all" ON agent_memory
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- anon/authenticatedユーザーはread-only（必要な場合のみ）
CREATE POLICY "authenticated_read" ON agent_memory
  FOR SELECT TO authenticated USING (true);
```

---

## Pythonでの実装

### 基本クラス

```python
import os
import time
from dataclasses import dataclass
from typing import Any, Optional
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

@dataclass
class MemoryRecord:
    id: str
    key: str
    value: Any
    version: int

class AgentMemoryStore:
    """Optimistic Concurrencyで競合を検知するエージェント共有記憶ストア"""

    def __init__(self, session_id: str, max_retries: int = 3):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        self.session_id = session_id
        self.max_retries = max_retries

    def read(self, key: str) -> Optional[MemoryRecord]:
        """記憶を読み取る（バージョン情報付き）"""
        result = (
            self.supabase.table("agent_memory")
            .select("id, key, value, version")
            .eq("session_id", self.session_id)
            .eq("key", key)
            .maybe_single()
            .execute()
        )
        if result.data is None:
            return None
        d = result.data
        return MemoryRecord(id=d["id"], key=d["key"],
                            value=d["value"], version=d["version"])

    def write(self, key: str, value: Any,
              agent_id: str = "agent") -> MemoryRecord:
        """
        Optimistic Concurrencyで書き込む。
        競合検知時は自動リトライ（最大 max_retries 回）。
        """
        for attempt in range(self.max_retries):
            record = self.read(key)

            if record is None:
                # 初回INSERT（競合しないのでシンプルに）
                result = (
                    self.supabase.table("agent_memory")
                    .insert({
                        "agent_id": agent_id,
                        "session_id": self.session_id,
                        "key": key,
                        "value": value,
                    })
                    .execute()
                )
                d = result.data[0]
                return MemoryRecord(id=d["id"], key=d["key"],
                                    value=d["value"], version=d["version"])

            # Optimistic Update: version が一致する場合だけ更新
            result = (
                self.supabase.table("agent_memory")
                .update({"value": value, "agent_id": agent_id})
                .eq("id", record.id)
                .eq("version", record.version)   # ← キーポイント
                .execute()
            )

            if result.data:
                # 更新成功（バージョンが一致した）
                d = result.data[0]
                return MemoryRecord(id=d["id"], key=d["key"],
                                    value=d["value"], version=d["version"])

            # バージョン不一致 → 競合発生 → リトライ
            wait = 0.1 * (2 ** attempt)  # exponential backoff
            print(f"[OCC] 競合検知 (attempt={attempt+1}), {wait:.1f}s後にリトライ")
            time.sleep(wait)

        raise RuntimeError(
            f"[OCC] {self.max_retries}回リトライ後も競合解消できませんでした: key={key}"
        )
```

---

## 実際のエージェントへの組み込み

### 並行して動く2つのエージェントの例

```python
import asyncio
import json

async def research_agent(store: AgentMemoryStore):
    """調査エージェント: 収集した情報をメモリに追記する"""
    print("[Research] 調査開始")
    
    # 現在のメモリを読んで、新しい情報を追加
    record = store.read("findings")
    current = record.value if record else {"items": []}
    
    current["items"].append({
        "source": "web",
        "content": "Claude Code の新機能: Projects連携",
        "timestamp": "2026-05-27"
    })
    
    store.write("findings", current, agent_id="research_agent")
    print("[Research] 書き込み完了")

async def summarize_agent(store: AgentMemoryStore):
    """要約エージェント: 調査結果を要約してメモリに書く"""
    print("[Summary] 要約開始")
    await asyncio.sleep(0.05)  # 少し遅延（競合をシミュレート）
    
    record = store.read("findings")
    if record:
        summary = f"収集アイテム数: {len(record.value.get('items', []))}"
        store.write("summary", {"text": summary, "based_on_version": record.version},
                    agent_id="summarize_agent")
    print("[Summary] 書き込み完了")

async def main():
    store = AgentMemoryStore(session_id="demo-session-001")
    
    # 2エージェントを並行実行
    await asyncio.gather(
        research_agent(store),
        summarize_agent(store),
    )
    
    # 結果確認
    findings = store.read("findings")
    summary = store.read("summary")
    print(f"\n最終状態:")
    print(f"  findings (v{findings.version}): {json.dumps(findings.value, ensure_ascii=False)}")
    print(f"  summary  (v{summary.version}): {json.dumps(summary.value, ensure_ascii=False)}")

asyncio.run(main())
```

---

## マージ戦略：競合時にデータを捨てない

単純なリトライでは「最新版で上書き」になり、競合した側の更新が消えます。配列への追記など**マージが必要な場合**は、リトライ時にマージロジックを挟みます：

```python
def append_to_list(self, key: str, new_item: Any,
                   agent_id: str = "agent") -> MemoryRecord:
    """配列フィールドへの追記（競合時もデータを保持）"""
    for attempt in range(self.max_retries):
        record = self.read(key)
        
        if record is None:
            return self.write(key, {"items": [new_item]}, agent_id)
        
        # 最新版を取得してマージ（リトライのたびに再取得）
        merged_value = dict(record.value)
        merged_value.setdefault("items", [])
        merged_value["items"].append(new_item)
        
        result = (
            self.supabase.table("agent_memory")
            .update({"value": merged_value, "agent_id": agent_id})
            .eq("id", record.id)
            .eq("version", record.version)
            .execute()
        )
        
        if result.data:
            d = result.data[0]
            return MemoryRecord(id=d["id"], key=d["key"],
                                value=d["value"], version=d["version"])
        
        time.sleep(0.1 * (2 ** attempt))
    
    raise RuntimeError(f"マージ失敗: key={key}")
```

---

## まとめ

- **Optimistic Concurrency** は「ロックせず書き込み、競合したらリトライ」の方式で、マルチエージェントの高スループットなメモリ操作に適している
- Supabaseでは `version` カラム + `WHERE version = ?` の条件付きUPDATEで実装できる。トリガーでバージョン自動インクリメントすると更新漏れがない
- **RLSを必ず設定**し、エージェントはバックエンド経由（service_role）で操作する
- 競合検知後のリトライには **exponential backoff** を入れてスパイクを防ぐ
- 配列追記など「後勝ち不可」な操作は、リトライ時に**最新版を取得してマージ**するロジックが必要
