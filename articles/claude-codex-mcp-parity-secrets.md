---
title: "Claude × Codex 並走設計：MCPパリティとシークレットハンドリングの落とし穴"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "Codex", "AI", "MCP", "設計"]
published: false
---
# Claude × Codex 並走設計：MCPパリティとシークレットハンドリングの落とし穴

## はじめに

Claude Code と Codex CLI を **同じプロジェクトで並走** させる構成は、いま AI コーディングの現場でかなり一般的になってきた。Claude をマスター、Codex を「重い実装の並列ワーカー」や「セカンドオピニオン」として使い分けるパターンだ。

ところがいざ運用に入れると、片方では自動で効いていた安全ガードが、もう片方では **完全に素通りする** という状況にハマる。筆者の環境でも「Claudeでは絶対に止まったはずの動作が、Codex経由だとそのまま進んでしまった」という事故を何度か経験した。

この記事では、両者を並走させるときに必ず潰しておくべき2つの観点 —— **MCPパリティ** と **シークレットハンドリング** —— に絞って、具体的な落とし穴と設計パターンをまとめる。

## 落とし穴1: SessionStart / Hook が継承されない

Claude Code には `SessionStart` Hook や `UserPromptSubmit` Hook、PostCompact Hook、auto-memory、MCP の自動接続といった「会話を開く前に効くガード」が複数ある。`CLAUDE.md` を読み込ませる、API課金ゲートを宣言させる、Vault の memory を再注入する、といった処理がここで走る。

Codex 側にも実験的な Hooks 機構はあるが、

- `PreToolUse` / `PostToolUse` が **Bash 中心**（Write / WebSearch / MCP を完全には捕捉しない）
- `AGENTS.md` は読み込むが、`AI-AGENT.md` のような中立正本ファイルや `memory/MEMORY.md` は **明示的に Read しない限り効かない**

という違いがある。結果として「Claude では `feedback_*.md` が auto-memory で効いて回避できたはずの NG 行動」が、Codex 経由だとそのまま実行される。

### 対策: 「Codexは AGENTS.md だけで足りる」と思わない

Codex セッション開始時、もしくは Codex worker サブエージェントの SKILL 内で、以下を **明示 Read** する設計に倒す。

```bash
# Codex worker SKILL.md の冒頭に置く明示ロード
cat AI-AGENT.md                 # 中立正本
cat memory/MEMORY.md             # PJ index
cat memory/feedback-index.md     # 直近の NG パターン一覧
# 対象PJに紐づくfeedback / pjメモがあれば追加で読む
```

スキル前段で `AI-AGENT.md` → `MEMORY.md` → 関連 `memory/feedback_*.md` を **必ず Read** させると、Claude側の auto-memory に擬似的なパリティが取れる。

## 落とし穴2: MCP 露出の非対称

Claude Code に vault-rag / code-rag / Slack / Notion / Drive など多数の MCP を接続していても、**Codex 側に同じ MCP が登録されていなければ、Codex はその知識にアクセスできない**。

ありがちな事故:

- Claude では `mcp__vault-rag__search_vault` で過去判断（ADR）を引いていた
- Codex worker に「同じ調査をやって」と委譲した
- Codex 側に vault-rag が露出していない → 一般知識で回答 → **過去のADRと矛盾する設計を提案**

### 対策: MCP マップを「両ランタイムで」棚卸しする

```bash
# Claude 側で接続中のMCPサーバーを列挙
ls ~/.claude/mcp-servers/ 2>/dev/null
cat ~/.claude/.mcp.json | jq '.mcpServers | keys'

# Codex 側
cat ~/.codex/config.toml | grep -A2 '\[mcp_servers'
```

両ランタイムを並べて差分を確認し、**Codex 側に欠けている重要 MCP を追加** するか、欠けたまま運用するなら「Codex はこの調査タスクには使わない」とスキル定義に明記する。曖昧な「とりあえず Codex に投げる」が一番事故る。

## 落とし穴3: シークレットの環境変数フォールバック

Claude / Codex 双方で API を叩くスクリプトを書くとき、**fail-open なフォールバック** を書きがち。

```python
# NG: 認証用途で空文字フォールバックは fail-closed に違反
api_key = os.environ.get("SERVICE_API_KEY", "")
client = SomeClient(api_key=api_key)  # 空文字で初期化 → 認証なし呼び出しが通る経路を作る
```

これは「環境変数が無いマシンで動かしたとき」に **無認証アクセス** が成立してしまう構造で、過去に課金事故と公開エンドポイントの認証バイパスを同時に踏んだ事例がある。

### 対策: 認証用キーは fail-closed で初期化する

```python
import os
import sys

def require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"[FATAL] env var {name} is not set\n")
        sys.exit(1)
    return v

api_key = require_env("SERVICE_API_KEY")
client = SomeClient(api_key=api_key)
```

加えて、**クライアント配布バイナリ・モバイルアプリ・公開リポ** には API key を絶対に埋め込まない。バイナリは抽出される前提で設計し、認証はバックエンド経由に集約する。

## 落とし穴4: Codex Companion Task の「発火元不明」問題

Claude Code 側のプラグインから Codex セッションに **自動投入される Companion Task** がある。プラグイン由来の `<task>` / `<structured_output_contract>` などのテンプレ入力が、通常のユーザー入力に見える形で流入し、**ユーザーが指示していないコード変更や commit まで進む** ことがある。

### 対策: 発火元確認をスキル化する

Codex 側で、以下のテンプレ形状を検知したら **必ず** 発火元 Job を確認させる。

```bash
# プラグイン由来Job のローカル痕跡
ls ~/.claude/plugins/data/codex-inline/state/*/jobs/ 2>/dev/null
# Codex セッションログ（originator確認）
grep -l '"originator": *"Claude Code"' ~/.codex/sessions/*.jsonl 2>/dev/null
```

ユーザーが明示していない自動投入は、**外部公開・DB書き込み・commit・push を含むタスクの前で必ず手動確認** に倒す。

## まとめ

Claude × Codex の並走設計で必ず潰すべきポイント:

- **SessionStart 相当を Codex 側で明示再現する**: `AI-AGENT.md` / `memory/MEMORY.md` / `feedback-index.md` をスキル前段で Read
- **MCP 露出の差分を棚卸しする**: 両ランタイムの MCP リストを並べて、欠けた MCP を埋めるか「使わない」と明記
- **認証キーは fail-closed**: `os.environ.get(KEY, "")` の空文字フォールバック禁止、`require_env` パターンに統一
- **クライアントバイナリに API key を埋め込まない**: 抽出される前提でバックエンド集約
- **プラグイン由来の自動投入タスクは発火元確認**: `<task>` / `<structured_output_contract>` テンプレは要警戒

Claude と Codex は「同じことができる兄弟」ではなく、**ガードの密度が違う別ランタイム** だと割り切ったほうが事故が減る。並走させるなら、弱い方に合わせて明示的にパリティを設計するのが結局いちばん速い。
