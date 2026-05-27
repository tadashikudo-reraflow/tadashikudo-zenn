---
title: "Claude Code Agent Team 設計パターン——並列エージェントで開発を加速する"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "LLM", "エージェント"]
published: true
---
# Claude Code Agent Team 設計パターン——並列エージェントで開発を加速する

## はじめに

Claude Code には **Agent Team** という機能があります。1つのタスクを複数のサブエージェントに分割して並列実行し、開発速度を大幅に向上できます。しかし、闇雲にエージェントを増やしてもトークンを無駄に消費するだけです。

この記事では「いつ Agent Team を使うべきか」「どう分割するか」という設計パターンを、実例を交えて解説します。

---

## Agent Team とは

Claude Code の `Agent` ツールを使うと、親エージェント（オーケストレーター）が子エージェント（ワーカー）に独立したタスクを委譲できます。

```
Claude (オーケストレーター)
├── Agent A: フロントエンドのバグ調査
├── Agent B: バックエンドのAPI仕様確認
└── Agent C: テストカバレッジ計測
```

3つのサブエージェントが同時に動くため、直列で実行するより格段に速くなります。

---

## 設計パターン1: ファンアウト（調査の並列化）

最も使いやすいパターンです。複数の独立した調査を同時に実行します。

**適用条件**: 独立した情報収集タスクが3つ以上ある場合

```python
# 例: 3つのPJのセキュリティ脆弱性を同時調査
Agent(subagent_type="security", prompt="PJのAPIエンドポイントを調査してください")
Agent(subagent_type="security", prompt="依存パッケージの既知脆弱性を確認してください")
Agent(subagent_type="security", prompt="認証ロジックのレビューを行ってください")
```

重要なのは、**3つのエージェントが互いのファイルに触れない**ことです。同一ファイルを参照するエージェントを分割するとコンテキストの重複が発生し、かえって非効率になります。

---

## 設計パターン2: チェーン（段階的な処理）

前工程の出力を次工程の入力にするパターンです。

```
リサーチ Agent → 設計 Agent → 実装 Agent → レビュー Agent
```

**適用例: SEO記事の自動生成パイプライン**

```python
# Step 1: キーワードリサーチ
research_result = Agent(
    subagent_type="analyst",
    prompt="ターゲットKWの競合記事を5件調査してください"
)

# Step 2: 構成案生成（research_resultを受けて）
outline = Agent(
    subagent_type="blogger",
    prompt=f"以下のリサーチを元に構成案を作ってください: {research_result}"
)
```

チェーンパターンは並列化できませんが、各ステップの結果が確定してから次に進むため、**精度が重要なタスク**に向いています。

---

## 設計パターン3: ワーカープール（大規模コード変更）

大量のファイルを一括変更する際に、ファイルをグループ分けして並列処理します。

```
親 Agent
├── Worker 1: src/components/ 以下を変換
├── Worker 2: src/pages/ 以下を変換
└── Worker 3: src/utils/ 以下を変換
```

**実装例: TypeScript 4 → 5 の型定義マイグレーション**

```python
import glob

# ファイルを3グループに分割
all_files = glob.glob("src/**/*.ts", recursive=True)
chunk_size = len(all_files) // 3

agents = []
for i in range(3):
    chunk = all_files[i*chunk_size:(i+1)*chunk_size]
    agents.append(Agent(
        subagent_type="dev",
        isolation="worktree",  # ← 各エージェントが独立したブランチで作業
        prompt=f"以下のファイルをTS5対応に変換してください: {chunk}"
    ))
```

`isolation="worktree"` を使うと、各エージェントが独立したgitブランチで作業するため、コンフリクトが発生しません。

---

## 設計パターン4: コンテキスト分離（メインウィンドウの保護）

長時間のリサーチや大量のファイル読み込みは、親エージェントのコンテキストウィンドウを圧迫します。そこで**サブエージェントに調査を委譲**し、サマリーだけを受け取るパターンです。

```python
# 悪い例: 親が直接大量ファイルを読む（コンテキスト圧迫）
# Read("/path/to/large/file1.md")
# Read("/path/to/large/file2.md")
# ... 50ファイル分

# 良い例: サブエージェントに委譲してサマリーだけ受け取る
summary = Agent(
    subagent_type="Explore",
    prompt="以下のディレクトリを調査して、200字以内で要約してください: /path/to/large/dir"
)
```

**目安**: ファイル数が5つ以上、または調査対象URLが3つ以上の場合はサブエージェントに委譲します。

---

## Agent Team を使わないべき場面

エージェントを増やすことがかえってコストになるケースもあります。

| NG パターン | 理由 |
|------------|------|
| 独立タスクが2つ以下 | 分割コスト > 時間節約 |
| 同一ファイルを参照する複数エージェント | コンテキスト重複、コンフリクトリスク |
| 簡単なGrep/Glob検索をAgentに任せる | ツール直接呼び出しの方が高速・安価 |
| エージェント内でToolSearchを使う | 親のプロンプトにMCP名前空間を渡す設計に修正が必要 |

---

## モデル選択の実践的指針

Agent Team のコストは選択するモデルで大きく変わります。

```
高コスト: Opus（複雑な推論が必要な場合のみ使用）
標準:     Sonnet（デフォルト。ほとんどのタスクはこれで十分）
```

チームメンバーのデフォルトは **Sonnet** を使いましょう。Opusが必要な場面は限定的です（CEO/PM レベルの意思決定タスクなど）。

```python
# 推奨: Sonnetで統一
Agent(model="sonnet", subagent_type="dev", prompt="...")

# 必要な場合のみOpus
Agent(model="opus", subagent_type="pm", prompt="プロダクト戦略を策定してください")
```

---

## まとめ

Agent Team 設計の実践ポイントをまとめます。

- **3つ以上の独立タスク**がある場合のみ並列化する（2つ以下はAgentを使わない）
- **ファンアウトパターン**は調査・レビュー系タスクに最適
- **`isolation="worktree"`** を活用してコンフリクトを防ぐ
- **コンテキスト保護**のためにリサーチはサブエージェントに委譲する
- **モデルはSonnetで統一**し、必要な場合のみOpusに切り替える

Agent Team を適切に活用することで、開発タスクのスループットを大幅に向上できます。まずは「独立した調査タスクが3つある」という状況で試してみてください。
