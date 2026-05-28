---
title: "ObsidianとClaude Codeで作る「第二の脳」——Vault自動管理の全体設計"
emoji: "🤖"
type: "tech"
topics: ["Obsidian", "ClaudeCode", "AI", "PKM", "知識管理"]
published: false
---
# ObsidianとClaude Codeで作る「第二の脳」——Vault自動管理の全体設計

## はじめに

「ノートを書いたのに、あとで見つからない」  
「情報は溜まっているのに、活用できていない」

こうした悩みを抱えながらObsidianを使っているエンジニアは多いと思います。Obsidianはリンクベースのナレッジグラフが強力ですが、**ノートの整理・検索・連携を手動でやるのは継続が難しい**という側面もあります。

本記事では、筆者が実際に運用している「ObsidianとClaude Codeを組み合わせたVault自動管理システム」の全体設計を紹介します。AIエージェントをナレッジ管理の「執事」として活用することで、Vaultが勝手に整理されていく仕組みです。

---

## システムの全体像

```
ObsidianVault (Gitリポジトリ)
├── 00_Inbox/          ← 未整理メモの一時置き場
├── 01_Projects/       ← アクティブなプロジェクト
├── 02_Knowledge/      ← 永続参照ノート
├── Daily Notes/       ← 日次ログ (YYYY/MM/YYYY-MM-DD.md)
└── memory/            ← AIエージェント用メモリ
    ├── MEMORY.md      ← セッション横断インデックス
    └── pj*.md         ← 各PJ状態ファイル

.claude/
├── CLAUDE.md          ← AIへの運用ルール
├── agents/            ← 専門エージェント定義
├── skills/            ← 再利用可能なスキル群
└── scheduled-tasks/   ← 定期実行タスク
```

重要なのは、**VaultがGitリポジトリとしてClaudeの作業領域にもなっている**点です。Claude Codeはファイルを読み書きし、変更をコミット・プッシュするまで一貫して行います。

---

## 設計の核心：AIへの「お作法」を明文化する

### CLAUDE.mdによる行動ルール定義

Vaultのルートに置く`CLAUDE.md`は、Claude Codeへの指示書です。ここに以下を明記します。

```markdown
## 絶対ルール

- ObsidianVault内のファイルを変更したら確認なしにコミット＆プッシュ
- 03_Archive/ は検索対象外
- 言語は日本語。出力は見出し+箇条書き+テーブル

## 検索の優先順

1. 特定ファイル名・正確なキーワード → Grep/Glob（高速）
2. 概念・文脈検索 → ベクトル検索MCPを使う
```

この「お作法ファイル」があることで、AIは毎回指示しなくても正しく動作します。

### memory/MEMORY.mdによる状態保持

Claudeはセッションをまたいで記憶を持ちません。しかし`memory/MEMORY.md`にインデックスを作り、詳細を`memory/pj-xxx.md`に分散させることで、**セッション開始時にロードするだけで文脈が復元**されます。

```markdown
# Claude Memory

## 進行中のプロジェクト

| PJ | 概要 | 詳細 |
|----|------|------|
| PJ-A | 〇〇SaaS開発 Next.js + PostgreSQL | → `pj-a.md` |
| PJ-B | コンテンツ自動化パイプライン | → `pj-b.md` |
```

このパターンの利点は：
- MEMORY.mdは200行以内に収める（常にコンテキスト内に載せる）
- 詳細は別ファイルに委譲（必要時にReadで取得）
- working-memory.mdで未完了タスクを時系列管理

---

## 実装：定期実行タスクでVaultを自律管理

### scheduled-tasksの仕組み

Claude Codeには`mcp__scheduled-tasks__*`というMCPがあり、cron形式で定期タスクを登録できます。

```json
{
  "name": "vault-daily-review",
  "schedule": "0 22 * * *",
  "model": "sonnet",
  "prompt": "本日のDaily Noteを作成し、00_Inboxの未整理ノートを適切なフォルダに振り分けてください。"
}
```

筆者の環境では以下のタスクが毎日自動実行されています：

| タスク | 実行時刻 | 内容 |
|--------|---------|------|
| morning-briefing | 07:00 | ニュース要約 + 今日のタスク確認 |
| inbox-cleanup | 22:00 | 00_Inbox整理 + Daily Note作成 |
| rag-refresh | 00:00 | ベクトルインデックス更新 |

