---
title: "Claude Code でコンテキストを枯渇させない3つの戦略——working-memory / PreCompact フック / 出力間引き"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "MCP", "コンテキスト管理", "設計"]
published: false
---
# Claude Code でコンテキストを枯渇させない3つの戦略——working-memory / PreCompact フック / 出力間引き

## はじめに

Claude Code を使い続けていると、必ず直面する問題がある。

**コンテキストウィンドウが満杯になる。**

長いコーディングセッションの中盤で `[context usage: 78%]` の警告が現れ始め、そのまま作業を続けると過去の指示が揮発し始める。最終的には `/compact` を打つか、新しいセッションを始めるしかなくなる。しかし、コンパクションをかけると「直前のエラーの原因」「未完了のタスク」「ブランチ名」……こういった**セッション間で引き継ぎたい情報**が失われる。

筆者はこの問題に対して3つの戦略を組み合わせて対処するようになった。

1. **working-memory パターン** — コンテキスト圧縮に先立ってメモリを構造化する
2. **PreCompact / PostCompact フック** — コンパクション前後を自動ハンドリングする
3. **出力間引き** — 大量テキストをサブエージェントに逃がしてメインコンテキストを守る

それぞれを実装例とともに紹介する。

---

## 戦略1: working-memory パターン

### 何をするか

コンテキストが増大する前に、「次のセッションに引き継ぎたい情報」を**構造化されたファイル**に書き出す。

よくある失敗パターンは「会話の中に情報が散らばった状態でコンパクションをかける」ことだ。Claude の `/compact` は会話をサマリーに圧縮するが、そのサマリーが何を保持するかはモデル任せであり、細かいスタック状態（変数名、エラーメッセージの原文、未コミットの変更）は失われやすい。

### 実装: working-memory.md

プロジェクトルートまたは `~/.claude/projects/<proj>/memory/` 以下に `working-memory.md` を配置し、セッション中に発生した重要情報を随時書き込む。

```markdown
# working-memory

## 進行中タスク
- [ ] `src/api/auth.ts` のJWT検証ロジック修正（deadline: 本セッション中）
- [x] `GET /users` のキャッシュ実装

## 直近のエラー
```bash
TypeError: Cannot read properties of undefined (reading 'userId')
  at src/middlewares/auth.ts:42
```
**原因**: `req.user` が undefined になるのは Passport の `passReqToCallback: true` 未指定
**暫定対処**: 型ガードを追加（根本修正は次セッションで Passport config を直す）

## 変更済みファイル
- `src/api/auth.ts` — JWT 有効期限を 24h → 1h に変更
- `src/config/passport.ts` — ★未コミット・要レビュー

## ブランチ
feature/auth-refactor（origin へ未 push）
```

このファイルを Claude に `Read` させるだけで、次のセッションは即座に「どこまで進んでいたか」を把握できる。

### Claude Code への組み込み

`~/.claude/CLAUDE.md` に以下を追加しておくと、`/compact` を打つ前に自動的にアラートが出る:

```
## compact保持指示
When compacting, always preserve:
未完了タスク・進行中PJの状態、変更済みファイルの一覧、
直近のエラーと対処方針、アクティブなブランチ名
```

`compact保持指示` のようなセクションを CLAUDE.md に書いておくと、Claude が `/compact` 実行時に自動的に参照し、保持すべき情報をサマリーに含めようとする。ただし**信頼度は100%ではない**。完全な引き継ぎには次の戦略と組み合わせる必要がある。

---

## 戦略2: PreCompact / PostCompact フック

### Claude Code Hooks の概要

Claude Code の `settings.json` には Hooks（フック）機能がある。ToolUse の前後やセッションの開始・終了時に任意のシェルコマンドを実行できる。

```json
// ~/.claude/settings.json (抜粋)
{
  "hooks": {
    "PreCompact": [
      {
        "hooks": [{
          "type": "command",
          "command": "bash ~/.claude/hooks/pre-compact-save.sh",
          "timeout": 10
        }]
      }
    ],
    "PostCompact": [
      {
        "hooks": [{
          "type": "command",
          "command": "bash ~/.claude/hooks/post-compact.sh",
          "timeout": 10
        }]
      }
    ]
  }
}
```

