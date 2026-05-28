---
title: "Claude Code のライフサイクルフック入門 — セッションの「前後」を自動化する"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "開発効率", "自動化"]
published: false
---
# Claude Code のライフサイクルフック入門 — セッションの「前後」を自動化する

## はじめに

Claude Code を使い始めると、「毎回セッション開始時に同じ前置き作業をしている」「ツールが実行される前に自動でチェックを挟みたい」という場面に遭遇する。そのニーズに応えるのが**ライフサイクルフック（Lifecycle Hooks）**だ。

フックを使うと、Claude Code の内部イベント（セッション開始・ツール実行前後・コンパクト前後など）に対してシェルコマンドやプロンプトインジェクションを差し込める。CI のフックや Git フックと概念は同じだが、AI エージェントのワークフローに組み込まれている点が特徴だ。

本記事では、フックの設定方法・利用可能なイベント種別・実践的なユースケースを順に解説する。

---

## フックの種類と発火タイミング

2025年時点で利用可能なフックイベントは以下の通り。

| イベント | 発火タイミング |
|---------|--------------|
| `SessionStart` | Claude Code のセッションが開始されたとき |
| `Stop` | Claude Code がユーザーへの応答を停止するとき |
| `PreCompact` | コンテキストの圧縮（`/compact`）が実行される直前 |
| `PostCompact` | コンテキスト圧縮が完了した直後 |
| `UserPromptSubmit` | ユーザーがプロンプトを送信したとき |
| `PreToolUse` | ツール（Bash, Read, Edit など）が実行される直前 |
| `PostToolUse` | ツール実行が完了した直後 |

各フックは `type` として `command`（シェルコマンド実行）か `prompt`（プロンプト差し込み）を選べる。

---

## 設定ファイルの基本構造

フックは `~/.claude/settings.json`（グローバル）またはプロジェクト直下の `.claude/settings.json`（プロジェクトローカル）に記述する。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/session-start.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/hooks/bash-guard.sh",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

### キー項目の説明

| キー | 必須 | 説明 |
|-----|------|------|
| `type` | ✓ | `command` or `prompt` |
| `command` | `type=command` 時 | 実行するシェルコマンド |
| `prompt` | `type=prompt` 時 | Claude に差し込むプロンプトテキスト |
| `timeout` | — | タイムアウト秒数（デフォルト30秒） |
| `matcher` | — | PreToolUse/PostToolUse でツール名を絞り込む |

### matcher の使い方

`PreToolUse` と `PostToolUse` では `matcher` でフックを絞り込める。

```json
{
  "matcher": "Bash"
}
```

`matcher` に MCP ツール名も指定できる。

```json
{
  "matcher": "mcp__some-mcp__some_tool"
}
```

`matcher` を省略すると **全ツールに適用**される。重いチェックを全ツールに付けるとパフォーマンスに影響するので注意。

---

## フックの終了コードとブロック

`command` フックの終了コードには意味がある。

| 終了コード | 動作 |
|-----------|------|
| `0` | 通常続行 |
| `1` | エラーログ（続行は継続） |
| `2` | **ブロック**（ツール実行または応答を中止） |

終了コード `2` を使うと「この Bash コマンドは実行禁止」という安全装置を作れる。

---

## 実践例 1: 危険なコマンドをブロックする（PreToolUse）

`rm -rf`・`git push --force origin main`・`DROP TABLE` などを検出してブロックする。

```bash
#!/bin/bash
# ~/.claude/hooks/bash-guard.sh

INPUT="$CLAUDE_TOOL_INPUT"

if echo "$INPUT" | grep -qE \
  'rm\s+-rf\s+[~/.]|DROP\s+(TABLE|DATABASE)|--force\s+origin\s+(main|master)|> /dev/sda'; then
  echo "BLOCKED: Dangerous command detected" >&2
  exit 2
fi

exit 0
```

settings.json への登録:

```json
"PreToolUse": [
  {
    "matcher": "Bash",
    "hooks": [
      {
        "type": "command",
        "command": "bash ~/.claude/hooks/bash-guard.sh",
        "timeout": 3
      }
    ]
  }
]
```

`CLAUDE_TOOL_INPUT` 環境変数にツールへの入力内容が JSON 文字列として渡される。Bash ツールの場合はコマンド文字列がここに入る。

---

## 実践例 2: セッション開始時にコンテキストを自動ロード（SessionStart）

毎回手動で「このプロジェクトは〜」と説明するのは非効率だ。`SessionStart` フックで必要なファイルを自動で読み込ませる。

```bash
#!/bin/bash
# ~/.claude/hooks/session-start.sh

# 直近のプロジェクト状況を標準出力に出力 → Claude のコンテキストに注入される
echo "=== Project Status ==="
echo "Today: $(date +%Y-%m-%d)"
echo ""

# 進行中タスクがあれば表示
if [ -f "$HOME/.claude/current-task.md" ]; then
  echo "=== Current Task ==="
  cat "$HOME/.claude/current-task.md"
fi
```

