---
title: "Codex CLI に Claude Code と同じ Vault 文脈を継承させる：Context Bootstrap の設計"
emoji: "🤖"
type: "tech"
topics: ["Codex", "ClaudeCode", "AI", "Obsidian", "設計"]
published: true
---
# Codex CLI に Claude Code と同じ Vault 文脈を継承させる：Context Bootstrap の設計

## はじめに

Claude Code と Codex CLI を同じ Obsidian Vault で併用していると、ある日決定的な事実に気づきます。

**「同じディレクトリで動かしているのに、両者の "知っていること" は全然違う」**

Claude Code は `CLAUDE.md` を読み、Hook で `MEMORY.md` を注入し、auto-memory で過去の修正指示を覚えています。一方の Codex CLI は `AGENTS.md` こそ読みますが、それ以外の Vault 共通ルールやプロジェクト記憶を**勝手には引き継ぎません**。

これを「Codex の手抜き」と捉えるのは設計判断として誤りです。正しくは、**ハーネスのコンテキスト注入レイヤを Codex 側でも明示的に設計しなければならない**ということです。

この記事では、筆者が運用する Vault（26 アクティブ PJ・1,100 ノート規模）で実際に組んだ「Codex 用 Vault Context Bootstrap」の設計を、Hook・Skill・運用プロトコルの 3 層で公開します。

---

## なぜ Codex は AGENTS.md だけでは足りないのか

そもそも「コンテキスト引き継ぎ」を Claude Code がどう実現しているかを分解すると、以下のレイヤに分かれます。

| レイヤ | Claude Code の実装 | 効果 |
|--------|--------------------|------|
| 1. 起動時注入 | `SessionStart` Hook + `CLAUDE.md` 自動ロード | Vault 共通ルールを毎回読む |
| 2. 圧縮復帰 | `PostCompact` Hook | コンパクト後も重要情報を保持 |
| 3. 入力ゲート | `UserPromptSubmit` Hook | 課金 API 呼び出し前に確認を強制 |
| 4. ツールゲート | `PreToolUse` / `PostToolUse` Hook | 危険コマンド・公開操作をブロック |
| 5. 永続記憶 | auto-memory（`memory/MEMORY.md` + `feedback_*.md`） | 過去の修正指示が次セッションで効く |
| 6. 意味検索 | `vault-rag` MCP（pgvector / sqlite-vec） | 概念ベースで過去ノートを引ける |

Codex CLI には `AGENTS.md` という adapter ファイルがあり、これは Codex 固有の差分を書く場所です。しかし `AGENTS.md` は **adapter** であって **Vault 共通ルールの正本ではありません**。

筆者の Vault では、共通ルールを `AI-AGENT.md` に分離し、`CLAUDE.md` と `AGENTS.md` はそれぞれの adapter として差分のみを定義しています。Codex がこの構造を理解せず `AGENTS.md` だけで「引き継ぎ完了」と判断すると、共通ルールも MEMORY も feedback も全部素通りします。

実際にこの構造の不整合で痛い目に遭いました。あるサービスのカバー画像生成を Codex に頼んだところ、過去に決めたカバー画像フローや生成物の格納ルールを完全に無視して、単発の画像生成として処理されたのです。Claude Code なら同じ依頼で `MEMORY.md` から関連 PJ メモを辿り、正規フローへ誘導していました。

---

## 設計方針：3 層で Claude のハーネスに「近づける」

完全同等は諦めます。Codex Hooks は実験的機能で、Bash 中心の `PreToolUse` / `PostToolUse` しか持たないため、MCP 呼び出しや Write 操作を細粒度で捕捉できません。

そのうえで、以下の 3 層で実用上の同等性を狙います。

```
┌──────────────────────────────────────────┐
│  Layer 1: Codex Hooks（自動・hooks.json）  │
│   SessionStart / UserPromptSubmit /       │
│   PreToolUse Bash / Stop                  │
├──────────────────────────────────────────┤
│  Layer 2: Skill（明示・vault-context-     │
│   bootstrap）— Hook 漏れの fallback        │
├──────────────────────────────────────────┤
│  Layer 3: 運用プロトコル（人間ルール）       │
│   Vault 内作業時に AGENTS.md だけで         │
│   完結したと見なさない                      │
└──────────────────────────────────────────┘
```