### PreCompact フック: コンパクション直前の状態を保存する

`PreCompact` は `/compact` が実行される**直前**に発火する。ここで作業状態を記録しておく。

```bash
#!/bin/bash
# ~/.claude/hooks/pre-compact-save.sh

MEMORY_DIR="$HOME/.claude/projects/-/memory"
COMPACT_LOG="$MEMORY_DIR/compact-log.md"
TIMESTAMP=$(date +%Y-%m-%dT%H:%M:%S)
CWD="${CLAUDE_WORKING_DIRECTORY:-$(pwd)}"

mkdir -p "$MEMORY_DIR"

{
  echo ""
  echo "## Compact: $TIMESTAMP"
  echo "- Working dir: $CWD"
  if git -C "$CWD" rev-parse --git-dir >/dev/null 2>&1; then
    echo "- Branch: $(git -C "$CWD" branch --show-current 2>/dev/null || echo 'N/A')"
    echo "- Uncommitted: $(git -C "$CWD" diff --stat --shortstat 2>/dev/null || echo 'none')"
  fi
} >> "$COMPACT_LOG"

# ログが肥大化しないよう 300 行超で末尾 150 行に切り詰め
LINE_COUNT=$(wc -l < "$COMPACT_LOG" | tr -d ' ')
if [ "$LINE_COUNT" -gt 300 ]; then
  tail -150 "$COMPACT_LOG" > "${COMPACT_LOG}.tmp"
  mv "${COMPACT_LOG}.tmp" "$COMPACT_LOG"
fi

echo "Pre-compact state saved"
```

このスクリプトが行うのは:
- コンパクション発生時刻とディレクトリの記録
- 現在の git ブランチと未コミット変更の記録
- ログの自動ローテーション（300行で上限）

### PostCompact フック: 重要な CLAUDE.md を再注入する

`PostCompact` はコンパクション完了**直後**に発火する。コンパクション後のコンテキストには CLAUDE.md の内容が再ロードされているが、プロジェクト固有の CLAUDE.md が失われることがある（特にサブディレクトリにある場合）。

```bash
#!/bin/bash
# ~/.claude/hooks/post-compact.sh

CWD="${CLAUDE_WORKING_DIRECTORY:-$(pwd)}"

echo "📋 PostCompact: コンテキストが圧縮されました。"

# CWD とその親ディレクトリを最大 4 階層まで遡って CLAUDE.md を探して再出力
dir="$CWD"
depth=0
found_any=0
while true; do
  for candidate in "$dir/.claude/CLAUDE.md" "$dir/CLAUDE.md"; do
    if [ -f "$candidate" ]; then
      echo "=== $candidate (compact後再ロード) ==="
      cat "$candidate"
      echo "=== END ==="
      found_any=1
    fi
  done
  [ $found_any -eq 1 ] && break
  [ "$dir" = "/" ] || [ "$dir" = "$HOME" ] && break
  [ $depth -ge 4 ] && break
  dir=$(dirname "$dir")
  depth=$((depth + 1))
done

echo "現在のPJ/タスク/未完了Todoを確認し、状況を一言報告してください。"
```

このアプローチのポイントは**フック出力が Claude のコンテキストに注入される**点だ。`post-compact.sh` が CLAUDE.md の内容を `cat` で出力すると、その内容がコンパクション後の最初のシステムメッセージとして機能する。プロジェクトルールや禁止事項が自動的に復元される。

### SessionStart フックとの組み合わせ

同様に `SessionStart` フックで working-memory.md を自動ロードすると、新しいセッション開始時に前回の状態が即座に注入される。

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{
          "type": "command",
          "command": "bash ~/.claude/hooks/session-start.sh"
        }]
      }
    ]
  }
}
```

```bash
#!/bin/bash
# session-start.sh
CWD="${CLAUDE_WORKING_DIRECTORY:-$(pwd)}"

# working-memory.md が存在すれば自動ロード
WMEM="$CWD/working-memory.md"
if [ -f "$WMEM" ]; then
  echo "=== working-memory.md (前回セッション引き継ぎ) ==="
  cat "$WMEM"
  echo "=== END ==="
