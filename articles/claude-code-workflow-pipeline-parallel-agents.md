---
title: "Claude Code Workflow スクリプトで複数エージェントを並列・パイプライン実行する"
emoji: "🤖"
type: "tech"
topics: ["claudecode", "agent", "ai", "automation"]
published: false
---

## はじめに

Claude Code には、複数のサブエージェントを**決定論的な制御フロー**でオーケストレーションする「Workflow ツール」があります。

通常の `Agent` ツールが「1つのサブエージェントを起動して結果を待つ」のに対し、Workflow スクリプトはループ・条件分岐・並列ファンアウトを JavaScript で記述し、その制御フローを確実に実行します。

```
通常のAgent:  main → spawn 1 agent → wait → proceed
Workflow:     main → script (parallel/pipeline/loop) → structured result
```

本記事では Workflow スクリプトの基本構造から、`pipeline()` と `parallel()` の使い分け、structured output の受け取り方まで解説します。

---

## Workflow スクリプトの基本構造

スクリプトは **plain JavaScript**（TypeScript は不可）で記述します。必ずファイル冒頭に `meta` を置きます。

```js
export const meta = {
  name: 'review-pr',
  description: 'PRを複数の視点でレビューし、確認済み問題を返す',
  phases: [
    { title: 'Review', detail: '複数次元でレビュー' },
    { title: 'Verify', detail: '各指摘を検証' },
  ],
}

// スクリプト本文（async コンテキストで実行される）
phase('Review')
const findings = await agent(
  '対象ファイルのバグ・セキュリティ問題・パフォーマンス問題を列挙してください。',
  { label: 'find-issues' }
)
log(`発見: ${findings.length}文字`)
```

### agent() の基本

```js
// schema なし → 最終テキストを string で返す
const text = await agent('ファイルを要約してください。')

// schema あり → バリデーション済みオブジェクトを返す
const SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    risk:    { type: 'string', enum: ['low', 'medium', 'high'] },
  },
  required: ['summary', 'risk'],
}
const result = await agent('リスクを評価してください。', { schema: SCHEMA })
console.log(result.risk) // 'low' | 'medium' | 'high'
```

`schema` を指定すると、エージェントは StructuredOutput ツールを呼ぶように指示され、型不一致の場合は自動リトライします。

---

## pipeline() と parallel() の使い分け

**最重要ルール: デフォルトは `pipeline()`**

| 関数 | バリア | 使いどき |
|------|--------|----------|
| `pipeline(items, ...stages)` | **なし** | 各アイテムが独立して全ステージを流れる。Wall-clock = 最遅アイテム1本分 |
| `parallel(thunks)` | **あり** | 全アイテムの前ステージ完了を待ってから次へ。全結果を合算したいとき |

### pipeline() の例

ファイルリストの各ファイルをレビュー → 各ファイルの結果を検証、という2ステージを並行処理します。

```js
const files = ['src/auth.ts', 'src/db.ts', 'src/api.ts']

const results = await pipeline(
  files,
  // Stage 1: レビュー
  (file) => agent(`${file} のバグ・問題点を列挙してください。`, {
    label: `review:${file}`,
    phase: 'Review',
    schema: FINDINGS_SCHEMA,
  }),
  // Stage 2: 検証（Stage 1の結果 + 元アイテムを受け取れる）
  (review, file) => agent(
    `次の指摘が本当に問題か検証してください:\n${JSON.stringify(review.findings)}`,
    { label: `verify:${file}`, phase: 'Verify', schema: VERDICT_SCHEMA }
  )
)
// auth.ts が Stage 2 に進む間、db.ts は Stage 1 を並行実行している
```

`pipeline()` の Stage コールバックは `(prevResult, originalItem, index)` を受け取ります。後段で元のファイル名が必要なときは `originalItem` を使います。

### parallel() が必要なケース

全ファイルのレビュー結果を集めてから重複除去し、それを検証する——全件が揃う必要がある場合はバリアが正当です。

```js
// 全ファイルのレビューを並行実行（バリア）
const allReviews = await parallel(
  files.map(f => () => agent(`${f} をレビュー`, { schema: FINDINGS_SCHEMA }))
)

// 全件をまとめて重複除去（全件が必要）
const deduped = deduplicateByFileAndLine(
  allReviews.filter(Boolean).flatMap(r => r.findings)
)

// 重複除去後の指摘を検証
const verified = await parallel(
  deduped.map(f => () => agent(`指摘を検証: ${f.desc}`, { schema: VERDICT_SCHEMA }))
)
```

「flatten/map/filter するだけ」なら `pipeline()` の中でやれば済みます。cross-item の集計が不要なのにバリアを置くのは wall-clock の無駄です。

---

## loop-until-dry パターン

バグや問題点を「見つからなくなるまで」探すループを書けます。

```js
const bugs = []
let dry = 0

while (dry < 2) {
  const result = await agent(
    'コードベースの未発見バグを列挙してください。',
    { schema: BUGS_SCHEMA }
  )
  const fresh = result.bugs.filter(b => !bugs.some(existing => existing.id === b.id))
  if (fresh.length === 0) {
    dry++
  } else {
    dry = 0
    bugs.push(...fresh)
    log(`${bugs.length} 件発見`)
  }
}

return bugs
```

連続2ラウンド新発見がなければ終了する「枯渇ループ」です。単純な `while (count < N)` より tail を捕捉しやすい構造です。

---

## よくある間違い

### TypeScript の型注釈を書いてしまう

```js
// NG: TypeScript は parse エラーになる
const files: string[] = ['a.ts', 'b.ts']

// OK: plain JS
const files = ['a.ts', 'b.ts']
```

### Date.now() / Math.random() を使う

Workflow スクリプトは **resume（再実行キャッシュ）** をサポートします。`Date.now()` や `Math.random()` は resume で値が変わるため、意図的に使用禁止にされています。タイムスタンプが必要なら `args` で外から渡してください。

```js
// NG
const ts = Date.now()

// OK: args から受け取る
const ts = args.timestamp // Workflow 呼び出し時に { args: { timestamp: Date.now() } } を渡す
```

### parallel() でバリアを置きすぎる

前ステージの **全アイテム** が完了するまで後ステージが始まれないため、遅いアイテム1つが全体を足止めします。「全件が揃わないと先へ進めない」理由がないなら `pipeline()` を使ってください。

---

## まとめ

- **`pipeline()` がデフォルト**: 各アイテムが独立してステージを流れ、最遅の1本が wall-clock を決める。バリアなし
- **`parallel()` はバリアが必要な時のみ**: 全件結果を cross-item で合算する直前に使う
- **`schema` で型安全**: エージェントの出力を JSON Schema でバリデーション、型不一致は自動リトライ
- **loop-until-dry**: 件数が不明な発見タスクには count ループより枯渇ループが安全
- **plain JavaScript**: TypeScript 記法・`Date.now()`・`Math.random()` は使用不可

Workflow ツールを使うと、コードレビュー・バグ探索・ドキュメント生成など「多視点で並列して結果を集める」タスクをコード1ファイルで構造化できます。Agent ツールの並列呼び出しとは違い、制御フローがスクリプトに明示されるため、後から読み返したり、途中から resume したりが容易です。