**重要なのはこの 3 層を「冗長」と見ないこと**です。Hooks は機構上の取りこぼしがあり、Skill は明示呼び出しが必要で、運用プロトコルは人間（または LLM）の自律性に依存します。どれか 1 層が抜けても残りで救える設計が、ハーネス工学の基本です。

---

## Layer 1: Codex Hooks の最小構成

Codex の Hooks 機構は `[features].codex_hooks = true` を有効化したうえで `hooks.json` を配置することで動きます。Claude Code と違い**実験的機能であること**、**`PreToolUse` / `PostToolUse` が主に Bash コマンドを対象とすること**を最初に把握しておきます。

筆者の `~/.codex/hooks.json` の骨格はこんな構成です（配線ファイルは `hooks/` ディレクトリの**外**に置く点に注意）。

```json
{
  "SessionStart": [
    {
      "type": "command",
      "command": "python3 ~/.codex/hooks/session_start.py"
    }
  ],
  "UserPromptSubmit": [
    {
      "type": "command",
      "command": "python3 ~/.codex/hooks/user_prompt_submit.py"
    }
  ],
  "PreToolUse": [
    {
      "matcher": "Bash",
      "hooks": [
        {
          "type": "command",
          "command": "python3 ~/.codex/hooks/pre_tool_use_bash.py"
        }
      ]
    }
  ],
  "Stop": [
    {
      "type": "command",
      "command": "python3 ~/.codex/hooks/stop_layer2_check.py"
    }
  ]
}
```

`SessionStart` で行うのは **Vault 共通ルールの注入**だけです。重要なのは「全部読む」ではなく「**最低限これだけは読ませる**」を選ぶことです。

```python
# ~/.codex/hooks/session_start.py（要点のみ）
import os, pathlib, sys

VAULT = pathlib.Path(os.environ["VAULT_ROOT"])

PRIORITY_FILES = [
    "AI-AGENT.md",                              # vendor-neutral 正本
    "memory/MEMORY.md",                          # PJ index + 重要feedback
    "memory/user_profile.md",                    # ユーザープロファイル
    "memory/feedback_user_response_preferences.md",  # 応答ポリシー
]

out = []
for rel in PRIORITY_FILES:
    p = VAULT / rel
    if p.exists():
        out.append(f"# === {rel} ===\n{p.read_text(encoding='utf-8')}\n")

# Codex は stdout を session context に追加注入する
sys.stdout.write("\n".join(out))
```

ここで `feedback_*.md` を**全件**注入しないのがコツです。Vault に蓄積されると数十〜数百件になり、トークン枠を圧迫します。トリガーとなるキーワード（PJ 番号・ファイル名・"課金"・"X 投稿" 等）を `UserPromptSubmit` で検出し、関連する feedback だけを後から引っ張る方式に分離します。

`PreToolUse Bash` フックでは、`git add -A`・`rm -rf` の対象パス・本番デプロイ系コマンド・`curl` の Authorization ヘッダ漏洩などを正規表現で検査します。検出時は exit code を非ゼロにすると Codex 側がブロックを尊重します。

```python
# ~/.codex/hooks/pre_tool_use_bash.py（抜粋）
import json, re, sys

payload = json.load(sys.stdin)
cmd = payload.get("tool_input", {}).get("command", "")

DANGEROUS = [
    (r"\bgit\s+add\s+(-A|--all|\.)\b", "git add -A は禁止。ファイル名個別指定にしてください"),
    (r"\brm\s+-rf\s+(/|\$HOME|~)\s*$", "ホーム/ルート直下の rm -rf を検出"),
    (r"supabase\s+db\s+push\b",          "Supabase 本番適用前に RLS 確認が必要"),
]

for pat, msg in DANGEROUS:
    if re.search(pat, cmd):
        sys.stderr.write(f"[BLOCKED] {msg}\n")
        sys.exit(2)
```

