---
title: "話題のHermes Agentを調べたら、Claude Code環境に全部あった件"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AIAgent", "NanoClaw", "マルチエージェント", "AI自動化"]
published: false
---
# 話題のHermes Agentを調べたら、Claude Code環境に全部あった件

## はじめに

Xのタイムラインに「Hermes Agentがヤバい」「マルチエージェント構築ならこれ一択」という投稿が流れてきた。NousResearch製のOSSエージェントフレームワークで、GitHub Starsが一晩で5,000を超えたというやつだ。

面白そうなので公式ドキュメントをひと通り読んだ。読み進めるうち、「あれ、これ全部すでにClaude Codeで実装してないか？」という既視感が止まらなくなった。

この記事では、Hermes Agentが提供する主要機能を一つずつ分解し、Claude Code環境のどの機能が対応しているかを整理する。

---

## Hermes Agentの主要機能

### 1. SOUL.md — エージェントの人格定義

Hermes Agentはシステムプロンプトの最上位に `SOUL.md` を注入する。性格・トーン・絶対やらないことを静的ファイルで定義し、セッション・プロジェクト跨ぎで人格を固定する仕組みだ。

```
~/.hermes/SOUL.md  # 全エージェント共通の人格層
~/.hermes/profiles/dev/SOUL.md  # プロファイル個別の人格
```

### 2. 3層メモリアーキテクチャ

Hermesのメモリは3段階に分かれている。

| Tier | 内容 | 特性 |
|------|------|------|
| Tier 1 | MEMORY.md + USER.md | 小・固定。セッション開始時にシステムプロンプト注入 |
| Tier 2 | SQLite FTS5（全会話）| 無制限。オンデマンド検索+LLM要約 |
| Tier 3 | 外部メモリプロバイダ（8種）| 無制限。ターン前プリフェッチ/レスポンス後シンク |

Tier 1が80%埋まると自動圧縮が走る。

### 3. スキルシステム（Progressive Disclosure）

スキルのロードは3段階で制御される。

- **L0**: 名前+説明のみ（約3,000トークン）
- **L1**: スキル本文をロード
- **L2**: 参照ファイルを深掘り

組み込み87件 + オプション79件 + Anthropic製16件 + コミュニティ505件で合計687スキルが利用可能。自動作成トリガーも内蔵（5回以上ツール呼び出し完了・エラー後解決・ユーザー修正・非自明ワークフロー発見）。

### 4. Curatorによるスキル自動整理

7日idle後にバックグラウンドで起動し、スキルを自動整理するデーモン。

```
30日未使用 → stale
90日未使用 → archive
LLMレビュー（最大8イテレーション）: keep / patch / consolidate / archive
```

安全策として実行前にtar.gzスナップショットを取り、バンドル/Hubスキルは不触。`hermes curator pin <skill>` でピン留め可能。

### 5. 組み込みcronスケジューラ

ゲートウェイデーモンが60秒間隔でチェックし、期限到達ジョブを独立セッションで実行する。

```bash
# 自然言語でcron設定
hermes cron add "毎朝9時にSlackにKPIレポートを送る" --skill kpi-reporter

# コンテキストチェーン
hermes cron add "前回の出力を次のcronに渡す" --context_from prev_job_id
```

### 6. GEPA — オフラインスキル自己進化

ICLR 2026 Oral採択のNousResearch/hermes-agent-self-evolutionリポジトリが提供するパイプライン。

```
実行ログ取込 → 評価データセット生成 → 進化的探索 → LLM-as-judge
→ 制約ゲート（100%テスト通過・15KB以内）→ PR送信（直接commitしない）
```

コストは$2〜10/回で、GPU不要（全てAPI呼び出し）。

### 7. プロファイルシステム

```bash
hermes profile create dev --clone
hermes profile create marketing --clone
```

プロファイルごとにconfig・メモリ・スキル・SOUL.mdが独立する。COO→PM→Marketing/Dev/Sales/Watchdogという組織階層を作れる。

---

## Claude Code環境との対応表

ここからが本題だ。上記の機能を「Claude Codeにすでにあるか」で整理した。

