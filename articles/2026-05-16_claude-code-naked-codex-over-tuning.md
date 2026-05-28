---
title: "ハーネスを育てすぎたClaude Codeが素のClaude Codeに負ける「naked codex現象」——自己診断と対処法"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "CLAUDEmd", "パフォーマンス", "設計"]
published: false
---
# ハーネスを育てすぎたClaude Codeが素のClaude Codeに負ける「naked codex現象」——自己診断と対処法

## はじめに

Claude Codeを使い込むほど、あることに気づく。`CLAUDE.md`にルールを追記し、スキルを増やし、hookを仕込む。最初は「これで完璧だ」と感じる。しかし数ヶ月後、なぜかタスクの完了品質が落ちていたり、Claude自身が指示に従わないケースが増えていたりする。

これを筆者は**「naked codex現象」**と呼んでいる。

> 重厚にチューニングされたClaude Codeが、何も設定していない素のClaude Codeに負ける状態。

本記事では、この現象がなぜ起きるか、どう自己診断するか、そして実際の対処法を解説する。

---

## naked codex現象とは何か

Claude Codeは起動時にプロジェクトルートと `~/.claude/` 配下のファイル群を読み込み、それを「コンテキスト」として持った状態でタスクを処理する。この初期ロードに含まれるのは主に以下だ。

- `CLAUDE.md`（グローバル + プロジェクト）
- `~/.claude/skills/*/SKILL.md`（アクティブスキル全件）
- hookスクリプト（SessionStart, PreToolUse, PostToolUse など）
- Agent定義・スケジュールタスク定義

これらは「LLMへの指示書」として機能するが、**LLMのコンテキストウィンドウに直接食い込む**という問題がある。

ルールが200行を超え、スキルが40件を超え、hookが10本以上になると、Claude Codeは毎回のターンで大量の指示書を「読み直して」から応答を生成する。この処理コストがタスク実行に使えるトークン・注意力を圧迫する。

結果として：

- **指示の優先順位を誤る**（長大なCLAUDE.mdの末尾のルールが無視される）
- **スキルのトリガー判定が曖昧になる**（類似スキルが増えるほど誤発動が増える）
- **hookが意図しない副作用を起こす**（コンテキストが肥大化して警告が飛び回る）

これらが重なった状態は、シンプルなプロジェクトで `claude` と打っただけの新鮮なセッション——いわゆる「naked codex」——に比べて明らかに品質が落ちる。

---

## なぜ「育てすぎ」が起きるか

現象の根本は、**ハーネス（設定群）が一方向にしか成長しない**ことにある。

Claude Codeは優れたコーディングアシスタントだが、自分のCLAUDE.mdを勝手に削除したり、古いスキルをアーカイブしたりはしない。追加は自律的に提案するが、削除や整理は人間からの明示的な指示がないと実行しない。

これにより以下のパターンが繰り返される：

```
タスクが失敗する
 → ルール追加でカバー（CLAUDE.md +10行）
  → 別のケースで失敗
   → スキルを追加
    → hookで防御
     → 3ヶ月後: CLAUDE.md 280行・スキル48件・hook 15本
```

各追加は局所的に正しい判断だ。しかし積み重なると全体として機能しなくなる。これはソフトウェアの「Big Ball of Mud」アンチパターンそのものだ。

---

## 自己診断メソッド

naked codex現象かどうかを判定する指標と基準値を示す。

### 指標1: CLAUDE.md の行数

```bash
wc -l ~/.claude/CLAUDE.md
```

| 行数 | 判定 |
|------|------|
| 〜180行 | 健全 |
| 181〜400行 | 要注意（公式推奨上限に近い） |
| 401行〜 | 危険域（Anthropic公式推奨超え） |

Anthropicは400行以下を推奨している。筆者の環境では256行になっており、目標の160〜180行を大きく超えていることが月次棚卸しで発覚した。

### 指標2: アクティブスキル数

```bash
ls ~/.claude/skills/ | wc -l
```

| 件数 | 判定 |
|------|------|
| 〜30件 | 健全 |
| 31〜40件 | 要注意 |
| 41件〜（上限50） | 高リスク |

スキルはSKILL.mdが全てコンテキストにロードされるわけではないが、トリガー判定ロジックが増えるほど**誤発動・未発動のノイズ**が増える。

### 指標3: hookの件数と複雑度

```bash
cat ~/.claude/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
hooks = d.get('hooks', {})
total = sum(len(v) for v in hooks.values())
print(f'Total hooks: {total}')
for k, v in hooks.items():
    print(f'  {k}: {len(v)}件')
"
```

hookはLLMコンテキストには直接入らないが、**各ツール呼び出しに割り込む**ため実行遅延と予期しないブロックの原因になる。

筆者環境の実測値：
- PreToolUse: 6件（Bash・TeamCreate・Agent・Edit/Write・Grok・Firecrawl にそれぞれ個別フック）
- PostToolUse: 3件
- SessionStart/Stop/PreCompact/PostCompact: 各1件
- **合計14件**

PreToolUseが6件というのは、毎回のツール呼び出しで最大6件のマッチング判定が走ることを意味する。

### 指標4: セッション開始の重さ（主観）

