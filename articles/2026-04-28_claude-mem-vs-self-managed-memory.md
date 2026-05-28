---
title: "67kスター「claude-mem」は本当に必要か？Claude Code自前メモリ管理の3層構造で代替する"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "メモリ管理", "Obsidian", "自動化"]
published: false
---
# 67kスター「claude-mem」は本当に必要か？Claude Code自前メモリ管理の3層構造で代替する

## はじめに

Claude Codeを長く使っていると、「セッションを跨いだ記憶」をどう管理するかが必ず課題になる。

GitHubで人気の `claude-mem` は、SQLiteベースで会話履歴・タスク・知識をベクトル検索可能な形で管理してくれる優れたツールだ。が、筆者は**あえて導入していない**。

理由はシンプルで、`claude-mem` が解決しようとしている問題のほとんどは、**Claude Code純正の `CLAUDE.md` + 自前のMarkdownファイル群 + フックの3層構造で十分カバーできる**からだ。

本記事では、外部依存を増やさずに「セッションを跨いで賢く動くClaude Code」を構築する具体的な方法を共有する。

## 3層構造の全体像

筆者の環境では、メモリを以下の3層に分けている。

| 層 | 配置先 | 役割 |
|---|---|---|
| **Layer 1: グローバルルール** | `~/.claude/CLAUDE.md` | 全プロジェクト共通の絶対ルール |
| **Layer 2: プロジェクトルール** | `<project>/CLAUDE.md` | リポジトリ固有の規約・パス |
| **Layer 3: 自動メモリ** | `~/.claude/projects/<hash>/memory/*.md` | 会話を跨いで成長する事実・好み |

Layer 1 / Layer 2 は Claude Code が自動でロードする。Layer 3 は `MEMORY.md` をインデックスにして、必要なときだけRead される設計だ。

```
~/.claude/
├── CLAUDE.md                    # Layer 1
└── projects/<project-hash>/
    └── memory/
        ├── MEMORY.md            # Layer 3 のインデックス
        ├── user_profile.md
        ├── feedback_xxx.md
        └── project_xxx.md
```

## Layer 1: グローバルルール（短く保つ）

`~/.claude/CLAUDE.md` は**全セッションで毎回フルロードされる**。長くすると毎ターンの先頭に重いコンテキストが乗るので、公式推奨の400行以下、実運用では160〜180行を目標にする。

ここに書くのは「絶対に破ってはならないルール」だけ。

```markdown
## 絶対ルール

### [I] API課金ゲート
外部LLM APIを呼ぶ前に必ず確認を挟む。

### [I] モデルポリシー
- デフォルト: Sonnet
- Opus自動昇格禁止: 明示指示なしにOpusへ切り替えない

### [I] 危険コマンドの禁止
- `git add -A` / `git add .` 禁止 → ファイル名個別指定のみ
```

詳細手順は `~/.claude/docs/` 配下の個別ドキュメントに分け、グローバルからはリンクだけ張る。これでグローバルが太らない。

## Layer 2: プロジェクトルール（adapter として書く）

プロジェクトルートの `CLAUDE.md` は、リポジトリ固有の知識を書く場所。

筆者は「中立な正本ファイル」と「Claude Code固有の差分ファイル」を分けて、CursorやCodexにも流用できる構成にしている。

```markdown
# CLAUDE.md - <Project>

> 優先度: グローバル > 中立正本 > このファイル（Claude adapter）

## Claude固有差分

| イベント | 検知条件 | 書き込み先 |
|---|---|---|
| 設計判断 | `.claude/agents/` への書き込み | `decisions/` にADR形式で記録 |
| スキル完了 | Skill tool 実行完了 | `skills-usage.json` に追記 |
```

「中立 + adapter」の分離は、将来別のCLIに乗り換えるときに差分だけ書き換えれば済む。ベンダーロックイン回避策としても有効だ。

