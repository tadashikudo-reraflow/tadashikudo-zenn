---
title: "Codex runtimeのバイナリをGit管理しない 自動pushを止める運用設計"
emoji: "🧭"
type: "tech"
topics: ["codex", "git", "automation", "governance", "claudecode"]
published: true
---

## はじめに

Codex を毎日使うようになってから、設定や skill より先に運用を壊したのは runtime 資産の扱いでした。具体的には `~/.codex/computer-use/Codex Computer Use.app` が Git 追跡されたまま残っていて、更新のたびに巨大な差分が出る状態でした。さらに Vault 側には commit 後に自動で `git push` する前提が残っていて、「commit はしてよいが push は承認後だけ」という今のルールと衝突していました。

2026-05-25 にこの2点をまとめて整理し、2026-06-17 時点で状態を再確認しました。この記事では、そのときに実際にやった変更と、今も崩れていない運用の最小形を残します。

## まず壊れていたのは「変更量」ではなく「変更の種類」

最初の問題は `.app` バンドルを Git で持っていたことです。`~/.codex` の commit `673860c` は `computer-use/Codex Computer Use.app` を追跡対象から外した変更で、実際の差分は 82 files changed / 1845 deletions でした。つまり設定ファイルが少し揺れていたのではなく、署名済みのアプリ bundle を丸ごと履歴に抱えていた状態です。

一方で 2026-06-17 時点の実体確認では、`Codex Computer Use.app` 自体はローカルに残っており、bundle 配下の entry 数は 253 ありました。しかし `git -C ~/.codex ls-files 'computer-use/*.app/*'` は 0 件です。ここで重要なのは、「使うために存在する」と「履歴で管理する」は別だと割り切ることでした。

実際に `.gitignore` へ入っているのは次のような runtime 向けパターンです。

```gitignore
computer-use/*.app/
computer-use/config.json
hooks.json.bak.*
hooks/*.bak.*
```

この変更以降、Codex / Computer Use の更新で署名・plist・provision profile の差分を追いかける必要がなくなりました。私の環境では「ローカルに置くべき実体」と「Git で再現すべき設定」の境界をここでやっと分けられました。

## commit と push を同じ操作にしない

もうひとつ厄介だったのは、自動化された `push` です。以前のメモでは Vault の `post-commit` hook が `git push origin main` を呼ぶ前提になっていましたが、現行ルールではこれは明確に危険です。commit はローカル整頓ですが、push は外部反映だからです。

この反省から、以後の運用はかなり単純にしました。

```bash
git -c core.hooksPath=/dev/null commit -m "..."
# push は承認後だけ別コマンドで実行する
```

2026-06-17 に Vault 配下を再確認したところ、`post-commit` hook は見つかりませんでした。少なくとも「気づかないうちに commit が外部反映まで進む」経路は残っていない状態です。これは小さな変更ですが、運用上はかなり大きくて、AI エージェントに commit を任せる場面でも承認境界を保ちやすくなりました。

ここで効いたのは、hook を賢くすることではなく、責務を減らしたことです。commit hook に品質チェックや軽い整形を載せるのはまだ分かりますが、push まで混ぜると「失敗時に何が起きたか」が急に見えづらくなります。特に自動化タスクでは、hook の副作用まで含めてレビューし続けるのはコストが高すぎました。

## 今の運用ルール

今は次の3点だけを守るようにしています。

1. `.app` や署名済み bundle は runtime binary とみなし、Git の再現対象にしない。
2. `config.json` や backup hook のような揮発ファイルも `.gitignore` に寄せる。
3. commit と push を分離し、必要なら `core.hooksPath=/dev/null` で hook 影響を切って commit する。

この設計にしてから、「差分はあるが意味が薄い」「commit したつもりが外部反映まで進む」という2種類の事故を同時に減らせました。設定を増やして防いだというより、Git に持たせる責任を削ったのが効いた感覚です。

## まとめ

- runtime binary は「存在が必要」でも「履歴管理が必要」とは限らない
- `.app` bundle を Git から外すだけで、更新時の巨大差分はかなり減る
- `push` は hook の副作用にせず、承認つきの明示操作として分離した方が安全
- AI エージェント運用では、hook を賢くするより承認境界を単純化した方が壊れにくい
- まず確認すべきは diff の量ではなく、その diff が設定変更なのか runtime 更新なのかという種類
