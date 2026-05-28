---
title: "Claude / Codex / Cursor を「中立層」で束ねる：AI-AGENT.md 設計と adapter pattern"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "Codex", "AI", "設計", "Obsidian"]
published: false
---
# Claude / Codex / Cursor を「中立層」で束ねる：AI-AGENT.md 設計と adapter pattern

## はじめに

複数の AI コーディングエージェントを併用していると、こんな悩みに突き当たります。

- Claude Code 用に書いた `CLAUDE.md` のルールを、Codex CLI でも適用したい
- でも `AGENTS.md`（Codex 用）にコピペすると、いつの間にか片方だけ更新されて乖離する
- Cursor を導入したら、また `.cursor/rules/` に同じことを書く羽目になる

筆者の Obsidian Vault でも、「言語は日本語で」「`.env` は出力しない」「外部 API 課金前は確認」といった**ランタイム非依存のルール**が、`CLAUDE.md` と `AGENTS.md` に二重化していました。新しいランタイムを足すたびに N 重化します。

この記事では、**中立層（neutral layer）+ adapter pattern** でこの問題を解いた設計を共有します。ソフトウェア設計の DI / Hexagonal Architecture とほぼ同じ発想を、AI エージェントの運用ルールに持ち込んだものです。

## 問題の構造：ルールの分類が混ざっている

エージェント向けルールは、よく見ると 2 種類が混ざっています。

| 種別 | 例 |
|------|-----|
| **AI 非依存（中立）** | プロジェクトのフォルダ構造、言語・出力形式、秘密情報の取り扱い、ブランドカラー |
| **ランタイム固有** | デフォルトモデル名（`claude-sonnet-4-6`）、Agent Team の起動方法、MCP ツールマップ、`~/.claude/` 配下のパス |

これらを 1 ファイルに混ぜて書くと、

- どのルールがどの AI で有効なのか曖昧
- ランタイム追加時にコピーすべき範囲が判断できない
- どちらかを編集して更新漏れが起きる

という事態になります。

## 設計：中立層 + adapter

そこで、**正本（source of truth）を中立層に置き、各ランタイムは差分だけ持つ adapter** にしました。

```
プロジェクトルート/
├── AI-AGENT.md         # 中立層・正本（ランタイム非依存ルール）
├── CLAUDE.md           # Claude Code adapter（差分のみ）
├── AGENTS.md           # Codex adapter（差分のみ）
└── .cursor/rules/      # Cursor adapter（将来）
```

- `AI-AGENT.md` が**唯一の正本**。Map・Rules・哲学・コンテンツポリシーを集約
- `CLAUDE.md` / `AGENTS.md` は冒頭で `AI-AGENT.md` を参照させ、**ランタイム固有の差分だけ**を記述

`CLAUDE.md` の冒頭はこんな具合です。

```markdown
# CLAUDE.md - プロジェクト名

> 優先度: グローバル `~/.claude/CLAUDE.md` > [[AI-AGENT]]（中立層・正本） > このファイル > PJローカル

> このファイルの位置づけ: Claude Code ランタイム固有の差分のみ
> （モデル運用・Agent Team・MCPマップ・`.claude/` 固有パス等）。
> 中立ルールは AI-AGENT.md が正本。
```

`AGENTS.md` も同じパターンです。

```markdown
# AGENTS.md - Codex adapter（差分のみ）

> 正本は AI-AGENT.md を参照。本ファイルは Codex ランタイム固有の差分のみ定義する。
```

## 何を中立層に置き、何を adapter に置くか

判断基準はシンプルで、**「別の AI ランタイムでも同じ結論になるか」** です。

### 中立層（AI-AGENT.md）に置くもの

- プロジェクトの Map（フォルダ構造）
- 出力スタイル（言語・見出し・テーブル形式）
- 検索の優先順（RAG 検索 → grep → web）
- 秘密情報・課金 API のガードレール
- 新規プロジェクト立ち上げ時の共通フロー
- ブランドカラー・コンテンツ格納ポリシー

### adapter に置くもの

