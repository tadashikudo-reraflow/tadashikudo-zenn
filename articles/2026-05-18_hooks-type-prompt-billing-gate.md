---
title: "Claude Code フックの type:\"prompt\" vs type:\"command\"——「止める」と「考えさせる」の使い分け、API課金ゲートの実装例"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "hooks", "設計", "自動化"]
published: true
---
# Claude Code フックの type:"prompt" vs type:"command"——「止める」と「考えさせる」の使い分け、API課金ゲートの実装例

## はじめに

Claude Code の `settings.json` に設定できるフックには、あまり注目されていない `type: "prompt"` というタイプがあります。

よく使われる `type: "command"` がシェルコマンドを実行して外部から制御するのに対し、`type: "prompt"` は **Claude 自身のコンテキストに追加の指示を差し込む** 動作をします。

「止める」か「考えさせる」か——この使い分けが、ハーネス設計の質を大きく変えます。本記事では実際に運用している3つの `type: "prompt"` フックを例に、設計の考え方を解説します。

---

## type:"command" と type:"prompt" の違い

まず基本を整理します。

```json
// type: "command" — シェルコマンドでハードブロック
{
  "matcher": "git push --force",
  "hooks": [
    { "type": "command", "command": "echo '強制プッシュ禁止' && exit 1" }
  ]
}
```

```json
// type: "prompt" — Claude 自身への追加指示を差し込む
{
  "matcher": "TeamCreate",
  "hooks": [
    { "type": "prompt", "prompt": "Agent Team起動前にユーザーへ確認を取ってください。" }
  ]
}
```

| 観点 | type: "command" | type: "prompt" |
|------|----------------|----------------|
| 制御の主体 | シェル（外部） | Claude 自身 |
| ブロック強度 | ハード（exit 1で強制停止） | ソフト（Claudeが判断） |
| 適した用途 | ルール違反の強制排除 | 確認・文脈判断が必要な場面 |
| 副作用 | exit 1でタスク中断 | Claudeが考慮して続行もあり |

`type: "prompt"` は Claude の推論ループに追加の文脈を注入するため、**「止めるかどうかをClaudeに判断させる」** 用途に向いています。

---

## 実装例1：Agent Team 起動ゲート

Agent Team（`TeamCreate` ツール）はトークンを大量消費します。Claude が自律的に「Agent Teamを使おう」と判断したとき、ユーザー確認なしに起動されると想定外のコスト増につながります。

```json
{
  "matcher": "TeamCreate",
  "hooks": [
    {
      "type": "prompt",
      "prompt": "Agent Teamゲート: TeamCreate が呼び出されようとしています。チーム構成（メンバー・目的）をユーザーに提示し、「このAgent Teamを起動してよいですか？（トークン大量消費）」と確認を取ってください。ユーザーが承認した場合のみ続行。"
    }
  ]
}
```

このフックが発火すると、Claude は `TeamCreate` を呼ぶ直前にこの指示を受け取り、**自分から確認プロンプトを出す**動作に切り替わります。

`type: "command"` + `exit 1` で完全ブロックにすることも可能ですが、それだと「ユーザーが明示的に依頼した場合」も止めてしまいます。`type: "prompt"` であれば Claude が文脈を読んで「ユーザーが既に承認している」と判断できる場合は続行できます。

---

## 実装例2：Grok API 課金ゲート（従量課金）

Grok API は従量課金で、Claude が内部チェーンの中で自律的に呼び出すと意図しない課金が発生します。

```json
{
  "matcher": "mcp__Grok__.*|mcp__grok__.*",
  "hooks": [
    {
      "type": "prompt",
      "prompt": "⚠️ Grok API（従量課金）を呼び出そうとしています。現在のユーザーメッセージに Grok / Grok API の明示的な利用依頼がありますか？ScheduledTask内での実行の場合は続行可。それ以外でユーザーの明示依頼がない場合は必ずブロックして確認を取ってください。"
    }
  ]
}
```

ポイントは **ScheduledTask 内では続行可** という条件分岐を prompt 内に入れていることです。夜間バッチや自動化タスクの中では Grok を使う設計になっているため、それを潰さずにインタラクティブセッションだけをゲートしています。

`type: "command"` + `exit 1` にした場合、ScheduledTask 内の正当な呼び出しも止まってしまうため、文脈依存の判断が必要なこのケースには `type: "prompt"` が適しています。

---

## 実装例3：Firecrawl スクレイピング課金ゲート

Firecrawl はページ単位の従量課金です。Claude が「調べておこう」と自律的に数十ページをクロールし始めると、予期しないコストになります。

```json
{
  "matcher": "mcp__firecrawl__.*",
  "hooks": [
    {
      "type": "prompt",
      "prompt": "⚠️ Firecrawl API（ページ単位の従量課金）を呼び出そうとしています。現在のユーザーメッセージに Firecrawl / スクレイピング / クロールの明示的な利用依頼がありますか？大量クロール（100ページ超）の場合は推定コストも伝えてください。ScheduledTask内での実行の場合は続行可。それ以外でユーザーの明示依頼がない場合は必ずブロックして確認を取ってください。"
    }
  ]
}
```

大量クロール時の「推定コストを伝える」という指示も prompt に含めている点が特徴です。`type: "command"` では単純な yes/no しか制御できませんが、`type: "prompt"` であれば Claude に **コスト計算して報告してから続行判断させる** ような複合的な指示が書けます。

---

## type:"prompt" を使う際の注意点

### ソフトゲートであることを理解する

`type: "prompt"` はあくまで Claude への追加指示です。Claude が「状況を踏まえると続行が適切」と判断した場合、フックの指示を無視して続行することがあります。

**セキュリティクリティカルな制御**（シークレットファイルの書き込み禁止・強制プッシュ禁止など）には `type: "command"` + `exit 1` を使ってください。

### matcher の誤検知に注意する

`type: "prompt"` は静かに差し込まれるため、誤検知しても気づきにくいのが難点です。

matcher は **ツール名（PreToolUse）またはイベント名（UserPromptSubmit）** に対して機能します。正規表現が広すぎると意図しないツール呼び出しにも発火します。

```json
// 悪い例: "Grok" が含まれる全ツールに発火してしまう
{ "matcher": "Grok" }

// 良い例: mcp__ プレフィックスで Grok MCP ツールに限定
{ "matcher": "mcp__Grok__.*|mcp__grok__.*" }
```

---

## まとめ

- `type: "prompt"` はシェルではなく **Claude 自身に追加指示を差し込む** フックタイプ
- 「ハードブロック」より「文脈を踏まえた自己判断」が必要な場面に有効
- 従量課金 API（Grok・Firecrawl）や Agent Team 起動など、**「条件付きで続行可」** なゲートに適している
- セキュリティクリティカルな制御は `type: "command"` + `exit 1` を使うこと
- matcher は正規表現で絞り込み、誤検知を最小化する設計にすること