## Layer 3: 自動メモリ（インデックス + トピックファイル）

ここが `claude-mem` と最も比較される部分。Claude Code は `~/.claude/projects/<hash>/memory/MEMORY.md` を自動でロードする仕様がある（**200行で打ち切り**）。

筆者はこれを**インデックスとして使い、本文は別ファイルに分散**させている。

```markdown
# Claude Memory - <Project>

> 役割分担: PJ詳細 → memory/<pj-name>.md / フィードバック → feedback-index.md

## ユーザー
- [プロフィール](user_profile.md) — 役職・スキル
- [対応原則](feedback_user_response_preferences.md) — 自律完結・確認ゲート

## PJインデックス
| PJ | 概要 | 詳細 |
|---|---|---|
| PJ-A | SaaSプロダクト | → pj-a.md |
| PJ-B | 自動化基盤 | → pj-b.md |

## フィードバック（最重要のみ）
- 危険コマンド禁止 → feedback_dangerous_commands.md
- APIキー埋め込み禁止 → feedback_no_hardcoded_keys.md
```

各トピックファイルには frontmatter を付けて type で分類する。

```markdown
---
name: user_response_preferences
description: ユーザーが好む応答スタイル
type: feedback
---

## ルール
- 即断・即実行を好む。確認は重要な分岐のみ
- 説明は箇条書き優先
```

**ポイントは「`MEMORY.md` を200行以内に保つ」こと。** 超えたらトピックファイルに切り出して、インデックスからリンクで張る。

## 自動更新フック：失敗から学ぶ仕組み

`claude-mem` の真の強みは「会話から自動で学習する」点にある。これも Claude Code の hooks で代替できる。

`~/.claude/settings.json`：

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/track_edits.py"
          }
        ]
      }
    ]
  }
}
```

`track_edits.py`：

```python
import json, sys, os
from datetime import date

payload = json.load(sys.stdin)
file_path = payload.get("tool_input", {}).get("file_path", "")

if "CLAUDE.md" in file_path or ".claude/agents/" in file_path:
    adr_dir = os.path.expanduser("~/projects/myrepo/decisions/")
    today = date.today().isoformat()
    with open(f"{adr_dir}/{today}_auto.md", "a") as f:
        f.write(f"- {file_path} を更新\n")
```

これだけで「設計判断を勝手にログ化する」運用が立ち上がる。専用DBは不要。

## claude-mem vs 自前管理：トレードオフ

| 観点 | claude-mem | 自前3層 |
|---|---|---|
| セットアップ | 即完了 | 30分 |
| ベクトル検索 | あり | なし（grep / Glob 代替） |
| 透明性 | DBブラックボックス | Markdown + git log で追える |
| バージョン管理 | DB管理が必要 | git で完結 |
| 他CLIへの流用 | claude-mem 依存 | Cursor / Codex でも読める |

**ベクトル検索が本当に必要なケース**（数千件規模のメモを横断検索）では `claude-mem` の方が楽。だが、個人開発〜中規模なら、grepとMarkdownリンクで十分回る。筆者環境ではLayer 3が約60ファイル・4,000行だが、`grep -r` と `MEMORY.md` のインデックスで困っていない。

## まとめ

- **claude-mem を入れる前に、`CLAUDE.md` × 3層構造で代替できないか検討する**
- **Layer 1（グローバル）は短く保つ**：160〜180行が実用解
- **Layer 2（プロジェクト）は中立正本 + adapter に分割**：他CLIへの移行コストが下がる
- **Layer 3（自動メモリ）は `MEMORY.md` をインデックス化**：200行制限を意識して分散
- **hooks で「失敗から学ぶ」を自動化**：PostToolUseでフィードバックを自動生成
- **Markdown + git の透明性**は、長期運用で効いてくる

便利なツールを入れる前に、一度「素のClaude Codeをどこまで使い倒せるか」を試してみてほしい。