| Hermes機能 | Claude Code相当 | 備考 |
|-----------|----------------|------|
| SOUL.md | CLAUDE.md | グローバル→プロジェクト→PJの3段階継承 |
| Tier 1 メモリ | memory/MEMORY.md | セッション開始時に自動ロード |
| Tier 2 SQLite FTS5 | vault-rag (sqlite-vec) | bge-m3ベクトル+全文検索 |
| Tier 3 外部プロバイダ | MCP Servers | vault-rag/Slack/Gmail/Calendar等 |
| スキルシステム | `.claude/skills/` | 50件上限・SKILL.md形式 |
| L0 Progressive Disclosure | スキルインデックス | CLAUDE.md + skill-index.md |
| Curator | `/skill-rotate` | 手動トリガーだが同等の棚卸し |
| cronスケジューラ | scheduled-tasks | 36件・SKILL.md+cron式 |
| プロファイルシステム | Agent Teams | `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` |
| GEPAスキル進化 | /session-end + feedback | フィードバックループは手動ADR |
| 承認ゲート | PreToolUse hooks | TeamCreate/Agent/Grok/Bash別に設定 |
| 自動圧縮 | `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | 75%でAutoCompact発動 |

### 対応していない（Hermes固有）機能

- **90ターン上限**: タスクあたりの暴走防止。Claude Codeにはなく手動で設計が必要
- **context_from チェーン**: 前cronの出力を次cronに自動受け渡し。Claude Codeのscheduled-tasksでは環境変数経由で代替
- **hermes profile clone**: プロファイル複製のワンコマンド。Agent Teamsは複数エージェントだが「クローン」概念はない
- **GEPA自動PR生成**: スキル改善提案をPRとして出力する仕組みはClaude Codeにない（手動ADR記録で代替）

---

## Claude Codeで同等構成を組む実例

### SOUL.md ↔ CLAUDE.md の対応

```markdown
# ~/.claude/CLAUDE.md（グローバル）

## 絶対ルール
- 外部LLM APIを呼ぶ前に必ず確認
- モデルはデフォルトSonnet（手動Opusのみ許可）

## トーン・スタイル
- シニアエンジニアとして振る舞う
- 曖昧な依頼は検証可能な成功条件に変換してから着手
```

これがHermesのSOUL.mdとほぼ同じ役割を果たす。

### cronスケジューラ ↔ scheduled-tasks

```yaml
# .claude/scheduled-tasks/morning-briefing/task.yaml
name: morning-briefing
cron: "0 7 * * *"
skill: SKILL.md
description: 毎朝7時に朝刊ブリーフィングを実行
```

Hermesの `hermes cron add` が自然言語→cron変換を自動でやってくれる点は便利だが、Claude CodeのYAML設定でも完全に同等の定期実行は実現できる。

### スキルの自動整理 ↔ /skill-rotate

Hermesは7日idleで自動起動するが、Claude Codeでは `/skill-rotate` スキルを手動で実行する。自動化したければscheduled-tasksに月次で登録すれば同等になる。

```yaml
# .claude/scheduled-tasks/skill-monthly-rotate/task.yaml
cron: "0 10 1 * *"  # 毎月1日10時
description: スキルローテーション実行
```

---

## 結論：どちらを選ぶか

Hermes AgentはCLIベースで完全ヘッドレス運用が可能、かつSuper Grok OAuthで**LLM費用ゼロ**にできる点が明確な優位性を持つ。Mac miniのような専用機に常駐させて24/7自律運用するユースケースでは現時点でベストな選択肢だ。

一方、すでにClaude Codeを業務に使っている場合、CLAUDE.md / スキル / scheduled-tasks / Agent Teamsの組み合わせで同等のマルチエージェント基盤を**追加費用・学習コストゼロ**で構築できる。

調査の結果、筆者は**ユースケース別の使い分け**に落ち着いた。24/7ヘッドレス常駐・LLM費用ゼロが必要な専用機にはHermesを採用し、インタラクティブな開発・Vault管理にはClaude Codeを残す構成だ。「どちらか一択」ではなく、役割を分けて両立する判断になった。

## まとめ

- Hermes AgentはSOUL.md・3層メモリ・Curator・GEPA・プロファイル・組み込みcronを持つOSS AIエージェント
- Claude CodeはCLAUDE.md・MEMORY.md+vault-rag・skill-rotate・scheduled-tasks・Agent Teamsで同等機能をカバー
- Hermes固有の利点は「ヘッドレス常駐」「LLM費用ゼロ（Grok OAuth）」「90ターン暴走防止」
- ヘッドレス専用機常駐にはHermes、インタラクティブ開発・複雑なVault作業にはClaude Code — 役割別の使い分けが現実解
- どちらも「フレームワークを選ぶ」ではなく「自分の運用フローに合わせてカスタマイズする」姿勢が重要
