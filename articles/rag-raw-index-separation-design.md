---
title: "RAGのRawデータとIndexを分離する設計"
emoji: "🗂️"
type: "tech"
topics: ["rag", "postgresql", "ai", "設計"]
published: true
---

## はじめに

RAGを運用し始めると、PDFやHTMLの収集場所と、ベクトル検索用DBの境界がすぐ曖昧になります。手元でも最初は「DBに入っていれば十分では」と見がちでしたが、2026-06-08 の dry-run を確認して、その考えは危ないと分かりました。

今回の構成では、Rawデータの正本を content workspace 側に置き、`karte` schema は検索用Indexとしてだけ扱います。`scripts/collect-official-sources.js` は収集専用で DB に書かず、`scripts/plan-rag-ingest.js` は差分計画だけ、`scripts/ingest-rag-sources.js` は `--execute` を付けたときだけ `karte.documents` `karte.pages` `karte.chunks` へ書く形です。

この分離を入れていて助かったのは、投入前に「何がどれだけ増えるか」を止めて見られたことでした。

## 1. 収集と投入を分けると、想定外の膨張を事前に止められる

2026-06-08 時点の `ingest-plan-20260608.md` では、公式ソース 57 件のうち `insert_candidates` は 40 件、`estimated_new_chunks` は 2602 でした。ここだけ見ると、増分はそこまで大きく見えません。

ただし、そのまま `ingest-db-20260608-dry-run.md` まで進めると、`planned_chunks` は 3725 まで増えました。しかも `inserted_chunks` は 0 のままなので、まだ DB を汚さずに止まれています。

特に大きかったのが次の2件です。

- `mhlw-iryoudx-pdf-09`: 2098 chunks
- `mhlw-iryoudx-pdf-10`: 694 chunks

この2つだけで 2792 chunks あり、dry-run 全体の大半を占めていました。もし Raw と Index を分けず、収集したそばから本番DBへ投入する設計だったら、「想定よりかなり重い PDF が混ざっていた」と気づく時点でもう巻き戻しコストが発生します。

```bash
# 収集は保存だけ
node scripts/collect-official-sources.js

# まず差分計画を見る
node scripts/plan-rag-ingest.js

# まだDBには書かない
node scripts/ingest-rag-sources.js
```

自分の運用では、投入前に 1 回止めて数を見る工程を独立させるだけで、RAGの事故率がかなり下がりました。

## 2. DBを正本にしないと、責務の切り分けが明確になる

判断メモでも、Raw PDF/HTML/text を正本にし、DBは検索用インデックスとして扱う方針にしています。これは抽象論ではなく、実装とも揃っています。

`plan-rag-ingest.js` は既存URLを `karte.chunks` `karte.documents` `karte.pages` から引いて重複判定しますが、正本はあくまでファイル側です。`ingest-rag-sources.js` も `SET LOCAL search_path TO karte, public;` を使い、通常投入先を `karte` に限定しています。

```js
// DB書き込みは --execute を付けたときだけ
if (arg === "--execute") args.execute = true;

// 通常のdry-runでは karte.chunks は増えない
const summary = {
  db_write: args.execute ? "yes" : "no",
};
```

この形にしておくと、DBが壊れてもRawから再計算できますし、逆にRaw側の収集品質が悪ければDBを増やさずそこで止められます。検索性能のための構造と、証跡保全のための構造を同じ場所に押し込まない、というだけですが効果は大きいです。

## 3. 情報レイヤーを分けないと、RAGはすぐ根拠汚染を起こす

もう1つ効いたのが、`source-layer-taxonomy.md` で canonical と context を分けたことです。L0/L1 の公式・準公式だけを canonical RAG に入れ、ニュース、ベンダー資料、個人メモ、AI調査は別レイヤーに逃がしています。

`collect-official-sources.js` が canonical の L0/L1 以外を拒否し、`plan-rag-ingest.js` も `cite_policy=primary_ok` かつ `rag_tier=canonical` かつ `source_layer=L0/L1` のものだけを `insert_candidate` にしています。つまり「検索できるから根拠に使う」を防ぐガードが、収集段階と投入計画段階の両方にあります。

ここを混ぜると、検索のヒット率は上がっても、記事生成では一次情報と補助文脈が同列になります。実際、自分はこの境界を曖昧にしたまま進めると、AIにとって都合のいい文面が上に来やすいと感じています。RAGの品質問題は、埋め込みモデルより前に、保存レイヤー設計でかなり決まります。

## まとめ

- Rawデータの正本と検索Indexを分けると、投入前に増分を止めて確認できる
- 今回の実測では `estimated_new_chunks=2602` に対して dry-run の `planned_chunks=3725` で、事前停止が効いた
- `--execute` なしでDBを書かない実装にすると、収集と投入の責務を分離しやすい
- `karte` schema を検索用に限定し、Rawは別保存にすると再計算と監査がしやすい
- canonical と context を分けないと、RAGはすぐに根拠汚染を起こす

RAGの運用で本当に怖いのは、検索精度が少し悪いことより、根拠の境界が見えないまま静かに混ざることでした。RawとIndexの分離は地味ですが、あとから効く設計だと感じています。