- 使用モデル ID（`claude-sonnet-4-6` / `gpt-5` 等）
- ランタイム固有の起動コマンド・alias
- MCP ツール名（`mcp__vault-rag__search_vault` 等）
- ランタイム専用ディレクトリ（`~/.claude/`, `~/.codex/`）
- ランタイム固有のフック設定

ポイントは、**ツール名は adapter、ツールが解決する役割は中立層**という切り分けです。

たとえば中立層には「概念・文脈検索を優先せよ」と書き、adapter にだけ「Claude では `mcp__vault-rag__search_vault`、Codex では `vault-context-bootstrap` Skill を使う」と具体名を書きます。

## adapter 単独読み込み時のフォールバック

実運用で 1 つ落とし穴がありました。**Codex CLI は session 開始時に `AGENTS.md` だけ読み込み、`AI-AGENT.md` を自動では引かない**ことがあります。

そこで `AGENTS.md` には**最小限の安全ガードレールだけ再掲**しています。

```markdown
## 安全ガードレール（AI-AGENT.md から再掲・単独読み込み時の最低保証）

- 外部 API 課金・外部公開・送信・PR マージは人間承認必須
- `.env` / SSH 鍵 / トークン等の秘密情報を出力しない
- 検証してから完了とする
```

これは DRY の例外として割り切ります。「中立層が読まれなかった場合でも事故らない」最低保証は、各 adapter に冗長コピーする価値があります。同じ理由で「言語は日本語」も再掲しておくと安全です。

## セッション開始プロトコルで強制的に正本を読ませる

冗長コピーを最小化するもう 1 つの方法は、**セッション開始時に必ず正本を読ませる**ことです。

Codex 側では `~/.codex/hooks.json` の `SessionStart` hook で Vault 文脈を自動注入し、hook が効かないセッション・compact 後の復旧では `vault-context-bootstrap` skill を明示実行する、という二段構えにしています。

読み込み順は以下に固定しました。

```
AI-AGENT.md → memory/MEMORY.md → memory/user_profile.md
  → memory/feedback_*.md → 対象 PJ の memory/pj*.md → memory/lessons.md
```

Claude Code 側は SessionStart hook + 永続メモリ機能で同等の効果が出ます。**正本ファイルを最初に必ず読ませる**ことさえ守れば、adapter の差分が小さくても事故りにくくなります。

## 段階移行の進め方

中立化はビッグバン書き換えより、**Phase 分け**が安全です。実際に踏んだ Phase を共有します。

1. **Phase 1: スケルトン作成**。`AI-AGENT.md` を空に近い状態で作り、各 adapter の冒頭に「正本はこちら」の注記だけ追加。本文は移動しない
2. **Phase 2: 段階移植**。CLAUDE.md / AGENTS.md の AI 非依存セクションを 1 章ずつ AI-AGENT.md へ移植し、adapter 側は「§N は AI-AGENT を参照」に置換
3. **Phase 3: PJ ローカル CLAUDE.md** の整理。PJ 単位の adapter も同じパターンに揃える
4. **Phase 4: コード側の adapter 化**。LLM 呼び出しを `anthropic` / `openai` 等のプロバイダ別 adapter で抽象化

Phase 1 は破壊的変更ゼロで、ロールバックも「新規ファイル削除 + 注記 revert」で済みます。先にこれを切ると、合意形成と実装の両方が進みます。

## まとめ

- AI エージェント向けルールは「AI 非依存」と「ランタイム固有」を分離し、中立層 + adapter にする
- 正本は `AI-AGENT.md` 1 本に集約し、`CLAUDE.md` / `AGENTS.md` / `.cursor/rules/` は差分だけ
- 「ツールが解決する役割」は中立層、「具体的なツール名」は adapter に置く
- adapter 単独読み込み時のため、安全ガードレールだけは冗長コピーを許容する
- セッション開始時に正本を強制ロードさせれば、差分の小さい adapter でも事故が減る
- Phase 分けで段階移行すれば、ロールバック容易性を保ったまま進められる

ランタイムが増えるたびにルールが N 倍になる現象は、コードの世界では DI で解いてきた問題です。エージェント運用にも同じ抽象化を持ち込めば、長く育てられるリポジトリになります。
