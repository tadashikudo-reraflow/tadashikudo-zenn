---
title: "Claude Code の memory system 完全ガイド——会話をまたいで文脈を保持する設計"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "開発効率", "生産性"]
published: true
---
# Claude Code の memory system 完全ガイド——会話をまたいで文脈を保持する設計

## はじめに

Claude Code を日常的に使っていると、「同じ説明を毎回繰り返している」と感じることはないでしょうか。プロジェクト構成、好みのコーディングスタイル、過去に叱られた失敗パターン——こうした文脈はセッションをまたいで持ち越せないと生産性に直結します。

Claude Code には、会話をまたいで文脈を保持するための **memory system** が組み込まれています。しかしドキュメントは断片化しており、「`CLAUDE.md` と `memory/` ディレクトリの使い分けがよく分からない」という声を筆者もよく耳にします。

本記事では、3層で構成される memory system の全体像と、実際に運用するうえでの設計パターンを整理します。対象は以下のような方です。

- Claude Code を導入したばかりで、設定方法の全体像を押さえたい
- `CLAUDE.md` をすでに使っているが肥大化して管理に困っている
- 複数プロジェクトを横断する知識の持たせ方を検討している

## memory system の3層構造

Claude Code の memory system は、優先度の異なる3つのレイヤーで構成されます。

| レイヤー | 場所 | 役割 | 適用範囲 |
|---------|------|------|---------|
| Global | `~/.claude/CLAUDE.md` | すべてのプロジェクトに共通するルール | 全セッション |
| Project | `<repo>/CLAUDE.md` | リポジトリ固有のルール | 当該リポジトリ |
| Local | `<repo>/.claude/CLAUDE.local.md` | 個人用・gitignore 対象 | 当該リポジトリ×個人 |

優先順位は **Global < Project < Local** で、下位レイヤーが上位を上書きします。これは git config と同じモデルで、「全体の方針は global、プロジェクト固有は project、試験的な個人設定は local」という分離が可能です。

さらに、Claude Code はセッション開始時に自動で以下を読み込みます。

- 起動ディレクトリから親方向にたどって見つかる `CLAUDE.md` 全て
- `~/.claude/CLAUDE.md`
- 各 `CLAUDE.md` が `@filepath` で参照する外部ファイル

この「自動読み込み」が memory system の根幹です。つまり `CLAUDE.md` は「毎回読まれるシステムプロンプト拡張」として機能します。

## CLAUDE.md の設計原則

`CLAUDE.md` を効果的に使う鍵は **短く保ち、詳細は外出しする** ことです。筆者は以下の構造で運用しています。

```
~/.claude/
├── CLAUDE.md              # 絶対ルール・最重要ポリシーのみ（150行以内）
├── docs/
│   ├── workflow-orchestration.md   # 詳細ワークフロー
│   ├── skill-management.md         # スキル運用ルール
│   └── chrome-mcp-guide.md         # MCP 使い分け
└── agents/                # サブエージェント定義
```

`CLAUDE.md` 本体は以下のような参照ポインタにとどめます。

```markdown
## [I] API課金ゲート

外部LLM APIを呼ぶ前に必ず確認を挟む。
詳細フォーマット: → `.claude/docs/workflow-orchestration.md`

## [G] Chrome MCP使用ガイドライン

URL取得の優先順: defuddle → WebFetch → Chrome MCP → Playwright
詳細（証跡チェック・誤用パターン）: → `.claude/docs/chrome-mcp-guide.md`
```

タグ `[I]` (Instruction = 絶対ルール) と `[G]` (Guidance = 推奨指針) を冒頭で凡例として定義しておくと、Claude 側が「違反すれば即停止すべき」「状況判断可」を区別できます。これは筆者が実運用で最も効果を感じた工夫です。

## memory/ ディレクトリによる運用知識の蓄積

`CLAUDE.md` は「常に適用されるルール」ですが、「過去に起きた失敗の記憶」や「進行中のプロジェクトの状態」は別の場所に置くのが定石です。筆者は以下の構造で運用しています。

```
memory/
├── MEMORY.md                         # インデックス + 横断最重要情報
├── working-memory.md                 # 未完了タスク・時系列ログ
├── feedback-index.md                 # フィードバック全件インデックス
├── feedback_no_computer_use.md       # 個別フィードバック（恒久ルール）
├── feedback_api_key_binary_isolation.md
├── lessons.md                        # 失敗から得た教訓
├── skill-traces.md                   # スキル実行時のエラー・リトライ履歴
└── project_<name>.md                 # プロジェクト別の状態
```

### MEMORY.md は "インデックス" に徹する

`MEMORY.md` を肥大化させると読み込みコストが跳ね上がり、Claude の集中力も落ちます。150行を超えたら分割する、というルールで運用するのが有効です。

```markdown
# Claude Memory

> **役割分担**: PJ詳細 → `memory/<pj-name>.md` / ツール設定 → `tools-config.md`
> このファイルはインデックス + 最重要情報のみ。

## ユーザー
- プロフィール: `user_profile.md`
- 対応原則: `feedback_user_response_preferences.md`

## プロジェクトインデックス
| プロジェクト | 概要 | 詳細 |
|------------|------|------|
| vault-auto | ナレッジベース自動改善 | → `proj-vault.md` |
| x-agent    | SNS自動投稿             | → `proj-x-agent.md` |
```