### ベクトル検索MCP（vault-rag）の統合

Obsidian標準の検索はキーワードマッチです。概念検索には`sqlite-vec + bge-m3`によるベクトル検索を追加します。

```python
# vault-rag MCP サーバー（簡略版）
from fastmcp import FastMCP
import sqlite_vec

mcp = FastMCP("vault-rag")

@mcp.tool()
async def search_vault(query: str, top_k: int = 5) -> list[dict]:
    """意味的に近いノートを検索する"""
    embedding = embed_text(query)  # bge-m3でエンベディング
    results = db.execute(
        "SELECT path, content, distance FROM chunks ORDER BY embedding <-> ? LIMIT ?",
        [embedding, top_k]
    ).fetchall()
    return [{"path": r[0], "snippet": r[1][:200]} for r in results]
```

Claude Codeはこのツールを「概念・文脈検索」の場面で自動的に使います。

```
# CLAUDE.mdでの指示
検索の優先順:
1. 特定ファイル名・正確なキーワード → Grep/Glob
2. 概念・文脈検索 → mcp__vault-rag__search_vault（Grepより先に使う）
```

---

## 実際の運用パターン

### パターン1：セッション開始時の自動ロード

`.claude/hooks/`にpost-startフックを設定し、セッション開始時に自動でコンテキストをロードします。

```bash
#!/bin/bash
# .claude/hooks/post-start.sh
cat memory/MEMORY.md
cat memory/working-memory.md
echo "=== 本日のDaily Note ==="
cat "Daily Notes/$(date +%Y)/$(date +%m)/$(date +%Y-%m-%d).md" 2>/dev/null || echo "(未作成)"
```

### パターン2：作業ログの自動コミット

VaultはGitリポジトリなので、Claude Codeがファイルを変更するたびに自動コミットされます。

```bash
# .git/hooks/post-commit（自動プッシュ）
#!/bin/bash
git push origin main --quiet &
```

これにより、AIが行った変更がすべてGitヒストリーに残ります。「昨日AIが何をしたか」を`git log`で確認できます。

### パターン3：エージェント分業体制

複雑なタスクは専門エージェントに分業させます。

```yaml
# .claude/agents/ceo.md
name: ceo
description: 全PJ統括・進捗管理・日次レポート生成
model: sonnet
system_prompt: |
  あなたはObsidianVaultを管理するCEOエージェントです。
  全プロジェクトの進捗を横断的に確認し、優先度を判断します。
```

```yaml
# .claude/agents/news.md  
name: news
description: AIニュース収集・要約・Daily Note記録
model: sonnet
```

---

## 設計時のポイント

### 1. AIルールは「人格」で表現する

「〜するな」と禁止事項を増やすより、「シニアエンジニアとして振る舞う」「根本原因を突き止めることに価値を置く」といった人格定義の方が、予期しない状況でも適切に対応します。

### 2. メモリは「インデックス」と「詳細」を分離する

MEMORY.mdはインデックスのみ（200行以内）。詳細は`pj-xxx.md`に分散。セッション開始コストを抑えつつ、必要時は詳細をロードできます。

### 3. 変更は必ずGitで追跡する

AIが自律的に動くほど「AIが何をしたか」の追跡が重要になります。Gitヒストリーがあれば、意図しない変更もすぐ気づけます。

---

## まとめ

ObsidianとClaude Codeを組み合わせた「第二の脳」の核心は以下の5点です：

- **CLAUDE.mdで行動ルールを明文化**する（毎回指示しない）
- **memory/MEMORY.mdで状態を永続化**する（セッションをまたいだ文脈保持）
- **scheduled-tasksで定期メンテを自動化**する（Vaultが勝手に整理される）
- **ベクトル検索MCPで概念検索を実現**する（キーワードに依存しない検索）
- **Gitで全変更を追跡**する（AI自律動作の安全弁）

この設計の最大の利点は、**ノート管理に時間を使わなくなること**です。書くことに集中し、整理・検索・連携はAIに任せる。その結果、Vaultが本当の意味で「第二の脳」として機能し始めます。

ぜひ自分のObsidian環境に取り入れてみてください。