`SessionStart` フックの **標準出力はそのまま Claude のシステムプロンプトに注入**される。これを使えば日次情報・プロジェクト状態・ルールを毎回自動で差し込める。

---

## 実践例 3: 停止時に自動コミット（Stop）

`Stop` イベントで作業終了のタイミングを検知し、自動で git commit を走らせる。

```bash
#!/bin/bash
# ~/.claude/hooks/stop-check.sh

# 変更があればコミット
cd ~/workspace/myproject || exit 0
if ! git diff --quiet HEAD 2>/dev/null; then
  git add -p  # インタラクティブ add は無理なので差分ファイルを個別 add
  git commit -m "auto: session-end snapshot $(date +%H:%M)"
fi
```

注意: `Stop` フックは応答完了のたびに発火する。頻度が高い場合は `git diff --quiet` などで変更がないときはスキップするように実装する。

---

## 実践例 4: ユーザー入力をゲートする（UserPromptSubmit）

`UserPromptSubmit` はユーザーがプロンプトを送信する瞬間に発火する。フック側で特定のキーワードを検出して処理を差し込める。

```bash
#!/bin/bash
# ~/.claude/hooks/prompt-gate.sh

PROMPT="$CLAUDE_USER_PROMPT"

# 「本番」「production」が含まれていたら警告を標準エラーに出す
if echo "$PROMPT" | grep -qi '本番\|production\|prod'; then
  echo "⚠️ 本番環境への操作が含まれています。慎重に実行してください。" >&2
fi

exit 0
```

`CLAUDE_USER_PROMPT` 環境変数にユーザーのプロンプトテキストが渡される。

---

## 実践例 5: prompt 型フックで確認を挟む

特定ツールが呼ばれる前に Claude 自身に確認プロンプトを差し込むことができる。

```json
"PreToolUse": [
  {
    "matcher": "mcp__someservice__delete_resource",
    "hooks": [
      {
        "type": "prompt",
        "prompt": "deleteリソース操作が呼ばれようとしています。削除対象をユーザーに提示し、本当に削除してよいか確認を取ってください。承認された場合のみ続行してください。",
        "timeout": 30
      }
    ]
  }
]
```

`type: prompt` の場合、`command` の代わりに `prompt` フィールドにテキストを書く。Claude がそのテキストを受け取り、ユーザーへの確認ダイアログとして機能する。

---

## 実践例 6: コンパクト前にログを保存（PreCompact）

`/compact` でコンテキストが圧縮される前に、重要な情報をファイルに書き出しておく。

```bash
#!/bin/bash
# ~/.claude/hooks/pre-compact-save.sh

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOG_DIR="$HOME/.claude/compact-logs"
mkdir -p "$LOG_DIR"

# 現在の作業内容をメモ
echo "CompactAt: $TIMESTAMP" > "$LOG_DIR/last-compact.md"
echo "Project: $(pwd)" >> "$LOG_DIR/last-compact.md"
echo "Git status: $(git status --short 2>/dev/null)" >> "$LOG_DIR/last-compact.md"
```

---

## フック開発のベストプラクティス

### 1. timeout を必ず設定する

フックがハングすると Claude Code 全体が止まる。重い処理は非同期化し、`timeout` で打ち切りを設定する。

```json
{
  "type": "command",
  "command": "bash ~/.claude/hooks/my-hook.sh",
  "timeout": 5
}
```

### 2. stderr と stdout を使い分ける

- **stdout**: Claude のコンテキストに注入される（SessionStart のみ）
- **stderr**: ログ・エラーメッセージ（ユーザーには表示されるがコンテキストに入らない）

意図しないデータをコンテキストに混ぜないよう、ログは `>&2` で stderr に流す。

### 3. 環境変数の一覧

フック実行時に Claude Code から渡される主な環境変数:

| 変数 | 内容 |
|-----|------|
| `CLAUDE_TOOL_INPUT` | ツールへの入力（JSON文字列） |
| `CLAUDE_TOOL_NAME` | ツール名（PreToolUse/PostToolUse） |
| `CLAUDE_USER_PROMPT` | ユーザープロンプト（UserPromptSubmit） |
| `CLAUDE_SESSION_ID` | セッションID |

### 4. matcher は具体的に絞る

`PreToolUse` に `matcher` なしで重い処理を書くと、すべてのツール呼び出し（Read・Grep・Glob・Edit など）で毎回実行される。必ず `matcher` で対象ツールを絞ること。

---

## まとめ

Claude Code のライフサイクルフックを使いこなすと、以下のことが実現できる:

- **セキュリティ**: 危険なコマンドを `PreToolUse + exit 2` でブロック
- **自動化**: `SessionStart` でコンテキスト自動ロード、`Stop` で自動コミット
- **確認ゲート**: `type: prompt` で AI が自律的に確認ダイアログを挿入
- **ログ蓄積**: `PreCompact` / `PostCompact` でセッション状態を永続化

フックは小さなシェルスクリプトから始められるが、積み上げると Claude Code の振る舞いを大幅にカスタマイズできる。まずは `SessionStart` でよく使う情報を自動ロードするところから試してみよう。
