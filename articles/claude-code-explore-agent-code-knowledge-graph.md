---
title: "Claude Code の Explore Agent が遅い？ コードナレッジグラフで94%ツールコール削減した話"
emoji: "🕸️"
type: "tech"
topics: ["claudecode", "mcp", "ai", "agent", "typescript"]
published: true
---

## はじめに

Claude Code で大きめのモノレポを触り始めると、Explore Agent（または `grep` / `glob` の連打）でじわじわ時間が溶けていく現象に必ず当たります。

筆者の手元には Next.js + TypeScript + Python が同居する `~/workspace/pj/` 配下に**33個の Git リポジトリ**があり、合計で TypeScript 約 1,200 ファイル、Python 約 500 ファイル。「この関数を変えると何が壊れる？」を Explore Agent に聞くと、毎回 30〜50 ツールコール走って 200KB のコンテキストを焼くことになります。

これを「**コードナレッジグラフ（KG）+ MCP**」で置き換えたところ、同じ質問が **3〜5 ツールコール**で終わるようになりました。本記事は **GitNexus 系の Code Graph MCP** を Claude Code に組み込んで運用するときの実装パターンと、grep ベース検索との使い分け基準をまとめます。

---

## ツールコールが膨らむ典型パターン

例として「`getUserSession` を変更したら影響範囲はどこか？」を Explore Agent に投げたときの内部動作を分解すると、こうなります。

```
1. Glob: **/*.ts で getUserSession を grep
2. 候補ファイル 27 件を1つずつ Read（部分読み）
3. 呼び出し元っぽい React コンポーネントをさらに辿る
4. middleware / API route の wrapper チェーンを別検索
5. 型エイリアス UserSession を別途 Grep
...
```

これで `38 tool calls / 184KB context` を消費しました。原因はシンプルで、**Explore Agent は「コードの関係構造」を持っていない**からです。テキスト検索なので毎回構造をゼロから推定し直す。

---

## コードナレッジグラフという解

`tree-sitter` で AST を抜き、関数・クラス・import・API route・hook を**ノード**、call / import / wraps / fetches を**エッジ**としてグラフ DB に入れる、というアプローチがあります。筆者は OSS の `gitnexus` をローカルで MCP として刺しています。

```bash
# インデックス済みリポジトリ確認
$ gitnexus list-repos

# 出力例
PJ03_politech-os    files=248  nodes=6337  edges=13537
gcportal            files=267  nodes=4156  edges=5447
PJ29_residential    files=107  nodes=1919  edges=2509
```

`edges=13537` というのは「13,537 個の関係（呼ぶ / import する / wrap する / fetch する）が事前計算済み」ということで、これがツールコール削減に直結します。

---

## MCP として Claude Code に刺す

`~/.claude/mcp.json` に登録するだけで Claude Code から直接呼べるようになります。

```json
{
  "mcpServers": {
    "gitnexus": {
      "command": "uvx",
      "args": ["gitnexus-mcp"],
      "env": {
        "GITNEXUS_DB": "/Users/yourusername/.gitnexus/graph.db"
      }
    }
  }
}
```

登録後、Claude Code 内では `mcp__gitnexus__impact` `mcp__gitnexus__context` `mcp__gitnexus__route_map` のようなツールとして自動的に見えます。

### 使用感の比較（実測）

同じ質問「`getUserSession` を変更した影響範囲」を投げた場合:

| アプローチ | ツールコール数 | コンテキスト消費 | 所要時間 |
|----|----|----|----|
| Explore Agent (grep+read) | 38 | 184 KB | 約 42 秒 |
| `mcp__gitnexus__impact` | **2** | **11 KB** | **約 3 秒** |

**約 94% のツールコール削減**になりました。グラフは「呼び出し元 / 型参照 / wrap している middleware / 影響を受ける API route」を1クエリで返してくれるので、Claude Code 側はそれを読んで判断するだけで済みます。

---

## 「グラフで聞くべき問い」と「grep で聞くべき問い」

ただし全てをグラフに置き換えるべきではありません。質問のタイプで使い分けると速度と精度が両立します。

### グラフが強い（KG を呼ぶ）

- **影響範囲分析**: 「この関数を消したら何が壊れる？」
- **コール経路**: 「API route `/api/grants` を誰がフェッチしている？」
- **依存の中心性**: 「最も多くのコンポーネントから import されている util は？」
- **型のリファクタリング前調査**: 「`UserSession` 型を持つ箇所」

```python
# 影響範囲
mcp__gitnexus__impact(symbol="getUserSession")

# API route の消費者
mcp__gitnexus__route_map(route="/api/grants")
```

### grep / semantic 検索が強い（KG を呼ばない）

- **文字列の存在確認**: TODO コメント / 特定エラーメッセージ
- **設定ファイル横断**: `.env` キー名 / Tailwind class
- **新規追加直後で未インデックス**のコード
- **「何となく似たコードある？」**系の曖昧質問（→ semantic 検索）

筆者は semantic 側は `code-rag`（bge-m3 + sqlite-vec）を別 MCP として刺しており、**KG = 構造クエリ / semantic = 概念クエリ / grep = 文字列クエリ**の三層で住み分けています。

---

## 運用上の落とし穴

実運用で踏んだ罠を3つだけ。

### 1. インデックスが古いと嘘をつく

`list-repos` の出力に `commitsBehind: 11` のように出ます。**3 コミット以上ズレたら再インデックス**を運用ルールにすると安全です。CI の post-merge hook で `gitnexus analyze` を回すか、`pre-commit` で差分インデックスを走らせるのが現実的。

### 2. 「全リポを一気に検索」はしない

複数リポにまたがる場合、`repo` パラメータを明示しないとグラフ DB 側で曖昧マッチが起きて精度が落ちます。Claude Code 側の system prompt（プロジェクト `CLAUDE.md`）に「**KG クエリは必ず repo を指定する**」と明文化しておきます。

### 3. グラフの限界を理解する

動的 import / 文字列で組み立てる関数呼び出し / メタプログラミングは AST から取れません。Python の `getattr` 経由のディスパッチや、Next.js の `(await import(name)).default` 系は**グラフに乗らない**ので、最終的な確認は実コード Read で裏取りする必要があります。

---

## まとめ

- Claude Code の Explore Agent は便利だが、**構造クエリには高コスト**（30〜50 ツールコール / 200KB context）
- `tree-sitter` ベースのコードナレッジグラフを MCP として刺すと、**同じ質問が 2〜5 ツールコールで完了**
- 「影響範囲 / 呼び出し経路 / 型参照」は KG、「文字列存在 / 概念検索」は grep・semantic 検索と**三層で使い分ける**
- インデックスの **commitsBehind** を運用フローに組み込む（古い KG は嘘をつく）
- 動的 import やメタプログラミングは KG の死角。最後は実コード Read で裏取り

「Explore Agent が遅い」と感じ始めたら、それは Agent のせいではなく、**質問にコード構造が必要なのに構造を持たないツールで答えようとしている**サインです。MCP を1本足すだけで体験が変わります。