### 自己改善ループを回す

memory system の真価は「一度指摘された失敗を二度繰り返さない」ことにあります。筆者は以下のトリガーを設定しています。

| トリガー | アクション |
|---------|-----------|
| 修正指示・方向転換 | `memory/feedback_<topic>.md` を新規作成 or 更新 |
| ツールエラー / リトライ | `memory/skill-traces.md` に追記 |
| 重要な設計判断 | `02_Knowledge/decisions/` に ADR 形式で記録 |
| `MEMORY.md` が 150 行超 | `/compact-memory` で圧縮 → topic file に分離 |

`CLAUDE.md` から `@memory/feedback-index.md` のように参照すれば、過去のフィードバックがセッション開始時に自動で読まれます。

## import 構文と読み込み制御

Claude Code の `CLAUDE.md` は `@<path>` 構文で他ファイルを import できます。これは単なる参照ではなく、実ファイルとして読み込まれます。

```markdown
# プロジェクトルール

@./docs/architecture.md
@./memory/feedback-index.md

## ローカル固有ルール
（以下、本ファイル固有の内容）
```

ただし無制限に import すると起動時のトークン消費が増えます。設計指針としては次のとおりです。

- **常に必要な情報** → `CLAUDE.md` に直接書くか `@` で import
- **トリガーがあるときだけ必要** → ポインタのみ書いて本文では参照しない
- **過去ログ・長文の詳細** → ポインタで参照、明示的に指示されたときだけ読ませる

実際の運用では、`CLAUDE.md` は 100〜200 行に収め、詳細は `.claude/docs/` や `memory/` に散らし、ポインタ形式で参照するのが最もコスト効率が良いと感じます。

## 複数プロジェクトをまたぐ知識管理

仕事で複数プロジェクトを横断する場合、以下のパターンが実用的です。

```
~/workspace/
├── project-a/
│   ├── CLAUDE.md        # project-a 固有のルール
│   └── memory/          # project-a の作業ログ
└── project-b/
    ├── CLAUDE.md
    └── memory/

~/.claude/
├── CLAUDE.md            # 全プロジェクト共通ルール
└── memory/
    └── cross-project-lessons.md  # 横断的な教訓
```

「project-a で失敗して覚えた教訓」を `~/.claude/memory/cross-project-lessons.md` に昇格させれば、project-b でも自動適用されます。

## スクリプトから memory を更新する

memory を「書き込まれるもの」として扱うと、自動化の余地が広がります。たとえば次のようなヘルパースクリプトを置いておくと、Bash ツール経由で Claude 自身が記録できます。

```python
# ~/workspace/scripts/record_feedback.py
import sys, datetime, pathlib

MEMORY_DIR = pathlib.Path.home() / ".claude" / "memory"

def main():
    topic = sys.argv[1]
    body = sys.stdin.read()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    target = MEMORY_DIR / f"feedback_{topic}.md"
    with target.open("a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n\n{body}\n")
    print(f"Recorded: {target}")

if __name__ == "__main__":
    main()
```

Claude 側から以下のように呼ばせます。

```bash
echo "PRサイズは300行以内に収める。超える場合は分割を提案" \
  | python3 ~/workspace/scripts/record_feedback.py pr_size_limit
```

スクリプトと `CLAUDE.md` のトリガールール（「修正指示を受けたら `record_feedback.py` を呼ぶ」）を組み合わせることで、自己学習ループが完成します。

## よくある落とし穴

最後に、運用で筆者が踏んだ罠を3つ共有します。

1. **`CLAUDE.md` を肥大化させすぎて Claude が指示を忘れる**: 冒頭の「絶対ルール」を優先的に見るよう、章立てと `[I]` `[G]` タグで可視化する
2. **`memory/` に秘密情報をコミットしてしまう**: API キー・メールアドレス・社名は `memory/` にも書かない。機械スキャンで弾くフックを用意する
3. **`CLAUDE.md` と `memory/feedback-index.md` の二重管理**: どちらが正本か決めて、もう一方はポインタに留める。両方に同じルールを書くと片方だけ古くなり破綻する

## まとめ

- Claude Code の memory system は **Global / Project / Local** の3層構成で、優先順位は下位が上位を上書きする
- `CLAUDE.md` は**短く保ち**、詳細は `.claude/docs/` や `memory/` に外出しする
- `memory/` は **インデックス + topic file 分割** で 150 行制約を守る
- 失敗・フィードバックは **`feedback_<topic>.md`** に記録し、`feedback-index.md` から参照する
- `@<path>` import と「ポインタ参照」を使い分け、起動時トークンを無駄にしない

memory system は「一度設定して終わり」ではなく、運用しながら育てていく仕組みです。まずは `~/.claude/CLAUDE.md` に10行のルールを書くところから始めて、気付きがあるたびに `memory/` へ蓄積していく——そのサイクルが回り始めると、Claude Code はあなた専用のパートナーに近づいていきます。