`claude` を起動してからfirst responseが返るまでの体感時間が**3秒を超えるようになったら**要注意だ。これはSessionStartフックの処理、初期ファイルロード、スキルインデックスのスキャンが重なった結果である。

---

## 具体的な対処法

### 対処1: CLAUDE.md のサージカル削除

月次棚卸しを実施し、以下の基準で削除する：

**削除してよいルール**：
- 「〜しないこと」系のネガティブルールで、最後30日間に違反事例がないもの
- 「詳細は → ~/.claude/docs/xxx.md を参照」と書いて実体が別ファイルにあるもの（参照先へ完全委譲できる）
- 特定PJのローカルCLAUDE.mdに移すべきもの（グローバルである必要がない）

実際に筆者が棚卸しでよく発見するのは「過去のインシデント対策ルール」だ。インシデントが起きた瞬間は重要に見えるが、6ヶ月後には誰も踏まない罠のために3行が費やされている。

```bash
# 最終更新日を確認して古いルールを特定する参考コマンド
git log --follow -p ~/.claude/CLAUDE.md | grep "^+" | head -50
```

### 対処2: スキルの3分類整理

スキルを以下の3分類に分けて整理する：

| 分類 | 定義 | 件数上限の目安 |
|------|------|-------------|
| A: 本番影響系 | 外部への書き込み・自動送信 | 〜10件 |
| B: 知識蓄積系 | 失敗パターンの体系化 | 〜15件 |
| C: ワンショット系 | 人間がリアルタイムでレビュー | 〜15件 |

Cに分類されるスキルは「Gotchasもなく、Evalもなく、一回完結」なものだ。これらは**コマンドファイル（`.claude/commands/`）に移すか、削除する**のが正解だ。スキルとして保持するほどの複雑性がない。

### 対処3: hookの最小化

hookは「どうしてもLLMへの指示では防げないガードレール」にのみ使う。

**hookが適切なケース**:
- シークレットの漏洩防止（`git add -A` を検知してブロック）
- 課金APIへの誤爆防止（未確認のまま外部LLM APIを叩こうとした場合にブロック）
- Vault git自動push（Stop hookでコミットをトリガー）

**hookが不適切なケース（CLAUDE.mdルールで十分）**:
- 「このコマンドの前にファイルを確認してください」→ CLAUDE.mdのルールで十分
- 「完了したらSlackに通知する」→ スキルのEvalステップで実行すべき
- 「Bashを実行する前にログを残す」→ ほとんどの場合、不要な複雑性

### 対処4: コンテキスト健全性チェックをCIに組み込む

月次ではなく**セッション開始時に自動チェック**できると理想的だ。

```bash
# ~/.claude/scripts/harness-health-check.sh
#!/bin/bash
set -e

CLAUDE_MD_LINES=$(wc -l < ~/.claude/CLAUDE.md)
SKILL_COUNT=$(ls ~/.claude/skills/ | wc -l)
HOOK_COUNT=$(python3 -c "
import json
d = json.load(open('$HOME/.claude/settings.json'))
hooks = d.get('hooks', {})
print(sum(len(v) for v in hooks.values()))
" 2>/dev/null || echo 0)

echo "=== Claude Code Harness Health ==="
echo "CLAUDE.md lines: $CLAUDE_MD_LINES (target: <180, warn: >400)"
echo "Active skills:   $SKILL_COUNT (target: <40, max: 50)"
echo "Total hooks:     $HOOK_COUNT (target: <10)"

[ "$CLAUDE_MD_LINES" -gt 400 ] && echo "⚠️  CLAUDE.md exceeds 400 lines" && exit 1
[ "$SKILL_COUNT" -gt 48 ] && echo "⚠️  Skill count approaching limit" && exit 1
echo "✅ Health check passed"
```

このスクリプトをSessionStartフックから呼び出すことで、閾値を超えた瞬間にセッション開始時に警告が出る。

---

## naked codexに「戻す」のではなく「軽量に保つ」

naked codex現象の解法は「全部削除して素に戻す」ではない。適切に育てたハーネスは確かに価値がある。目標は**「軽量で精度の高いハーネス」**だ。

筆者が実践している原則：

1. **削除は追加と同じ頻度で行う** — 何かを追加したら何かをアーカイブする
2. **グローバルより先にローカルを疑う** — そのルールは本当に全PJに必要か？
3. **ルールの有効期限を意識する** — 「このインシデントからN日経ったら削除候補」と記録する
4. **スキルは発動経路が明確なものだけ** — 「いつか使うかも」は入れない
5. **hookは最小限・最後の砦** — LLMの指示で防げることはLLMに任せる

---

## まとめ

- **naked codex現象**: ハーネスを育てすぎたClaude Codeが素のClaude Codeに性能で負ける状態
- 原因はCLAUDE.md・スキル・hookの一方向的な蓄積によるコンテキスト圧迫
- 自己診断は「CLAUDE.md行数 / スキル件数 / hook件数」の3指標で定量化できる
- 対処はサージカル削除・スキル3分類整理・hookの最小化の組み合わせ
- 月次棚卸しをCLAUDE.md自体に「context-drift-review」キーワードで自動リマインドさせると継続しやすい

Claude Codeは「設定すれば設定するほど良くなる」ツールではない。**設定の密度と精度を保つ**ことが、長期的に高い性能を維持する鍵だ。
