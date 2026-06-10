---
title: "MCPは接続数よりprotocol watchを先に固定する"
emoji: "🧭"
type: "tech"
topics: ["mcp", "codex", "claudecode", "ai", "architecture"]
published: true
---

## はじめに

MCP を触り始めると、つい「何本つないだか」を進捗として見たくなります。自分も最初はその見方をしていました。ただ、2026-05-31 に Vault 運用ルールを見直したとき、先に固定すべきなのは接続数ではなく、仕様変化をどう追うかという判断基準だと整理しました。

その日に実際にやったのは、週次レビューで見えた差分を `AI-AGENT.md`、`AGENTS.md`、`runtime-capabilities.json`、関連する運用 skill に反映し、`sampling` 依存を増やさず `roots` / `prompts` / `resources` / `tools` / `elicitation` を優先する方針を正本化することでした。日次メモにも「hook追加より、`sampling` 非依存のMCP設計原則と protocol watch を正本へ足す方が先」と残しています。

## 2026-05-31 に変えたこと

判断の出発点は ADR `ADR-2026-05-31-mcp-protocol-watch-and-design-principles.md` です。ここでは「接続棚卸しだけ続ける」案ではなく、「正本へ原則追加」を採用しています。理由は単純で、MCP の価値が接続数そのものではなく、どの面を使うかに移っていたからです。

実際、正本の `AI-AGENT.md` には次の guidance を追加しました。

```md
新規MCP設計・追加・評価では
roots / prompts / resources / tools / elicitation を優先し、
sampling 前提の新規依存は避ける
```

同じ日には関連する2つの運用 skill にも、週次チェックで `roots` / `prompts` / `resources` / `tools` / `elicitation` と `sampling` の扱いを確認する手順を入れています。つまり「MCP を使う」ではなく、「MCP のどの面を監視対象にするか」を運用ルールへ昇格させました。

## 接続数より先に見るべきこと

この判断が効いたのは、transport が違っても見る観点を揃えられたからです。手元の実装でも、ローカルの stdio サーバーは `@modelcontextprotocol/sdk/server/stdio.js` の `StdioServerTransport` を使い、Cloudflare Workers 側の HTTP 実装は JSON-RPC 2.0 の `initialize` / `tools/list` / `tools/call` を自前で受けています。transport は違いますが、「どの tool を公開するか」「resource や prompt を持たせるべきか」「本当に新規接続が必要か」という設計論点は共通です。

この共通論点を先に固定しておくと、あとで新しい MCP を見つけても判断がぶれません。自分の運用では、まず既存 plugin / Browser / CLI / Docs MCP で代替できるかを確認し、代替できないときだけ新規接続を検討するようになりました。実際、この方針は手元の MCP 設定メモで「Google Drive / Gmail / Calendar / Slack は plugin で covered」「一部だけ partial」と整理している内容とも噛み合っています。

## protocol watch を入れて良かった点

一番大きかったのは、MCP を「増設タスク」ではなく「前提管理タスク」として扱えるようになったことです。2026-05-31 の日次メモでは、Codex セッションの結論として「先に増やすべきなのは hook ではなく、MCP の前提変化を判断基準として固定すること」と書いています。これはかなり実感に近いです。

運用していると、ツールの数より「その接続は今も必要か」「既存経路で置き換えられないか」「`sampling` 前提を抱え込んでいないか」のほうが後から効いてきます。先にそこを明文化しておくと、週次レビューで見るべき差分が減り、接続を追加するときの説明責任も軽くなります。

## まとめ

- MCP 運用の最初の指標を「接続数」に置くと、設計判断がぶれやすい
- 先に固定すべきなのは `roots` / `prompts` / `resources` / `tools` / `elicitation` をどう使うかという判断基準
- `sampling` は便利そうでも、新規依存の前提にしないほうが運用が安定した
- transport が stdio でも HTTP でも、監視すべき論点はかなり共通化できる
- 新規MCP追加前に既存 plugin / Browser / CLI / Docs MCP で代替できるかを見るだけで、接続の増殖をかなり抑えられる
