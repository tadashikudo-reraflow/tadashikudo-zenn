---
title: "Claude Codeのマルチエージェントを、LangGraphの設計思想で整理・改善した話"
emoji: "🕸️"
type: "tech"
topics: ["AI", "Claude", "LangGraph", "マルチエージェント", "ClaudeCode"]
published: true
---

## はじめに

「LangGraphって今どこで使われてるの？」という素朴な疑問を持ったことがきっかけでした。

LangGraphを調べていくうちに気づいたこと——**自分がMarkdownで運用しているClaude Codeのマルチエージェント設計が、すでにLangGraph相当の構造をしていた**。

Python製のランタイムを入れるわけでも、グラフを描くわけでもない。でも設計思想は同じだった。この記事では、LangGraphの概念を使って既存のClaude Codeエージェント設計を解析し、OpusとCodexでレビューして10個のギャップを発見→修正するまでの話を書きます。

## LangGraphとは何か（3分でわかる概念）

LangGraphは、LLMエージェントの「状態遷移グラフ」をPythonで記述するフレームワークです。核心となる概念は6つ。

| 概念 | LangGraph | 説明 |
|------|-----------|------|
| **State** | `TypedDict` | グラフ全体で共有される状態オブジェクト |
| **Node** | Python関数 | 状態を受け取り、状態を返す処理単位 |
| **Edge** | 条件付き遷移 | どのNodeに次に行くかのルーティング |
| **Checkpoint** | `MemorySaver` | 中断・再開可能な状態スナップショット |
| **HITL** | `interrupt_before` | 人間確認が必要な分岐点 |
| **Supervisor** | `add_conditional_edges` | 複数Agentを調整する親Node |

これを見た瞬間に「あれ、これMarkdownで書いてるやつと同じじゃないか」となりました。

## Vault設計とLangGraphの対応

私はClaude Codeでのエージェント組織を`~/.claude/`配下にMarkdownで管理しています。改めてLangGraphの目で対応表を作ると：

| LangGraph概念 | Vault実装 | 具体例 |
|--------------|-----------|-------|
| **State** | `MEMORY.md` | PJ状態・working-memory・フィードバック一覧 |
| **Node** | `SKILL.md` / `agents/*/SKILL.md` | dev, pm, ceo, news, marketing等の各Agent |
| **Conditional Edge** | SPECブロックの `if/else` 判定 | dev/SKILL.md内の「codex失敗→フォールバック」ルーティング |
| **Checkpoint** | Gitコミット | `session-end`スキル実行時に自動コミット |
| **HITL** | 確認ゲート | X投稿・Agent Team起動・外部API課金時の承認フロー |
| **Supervisor** | CEO Agent | 9つの配下Agentをタスク分解・進捗管理 |
| **Fan-out / Fan-in** | Agent Teamモード | `🔵チーム` フラグで並列spawn |

つまり、**LangGraphはこの設計思想のPython実装版**であり、Vaultはその**Markdown実装版**という位置づけでした。

## OpusとCodexで設計レビューを実施

「LangGraph相当」と言えるなら、LangGraphの設計パターンから逆算してギャップを探せるはず。

Claude Opus 4.6 + Codex（GPT-5.4）で並列レビューを実施しました。Opusには設計思想・ADR生成を、Codexには実装ギャップの具体的指摘を依頼。

### 発見した10個のギャップ

| # | Gap | 深刻度 |
|---|-----|--------|
| 1 | リトライカウンタがセッション跨ぎで消える（volatile） | 高 |
| 2 | Dev→Security ハンドオフスキーマ未定義 | 高 |
| 3 | CEO ループ終了条件が曖昧（無限ループリスク） | 高 |
| 4 | News→Marketing ハンドオフ形式未定義 | 中 |
| 5 | two-stage-review外部レビュー失敗時のスコア補正なし | 中 |
| 6 | PRD→GitHub Issue SPEC変換形式未定義 | 中 |
| 7 | Analyst の起動トリガーが受動的すぎる | 低 |
| 8 | フォールバック実装経路で品質ゲートがスキップされる | 高 |
| 9 | PM AgentのPJリストが陳腐化（廃止PJ3件が残存） | 中 |
| 10 | 自己レビューパターンしかなく対立議論が欠如 | 低 |

OpusはさらにADR（Architecture Decision Record）を5本生成しました：

- **ADR-001**: ハンドオフコントラクト機構の導入
- **ADR-002**: Adversarial Debate Patternのスキル化
- **ADR-003**: Fan-in調整スキルの明示化
- **ADR-004**: メモリ統合自動化
- **ADR-005**: CEO Reflection Stage（ReAct的な自己評価ループ）

## 実装した修正（Week 1）

すべてを一度に実装するより、まず高深刻度のGapをピンポイントで直すことを選びました。

### Gap #1: リトライカウンタの永続化

**Before**: `dev/SKILL.md`内のカウンタ変数はセッション再開で0にリセット

**After**: `60_data/dev-queue.md`のタスク行に`retry_count: N`フィールドを追加

```markdown
<!-- dev-queue.md のエントリ例 -->
- [ ] Issue #42: OAuth実装 | retry_count: 1 | last_error: "codex timeout"
```

セッション再開時にこのフィールドを読み取って継続カウント。フィールド未存在時は0から開始。

### Gap #5: 外部レビュー失敗時のスコア補正

**Before**: Stage 3（Codex外部Evaluator）失敗時はサイレントにスキップ

