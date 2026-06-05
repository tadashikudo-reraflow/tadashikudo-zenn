---
title: "NotebookLMをCodexで使うときの安全設計"
emoji: "📓"
type: "tech"
topics: ["codex", "notebooklm", "cli", "security", "ai"]
published: true
---

## はじめに

2026-06-05 に手元の Codex 環境を見直したところ、`/opt/homebrew/bin/notebooklm` がそのまま実行でき、`notebooklm --version` は `0.3.4` を返しました。以前は「NotebookLM は Claude Code 側の専用経路」と見ていましたが、この確認で前提が変わりました。

ただし、使えることと安全に使えることは別です。実際に叩くと `notebooklm status` は現在の notebook と conversation をローカルに返す一方、`notebooklm list --json` はこの制限環境ではネットワーク依存で失敗しました。つまり Codex から NotebookLM を扱うときは、まず「ローカル状態の確認」と「外部通信を伴う操作」を分けて設計しないと事故りやすいです。

この記事では、実際に確認できた CLI と skill ファイルを根拠に、Codex 側へ移したときに最低限守るべき境界線を3つに絞って整理します。

## 1. `status` と `list` を同列に扱わない

今回の確認でいちばん大きかったのは、`status` と `list` の性質が違うことです。

- `notebooklm status` は現在の context を即時に返した
- `notebooklm list --json` はネットワークに出られない環境では失敗した
- `notebooklm auth check --help` でも `--test` は network request を伴うと明記されていた

この差を無視して「まず一覧を取る」実装にすると、閉じた実行環境ではそれだけで詰まります。先にローカルで見える範囲を確認し、外に出る操作は別ゲートに分けるほうが安全です。

```bash
# 先にローカルで確認できるもの
notebooklm --version
notebooklm status
notebooklm auth check

# 外部通信を伴うものは別扱い
notebooklm list --json
notebooklm auth check --test
```

Codex では「read-only だから安全」とは限りません。NotebookLM CLI では read 系コマンドでも通信境界をまたぐものがある、というのが今回の実測でした。

## 2. `use` 前提ではなく `-n` 前提で書く

`notebooklm status` が notebook と conversation を保持していたので、CLI には共有 context があると分かります。Claude 側の元 skill と、Codex 側へ移した `~/.codex/skills/notebooklm/SKILL.md` の両方でも、並列実行時は `use` 依存を避ける前提でした。

help を確認すると、少なくとも次は `-n, --notebook` を受けます。

- `notebooklm ask`
- `notebooklm source list`
- `notebooklm source add`
- `notebooklm download report`

したがって、自動化や複数 agent から使うなら `use` で現在地を切り替えるより、毎回 notebook ID を明示したほうが安全です。

```bash
# 良い例: notebook を毎回明示する
notebooklm ask -n <notebook_id> "要点を3つに絞って"
notebooklm source list -n <notebook_id> --json
notebooklm download report -n <notebook_id> --dry-run

# 避けたい例: 共有contextに依存する
notebooklm use <notebook_id>
notebooklm ask "要点を3つに絞って"
```

単一セッションでは動いても、あとで別 agent や別タスクが同じ `~/.notebooklm` を触ると、原因が見えにくいズレになります。ここは「便利さより再現性」を取るべきでした。

## 3. `source add` と `download` を境界として扱う

CLI help を読むと、`source add` は URL、YouTube、既存ファイル、inline text を自動判定して投入できます。逆に言えば、これは単なるローカル処理ではなく、外部サービスへの送信入口です。

また `download report` はローカル書き込みを伴います。`--dry-run` があるので、保存前に対象を確認する流れを挟めます。

```bash
# 外部送信になるので、対象を明示した依頼だけ実行する
notebooklm source add -n <notebook_id> ./notes.md
notebooklm source add -n <notebook_id> "https://example.com/doc"

# まず保存せず確認する
notebooklm download report -n <notebook_id> --dry-run
```

自分の運用では、ここを境にルールを分けるのがいちばん分かりやすかったです。

- `status` `auth check` はローカル確認寄り
- `list` `ask` はネットワーク依存の読取
- `source add` は外部送信
- `download` はローカル生成物の確定

NotebookLM の回答自体も一次ソースにはしません。Codex 側 skill にもある通り、公開向けの主張は source fulltext や元資料で再確認する前提にしておくと、便利さと検証性を両立しやすいです。

## まとめ

- `notebooklm status` と `notebooklm list` は同じ read 系でも境界が違う。ローカル確認と外部通信を分けて設計する
- 共有 context があるので、Codex の自動化では `use` より `-n` を優先する
- `source add` は外部送信、`download` はローカル確定として別ゲートに置く
- `auth check --test` のように network 前提の確認は、閉じた実行環境では失敗しうる
- NotebookLM の回答はそのまま公開根拠にせず、元ソースで裏取りする

CLI が使えるようになった時点で「移植完了」と見なすと危ないです。実際には、どこから外部通信になり、どこで共有 context に依存し、どこでローカル成果物が確定するかまで切り分けて、はじめて Codex 側の運用に乗せられると感じました。
