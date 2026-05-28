---
title: "Anthropic Memory APIとは何か——Managed Agentsの「記憶」設計を読み解く"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AIAgent", "MemoryAPI", "Anthropic", "AI"]
published: false
---
# Anthropic Memory APIとは何か——Managed Agentsの「記憶」設計を読み解く

## はじめに

「AIエージェントに、昨日の失敗を今日覚えておかせるにはどうすればいいか」

エージェントを実務で使い始めた人なら一度は直面する問いだ。セッションをまたぐとコンテキストはリセットされ、同じミスが繰り返される。DBに手書きで記録を残してもエージェントが自発的に参照しないケースも多い。

Anthropicが2026年5月にPublic Betaとして公開した**Memory API**は、このセッション間記憶の問題に対するAnthropic公式の回答だ。Managed Agentsプラットフォーム上で提供され、エージェントが作業経験をファイルシステム型のMemory Storeに書き込み・読み込みする仕組みを標準化する。

本記事では、Memory APIのコアコンセプトを整理し、なぜこの設計になったのかを紐解く。同時期に発表された**Dreaming**（Research Preview）との関係も解説する。

---

## Memory APIの全体像

### Managed Agentsとの関係を押さえる

Memory APIはManaged Agentsプラットフォームの機能として提供される。まずManaged Agentsの位置づけを整理しておく。

| | Claude Code | Agent SDK | Managed Agents |
|---|---|---|---|
| 実行場所 | ローカル | ローカル/自前サーバー | Anthropicクラウド |
| Human in loop | あり | 開発者次第 | なし（Fire-and-forget）|
| 適用場面 | 対話・確認あり | スクリプト連携 | バッチ・全自動処理 |

Claude Codeが「隣に座って一緒に作業する同僚」であれば、Managed Agentsは「依頼書を渡して翌朝までに仕上げてもらう外注先」に近い。非同期バッチ処理向けのホスティング環境だ。

Memory APIはこのManaged Agents上のエージェントが「前回のセッションで何を学んだか」を次のセッションに引き継ぐための永続記憶層として設計されている。

---

## Memory APIの4つのコアコンセプト

### 1. ファイルシステムとして見せる

Memory Storeの設計上の特徴として、**Claude自身がファイルシステムとして操作できる形式**で永続化する点がある。

ベクトルDBのような抽象的なストアではなく、ディレクトリとファイルの構造で記憶を保持する。これはClaudeの強みに合わせた設計だ。ClaudeはBash操作・grep・ファイル構造化が得意であり、「記憶を整理する」という作業自体をClaudeに委ねやすい。

開発者から見ると、Memory Storeは次のように操作できる。

```python
from anthropic import Anthropic

client = Anthropic()

# Memory Storeにエントリを書き込む
client.beta.memory.write(
    store_id="store_abc123",
    key="tasks/2026-05-24/lesson_learned",
    content="Neonの接続文字列変更後はchannel_binding=requireを外すこと",
    metadata={"agent": "worker-a", "session_id": "sess_xyz"}
)

# Memory Storeから読み込む
entry = client.beta.memory.read(
    store_id="store_abc123",
    key="tasks/2026-05-24/lesson_learned"
)
print(entry.content)
```

### 2. Optimistic Concurrencyによる書き込み保護

複数のエージェントセッションが並列で動く場合、同じファイルへの同時書き込みが競合する。DBの行ロックに相当する問題だ。

Memory APIは**content_hashによるOptimistic Concurrency**でこれを解決する。

```python
# content_hashを使った安全な更新
entry = client.beta.memory.read(store_id=store_id, key=key)
current_hash = entry.content_hash  # 現在の状態のハッシュ

try:
    client.beta.memory.write(
        store_id=store_id,
        key=key,
        content=new_content,
        expected_content_hash=current_hash  # 読んだ時点のhashと一致しなければ失敗
    )
except MemoryConflictError:
    # 他のエージェントが先に書き込んでいた場合
    # 最新版を取得してマージロジックを走らせる
    latest = client.beta.memory.read(store_id=store_id, key=key)
    merged = merge_content(entry.content, new_content, latest.content)
    ...
```

書き込み時に「読んだ時点のhash」を渡し、サーバー側のhashと一致しなければ`MemoryConflictError`が返る。「読んでから書くまでの間に他が更新した」ケースを検知できる。DBのOPTIMISTIC LOCKINGと同じ原理だ。

### 3. Permission Scopeによるアクセス制御

Memory Storeにはすべての記憶が混在するが、すべてを等価に扱うわけではない。

**Permission Scope**で「このエントリは誰が読み書きできるか」を定義できる。