fi
```

---

## 戦略3: 出力間引き（サブエージェント委譲）

### なぜ大量出力がコンテキストを圧迫するか

Claude Code がファイルを検索したり、コマンドを実行したりすると、その**出力全体がコンテキストに蓄積**される。大きな `git diff`、大量の `grep` 結果、長い `npm test` ログ……これらが積み重なると、あっという間に使用率が跳ね上がる。

### 実装パターン: Agent 委譲

調査・探索タスクは Agent ツールに委譲する。Agent はメインの Claude Code とは別のコンテキストウィンドウを持ち、結果だけを返してくる。

```python
# 悪い例: メインコンテキストに大量ログが蓄積する
result = bash("find ~/workspace -name '*.ts' -exec grep -l 'useAuth' {} \\;")
# → 数百行の出力がコンテキストに入る

# 良い例: Agent に探索を委譲し、サマリーだけ受け取る
result = agent(
  "workspace/pj/ 以下で useAuth を使っている .ts ファイルを全て列挙し、
   ファイルパスと使用箇所のサマリーだけを返してください。
   コードの中身は不要、一覧のみ。"
)
# → 簡潔な一覧だけが返ってくる
```

### 外部 CLI への委譲

さらに大きなタスクには外部 CLI を活用できる。

```bash
# Gemini CLI: 大量ドキュメントのリサーチ（1M コンテキスト活用）
gemini -p "以下のURLを全て読んで、${QUERY}についてまとめてください" \
  --yolo -m gemini-2.5-flash -o text

# Codex CLI: 大規模コード変換（メインコンテキスト温存）
codex exec --full-auto -m o3-mini -C /path/to/project \
  "src/ 以下の全 .js ファイルを .ts に変換し、型エラーを修正してください"
```

Claude Code 自身がオーケストレーターとして機能し、重い作業を外部に投げることでメインのコンテキストを「司令塔の思考」だけに使える。

### 出力量を絞るプロンプト習慣

Agent や Bash コマンドに指示する際に「出力量を絞る」プロンプトを意識的に使う。

```
# NG: 全部返してもらう
「このリポジトリの構造を調べて」
→ 数百ファイルの一覧が返ってくる

# OK: 必要な情報に絞る
「このリポジトリの src/ 直下のディレクトリ名だけ列挙してください（ファイル名不要）」
→ 10 行以内で返ってくる
```

---

## 3つの戦略を組み合わせた実践例

```
[セッション開始]
  SessionStart フック → working-memory.md を自動注入
  
[作業中]
  大量リサーチが発生 → Agent に委譲（戦略3）
  コンテキスト 70% 超 → working-memory.md を手動更新（戦略1）
  
[/compact 実行前]
  PreCompact フック → compact-log.md にブランチ・未コミット状態を記録（戦略2）
  
[/compact 実行後]
  PostCompact フック → CLAUDE.md を自動再注入（戦略2）
  → compact-log.md + working-memory.md を Read させて状態復元
  
[次セッション]
  SessionStart フック → working-memory.md から自動再開（戦略1+2の連携）
```

コンパクション後に Claude が「さっきまで何をやっていたか分からない」状態になるのは、**保存・復元のサイクルが設計されていない**からだ。この3つの戦略は、そのサイクルをシステムとして組み込む。

---

## まとめ

Claude Code のコンテキスト管理で押さえるべき実践ポイントは以下の5点だ。

- **working-memory.md を「状態スナップショット」として設計する** — 自然言語の作業メモではなく、機械的にパース可能な構造化フォーマットにする
- **PreCompact フックで Git 状態を記録する** — ブランチ名・未コミット変更は消えやすい情報。自動記録が最も確実
- **PostCompact フックで CLAUDE.md を再注入する** — コンパクション後の「ルール失念」を防ぐ最も効果的な手段
- **Agent 委譲の判断基準は「3ファイル以上の横断調査」** — それ以下なら直接 Read/Grep が速い
- **コンテキスト 70% でコンパクションのタイミングを計画する** — 90% を超えてから慌てて打つと有用な情報が失われる

コンテキストウィンドウは有限のリソースだ。設計して使わない限り、長いセッションは常にエントロピーとの戦いになる。
