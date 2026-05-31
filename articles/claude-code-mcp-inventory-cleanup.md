---
title: "Claude CodeのMCPを棚卸しする——使っていないMCPが蓄積してセッションが遅くなる前に"
emoji: "🧹"
type: "tech"
topics: ["claudecode", "mcp", "ai", "設計"]
published: true
---

## はじめに

Claude Code を使い込んでいくと、MCP（Model Context Protocol）サーバーが少しずつ増えていきます。「便利そうだから」と追加したブラウザ操作系、検索系、外部SaaS連携系——気づけば 10、15 とつながっていて、いざ `claude mcp list` を叩くと半分は「いつ使ったか思い出せない」状態になっていたりします。

問題は数だけではありません。**接続済みの MCP サーバーは、そのツール定義がコンテキストに載る分だけトークンを食い、ツール選択の精度もわずかに鈍らせます**。使わないサーバーを放置するのは、起動のたびに見ない引き出しを全部開けてから作業を始めるようなものです。

この記事では、MCP を「棚卸し（インベントリ → 不要判定 → 削除）」する実践的な手順をまとめます。筆者の環境では 17 サーバーまで膨らんでいたものを、定期レビューの習慣で運用可能な数に保てるようになりました。

## なぜ「使わないMCP」が地味に効いてくるのか

MCP サーバーをつなぐと、そのサーバーが提供するツール群がエージェントから呼び出せるようになります。ここでコストが2種類発生します。

1. **コンテキストコスト**: ツールのスキーマ（名前・説明・引数定義）がプロンプトに展開される。サーバーごとに数個〜数十個のツールがあるため、積み重なると無視できない。
2. **判断コスト**: 似た役割のツールが複数並ぶと、エージェントが「どれを使うべきか」を迷う余地が増える。

最近の Claude Code はツールを遅延ロードする仕組み（必要になってからスキーマを取得する方式）を持っており、昔よりは緩和されています。それでも「接続そのものの維持」「ヘルスチェック」「認証の更新」といった運用負荷は残ります。つまり、**使わないサーバーは消すのが一番安い**わけです。

## Step 1: まず棚卸しリストを出す

最初にやるのは現状把握です。`claude mcp list` が全スコープのサーバーを接続状態つきで一覧してくれます。

```bash
claude mcp list
```

出力はこんな雰囲気になります（サーバー名は匿名化しています）。

```
Checking MCP server health…

remote-drive: https://example.com/mcp - ✓ Connected
remote-design-tool: https://example.com/mcp - ! Needs authentication
local-search: /usr/local/bin/search-mcp - ✓ Connected
browser-automation: node .../cli.js - ✓ Connected
notes-connector: node .../notes-mcp-server - ✓ Connected
plugin:some-saas: https://example.com/api/mcp (HTTP) - ! Needs authentication
```

ここで注目するのが `! Needs authentication` です。**「接続しているのに認証が切れたまま放置されている」サーバーは、ほぼ確実に使っていない候補**です。日常的に使っていれば、とっくに再認証しているはずだからです。

個別の詳細を見たいときは `get` を使います。

```bash
claude mcp get remote-design-tool
```

## Step 2: 設定がどこに書かれているかを把握する

棚卸しでつまずきやすいのが「同じサーバーがどのスコープに定義されているか分からない」問題です。Claude Code の MCP 設定は複数の層に分かれています。

| スコープ | 置き場所 | 用途 |
|---------|---------|------|
| project | リポジトリ直下の `.mcp.json` | そのプロジェクト専用。チーム共有向き |
| user（global） | ユーザー設定（`~/.claude.json` 相当） | 全プロジェクト共通で使うもの |
| plugin | プラグイン同梱 | プラグインが連れてくる |
| remote connector | claude.ai 側の連携 | クラウドのコネクタ |

プロジェクト単位の `.mcp.json` は中身を直接読めます。

```bash
cat .mcp.json
```

```json
{
  "mcpServers": {
    "local-search": {
      "command": "/usr/local/bin/search-mcp",
      "args": []
    },
    "notes-connector": {
      "command": "node",
      "args": [".../notes-mcp-server"],
      "env": {
        "API_TOKEN": "YOUR_API_TOKEN"
      }
    }
  }
}
```

:::message alert
`.mcp.json` は git にコミットされることが多いファイルです。`env` にトークンをベタ書きすると、そのままリポジトリに混入します。必ず環境変数参照（`os.environ.get("API_TOKEN")` を読むラッパー、もしくは別管理のシークレット）にしておきましょう。棚卸しのついでにここも点検する価値があります。
:::

## Step 3: 不要なサーバーを消す

候補が固まったら削除します。スコープを意識して `remove` します。

```bash
# user スコープから削除
claude mcp remove remote-design-tool --scope user

# project スコープ（.mcp.json）から削除
claude mcp remove notes-connector --scope project
```

プロジェクトスコープの `.mcp.json` サーバーについて「承認/拒否の選択」をリセットしたい場合は、専用コマンドがあります。

```bash
claude mcp reset-project-choices
```

削除後にもう一度 `claude mcp list` を叩いて、意図したサーバーだけが残っていることを確認すれば完了です。

## Step 4: 棚卸しを習慣化する3つのルール

一度きれいにしても、放っておけばまた増えます。リバウンドを防ぐために、筆者は次のルールを運用しています。

- **追加時に「発動経路」を1つ以上言えること**。「いつ・どのワークフローで呼ぶか」を説明できない MCP は追加しない。試したいだけなら project スコープで足して、終わったら消す。
- **`! Needs authentication` が出たら即判断**。再認証して使い続けるか、消すか。放置の常態化が最大の負債。
- **月1で `claude mcp list` レビュー**。30日触っていないサーバーは「本当に要るか」を自問する枠を作る。

「あると便利」は罠です。**実際に呼んでいない MCP は、便利の在庫ではなく、毎セッション運ぶ重り**だと考えると判断がぶれません。

## まとめ

- 接続済み MCP はツール定義のコンテキスト負荷と判断コストを生む。使わないものは消すのが最も安い
- 棚卸しは `claude mcp list` で現状把握 → `! Needs authentication` を不要候補として疑う
- 設定は project / user / plugin / remote の多層。`.mcp.json` のトークンベタ書きは同時に点検する
- 削除はスコープを指定して `claude mcp remove --scope ...`、必要なら `reset-project-choices`
- 「発動経路を説明できないものは足さない」「認証切れは即判断」「月1レビュー」でリバウンドを防ぐ