```python
# 読み取り専用エントリの書き込み（人間がレビューしたPlaybookなど）
client.beta.memory.write(
    store_id=store_id,
    key="playbooks/deploy_procedure",
    content="...",
    access="ro"  # エージェントは参照のみ・更新は人間のみ
)

# エージェントが随時更新するエントリ
client.beta.memory.write(
    store_id=store_id,
    key="learned/recent_errors",
    content="...",
    access="rw"  # エージェントが更新可
)
```

典型的な使い分け：
- `ro`：Playbookやビジネスルール（人間がレビューして確定したもの）
- `rw`：タスク完了後の学習ログ、エラーパターン、環境情報

### 4. Version Historyによる追跡

Memory Storeへの書き込みはすべてバージョン履歴として保持される。「いつ・誰が・何を書いたか」を追跡できる。

```python
# バージョン履歴を取得
history = client.beta.memory.list_versions(
    store_id=store_id,
    key="tasks/2026-05-24/lesson_learned"
)

for version in history:
    print(f"{version.created_at} | {version.metadata.get('agent')} | {version.content_hash[:8]}")
```

エージェントの判断が後から見て誤りだったとき、「どのタイミングで何を学習したか」を遡って確認できる。

---

## Dreamingとは何か

同時期に**Research Preview**として発表された**Dreaming**は、Memory APIの上に乗る自動化レイヤーだ。

通常の記憶書き込みが「タスク完了後の個別セッション」視点なのに対し、Dreamingは**複数セッションの作業transcriptをバックグラウンドで横断分析し、共通パターンを自動的にMemory Storeに書き込む**非同期プロセスだ。

```
セッションA: タスク完了 → transcript保存
セッションB: タスク完了 → transcript保存
セッションC: タスク完了 → transcript保存
                    ↓（非同期・ホットパス外）
              Dreamingプロセス
              ・3セッション横断分析
              ・「APIタイムアウト時はリトライ3回」という共通パターンを検出
              ・Memory Store["patterns/api_retry"] に自動書き込み
```

人間に例えれば、「業務日誌を毎日書いていたら、週次レビューで無意識のパターンに気づく」プロセスに近い。

Rakutenの事例では、Dreamingを活用した結果として「first pass mistakes（初回エラー率）が90%減少した」と報告されている。

---

## マルチエージェントでの実践パターン

Memory APIが真価を発揮するのはマルチエージェント構成だ。

### パターン1：Generator-Verifier

```
Session A（生成）
  → タスク完了後、Memory Storeに成果物の要点を書き込む
  → Events APIで完了通知

Session B（検証）
  → Events APIで完了を検知して起動
  → Memory Storeから成果物の要点を読む
  → 検証後、結果を別のkeyに書き込む
```

### パターン2：Shared State（並列学習）

複数エージェントが同じドメインで並列タスクを処理するとき、お互いの学習を共有できる。

```python
# エージェントAが学んだエラーパターン
client.beta.memory.write(
    store_id=shared_store,
    key="errors/db_timeout",
    content="Neonはアイドル後の最初のクエリで3〜5秒のウォームアップが発生する",
    metadata={"agent": "worker-a"}
)

# エージェントBが同じパターンを使う
pattern = client.beta.memory.read(
    store_id=shared_store,
    key="errors/db_timeout"
)
# → Aが発見したノウハウをBが即活用できる
```

---

## 既存のAgent SDKとの違い

Agent SDK（自前インフラで動かすライブラリ）でも、外部DBに記憶を書くことはできた。Memory APIが解決するのは**「その仕組みを毎回自作しなくてよくなる」**という点だ。

| 課題 | 従来（自前実装） | Memory API |
|------|---------------|-----------|
| 並列書き込み保護 | FOR UPDATE / 楽観的ロックを自作 | content_hash組み込み済み |
| アクセス制御 | DBレベルのRLSを自前設定 | Permission Scope標準搭載 |
| 監査ログ | ログテーブル設計 | Version History標準 |
| エージェント間共有 | Supabase等の外部DB設計 | store_id共有だけ |

ただし現時点ではManaged Agentsプラットフォーム限定の機能であり、Claude APIを直接使うAgent SDKからは利用できない点に注意が必要だ。

---

## まとめ

- **Memory APIの本質**: エージェントのセッション間記憶を標準化したファイルシステム型永続ストア
- **content_hash**: 並列エージェントの書き込み競合をOptimistic Concurrencyで解決
- **Permission Scope**: Playbook（ro）と学習ログ（rw）を分離することで誤上書きを防止
- **Dreaming**: バックグラウンドでtranscriptを横断分析し、共通パターンを自動記憶化
- **Managed Agents限定**: 現時点はManaged AgentsのPublic Beta機能（Agent SDK直接利用は不可）

AIエージェントが「毎回ゼロから学び直す」のではなく、経験を蓄積して改善していく——そのための設計基盤として、Memory APIはエージェントアーキテクチャの標準的なコンポーネントになっていく可能性がある。