**After**:

```
- エラー時はスキップ（Stage 1〜2の判定で出力を続行）
  ただし `[EXTERNAL_REVIEW_UNAVAILABLE]` を結果に明記し、
  総合判定のPASS閾値を75点→80点に引き上げる
```

外部評価者がいないぶん、自己評価の基準を上げるというシンプルな補正です。

### Gap #8: フォールバック後の品質ゲートスキップ

**Before**: codex-worker失敗→Claude自力実装の経路で`/two-stage-review`を省略

**After**: 実装経路に関わらず、必ずStep 7.5→7.6（`/two-stage-review`）を経由する旨を明記

### Gap #9: PM Agent PJリスト陳腐化

アーカイブ済みPJ（PJ10えほんレールウェイ、PJ12/PJ13）を削除し、現行アクティブPJ（PJ19〜34）に更新。

## ADR-001: ハンドオフコントラクト機構

Gap #2/#4/#6に対処するため、`~/.claude/handoff-contracts/`を新設しました。LangGraphで言えば「Edge定義ファイル」にあたります。

```
~/.claude/handoff-contracts/
├── README.md
├── product-pipeline.md       # PM → Dev
├── content-pipeline.md       # News → Marketing
└── product-extended-pipeline.md  # Dev → Security
```

各ファイルで「送信側が出すべき形式」と「受信側の検証ルール」を定義しています。

**product-pipeline.md の例**（PM→Dev ハンドオフ）:

```markdown
## SPEC

### 受け入れ条件（Acceptance Criteria）
- [ ] AC1: ユーザーがXXXできる
- [ ] AC2: エラー時にYYYが表示される

### スコープ外（Out of Scope）
- ZZZ機能は別Issue

### メタデータ
pj: PJxx
priority: P1
compliance_required: false
security_review_required: true
```

Dev AgentはSPECブロックが存在しない場合は受け取り拒否→PM側に差し戻しのルールを持ちます。

## ADR-005: CEO Reflection Stage

LangGraphの**Reflection Pattern**（出力を評価して改善するループ）を、CEO Agentの日次フローに組み込みました。

従来：タスク分解 → Agent spawn → レポート生成 → **終了**

改善後：タスク分解 → Agent spawn → レポート生成 → **Reflection（3ステップ）** → 待機

### Reflection の3ステップ

**Step R1: Aggregate（収集）**
- 繰り返しエラーパターン（3回以上 = 構造的問題）を特定
- 達成率50%未満の目標を分類（単発失敗 vs 慢性的不達）
- Handoff Contractを使わなかったハンドオフを検出

**Step R2: Reflection（反省）**

```markdown
## CEO Reflection YYYY-MM-DD

### 何が機能したか
- News→Marketing ハンドオフが初めてコントラクト準拠で実行

### 何が機能しなかったか
- PJ05投稿達成率33%（目標3本→実績1本）
  - 根本原因仮説: [Marketing Agent のループ上限10回が低すぎる]
  - 次のアクション: max=15 に変更を提案
```

**Step R3: Bet for Tomorrow（賭け宣言）**

翌日の最重要Betを1〜3件Founderに提示して承認を得る。これがHITL（Human-in-the-Loop）として機能します。

### ループ終了条件（Gap #3対策）

> 「構造的問題なし・全Bet確認済み」→次のFounder指示まで待機
> 「構造的問題あり」→対象SKILL.mdの修正をFounderに提案して待機

自律ループを禁止し、必ずFounderの承認で次フェーズに進む設計です。

## LangGraph Pythonとの使い分け

「じゃあPythonのLangGraphは使わないの？」という疑問が当然あります。

私の結論は「**ユースケース次第で使い分ける**」です。

| 観点 | Markdown実装（Vault） | LangGraph（Python） |
|------|----------------------|---------------------|
| 適用場面 | 中長期の組織設計・戦略的タスク | 短期・高頻度・決定論的なループ |
| 状態管理 | Git＋MEMORY.md | `MemorySaver` / `AsyncSqliteSaver` |
| デバッグ | git diffで追える | LangSmithで可視化 |
| 変更コスト | エディタで即座に変更 | Pythonコード変更＋テスト |
| 向いているタスク | 複数PJの戦略調整・週次計画 | データパイプライン・APIループ |

実装コストを下げながら設計思想の恩恵を受けたいなら、Markdownから始めるのは有効な選択肢です。

## まとめ

- Claude Codeのマルチエージェント設計は**LangGraphの設計思想をMarkdownで実装したもの**と解釈できる
- OpusとCodexの並列レビューで**10個の設計ギャップ**を発見
- Week 1で高深刻度のギャップ4件を修正（リトライ永続化・スコア補正・品質ゲートスキップ防止・PJリスト更新）
- **ハンドオフコントラクト**（ADR-001）でAgent間のデータスキーマを定義→Edge定義に相当
- **CEO Reflection Stage**（ADR-005）でReflection PatternをMarkdownレベルで実装
- LangGraph Pythonは「短期・高頻度・決定論的ループ」に向いており、Markdownと使い分ける

LangGraphの概念は特定のPythonライブラリではなく、エージェント設計の**思考フレームワーク**です。どんな実装でもこのレンズで見ると改善点が見えてきます。

---

*この記事のベースとなった設計は`~/.claude/`配下のMarkdownで管理しています。改善内容は随時gitコミットで追跡しています。*