---

## Layer 2: 明示呼び出し用 Skill

Hook が万能ではない以上、「Hook が落ちた」「コンパクト直後で記憶が薄い」「スモークテストで挙動を検証したい」という場面で**明示的に呼べる入口**が必要です。

筆者は `~/.codex/skills/vault-context-bootstrap/SKILL.md` を以下の構造で置いています。

```markdown
---
name: vault-context-bootstrap
description: Load ObsidianVault operating context before Codex work.
---

# Vault Context Bootstrap

## Quick Workflow
1. Read AI-AGENT.md（vendor-neutral 正本）
2. Read memory/MEMORY.md（PJ index）
3. Read user interaction context（user_profile / response_preferences）
4. Read task-specific memory（pj*.md / feedback_*.md / decisions/）
5. Use the right search mode（rg / vault-rag / obsidian MCP）
6. Apply gates before destructive / publishing actions
```

ポイントは **Skill 内で読むファイルの順序を固定する**ことです。順序が固定されていれば、Codex の応答が「どの層まで読み込んでから答えたか」を後から検証できます。検証可能性のないハーネスは保守できません。

---

## Layer 3: 運用プロトコル

最後の層は人間（あるいは LLM 自身）の自律性に依存する部分です。ここで効くのは**ドキュメントの構造**です。

筆者の Vault では `AI-AGENT.md` の冒頭にこう書いてあります。

> このファイルは AI 非依存・中立運用ルールの **正本**。
> ランタイム固有設定は adapter（CLAUDE.md / AGENTS.md / 将来の `.cursor/`）に分離する。

`AGENTS.md` の冒頭にはこう書きます。

> 優先度: グローバル `~/.codex/AGENTS.md` > `AI-AGENT.md`（中立層・正本）> このファイル（Codex adapter）> PJ ローカル `AGENTS.md`
>
> ⚠️ Vault 作業を始める前に `AI-AGENT.md` を必ず Read してから進めること。

LLM は「優先度」「正本」「⚠️」というメタ情報に強く反応します。Hook が漏れても、Skill を呼び忘れても、ファイル冒頭のこの 3 行で「AGENTS.md だけで終わるな」というメッセージが効きます。

---

## アンチパターン

実装中に踏んだ落とし穴を共有します。

- **`AGENTS.md` に共通ルールを書いてしまう** — adapter と正本の分離が崩れる。共通ルールは vendor-neutral な `AI-AGENT.md` に置く
- **`SessionStart` で全 feedback を注入する** — トークン枠を即圧迫。トリガー検出して必要分だけ引く
- **Hook をデバッグせず「動いた気がする」で終える** — Hook 失敗は静かに起きる。`stderr` のログを別ファイルに残し、毎週レビューする
- **Skill を Hook の代替にする** — Skill は明示呼び出し前提。自動性は持たない。両者は補完関係
- **Claude Code の Hook 設定を Codex にコピペする** — `PreToolUse` の matcher 名・引数仕様が違う。公式ドキュメントを必ず参照

---

## まとめ

- Codex CLI は `AGENTS.md` を読むだけで Vault の文脈を引き継いだことにはならない。共通ルール・MEMORY・feedback・PJ 記憶は明示的にロードする必要がある
- 完全同等は諦めて、**Hook（自動）/ Skill（明示）/ 運用プロトコル（人間ルール）の 3 層**で実用上の同等性を狙う
- `SessionStart` Hook で注入するのは「最低限これだけ」を厳選する。全部入れるとトークン枠が即死する
- `PreToolUse Bash` Hook で `git add -A` や本番デプロイ系の事故を遮断する。exit code 非ゼロでブロックが尊重される
- ファイル冒頭の「正本/adapter」「優先度」表記は、Hook/Skill 層が漏れたときの最終セーフティネットとして強力に効く
- 検証可能性のないハーネスは保守できない。Skill 内で読むファイルの順序を固定し、後から振り返れるようにする
