---
title: "Claude Code中心運用からCodex中心運用へ移行する判断基準"
emoji: "🧭"
type: "tech"
topics: ["codex", "claudecode", "automation", "workflow", "ai"]
published: true
---

## はじめに

Claude Code で積み上げた運用を、どの単位で Codex に移すべきかを整理したかったので、ここ数週間は自分の環境を実測しながら移行基準を固めていました。結論から言うと、設定やプロンプトを丸ごとコピーするより、「その運用が何を達成しているか」を capability 単位で切り分けてから移すほうが安定しました。

2026-06-15 時点で手元を確認すると、`~/.codex/automations` の `automation.toml` は 15 件、`~/.claude/scheduled-tasks` 側の `SKILL.md` / `config.toml` / `automation.toml` は 24 件ありました。つまり、まだ 1:1 で移植し切った状態ではありません。さらに `python3 ~/.codex/skills/claude-context-sync/scripts/check_claude_codex_drift.py` の出力でも、`codex-hook-coverage` や `tooling-mcp-cli-parity` は `partial` のままでした。移行は「存在するから完了」ではなく、「どこまで実運用を肩代わりできたか」で見ないと危ない、というのが先に見えた事実です。

## 1. まず移すのはファイルではなく capability

自分が最初にやって失敗しにくかったのは、Claude 側の資産を「機能」に分解することでした。`~/.codex/references/claude-to-openai-migration.md` と、Vault 側の `02_Knowledge/decisions/2026-05-24_codex-primary-runtime-migration.md` / `02_Knowledge/playbooks/ai/codex-primary-runtime-migration-spec.md` を読むと、考え方はかなり一貫しています。

- 日常運用の中心はコード編集、Vault運用、レビュー、定期タスク、安全ガード
- それぞれに「Codex 側で既に代替があるか」「まだ shadow 並走にすべきか」を付ける
- 未移植のものは、名前があるだけで完了扱いにしない

この見方に変えてから、移行判断がかなり楽になりました。たとえば review 系は `covered` でも、hook や CLI parity は `partial` のまま残ることがあります。ここを全部「移行済み」と雑にまとめると、あとで compact 後の復元や自動化の二重発火で詰まりやすいです。

## 2. 「exists」より「artifact が回るか」を見る

移行 spec で特に効いたのは、`target=exists` を完了条件にしないという考え方でした。実際、自分の環境でも「ファイルはあるが、運用としてはまだ足りない」ものが残っています。

```bash
# 2026-06-15 に確認した実測
find ~/.codex/automations -maxdepth 2 -name automation.toml | wc -l
# => 15

find ~/.claude/scheduled-tasks -maxdepth 2 \
  \( -name SKILL.md -o -name config.toml -o -name automation.toml \) | wc -l
# => 24

python3 ~/.codex/skills/claude-context-sync/scripts/check_claude_codex_drift.py
# covered と partial が混在
```

この差分が見えてからは、移行の完了条件を「ファイルがある」ではなく、次の3点で見るようになりました。

- 実行結果の成果物を確認できるか
- 二重発火しないか
- 承認ゲートが Codex 側にも保たれているか

特に scheduled task はここを曖昧にすると危険です。Claude 側と Codex 側に同名っぽい仕組みが残っていても、owner を決めずに放置すると「片方は止まる」「両方走る」のどちらかになりがちでした。

## 3. いまの自分の基準

いまは、新しい運用を Codex に移す前に次の順で見ています。

1. その運用を capability 名で一言にできるか
2. Codex 側に対応する skill / automation / hook があるか
3. `covered` か `partial` かを drift チェックで確認したか
4. 実成果物や dry-run で「回る」ことを見たか
5. push、公開、送信の承認ゲートが混ざっていないか

この順序にしてから、「とりあえず移したが、運用としては未完成だった」という事故が減りました。自分の環境では、Claude を即廃止するより、read-only の参照元として残しながら Codex 側を capability 単位で埋めるほうが現実的でした。

## まとめ

- Claude Code から Codex への移行は、設定移植より capability 分解から始めると判断しやすい
- `exists` 判定だけでは弱く、成果物確認・二重発火防止・承認ゲート維持まで見たほうが安全
- 2026-06-15 の自分の環境では、Codex automation 15 件に対して Claude 側 artifacts は 24 件あり、まだ完全一致ではなかった
- drift チェックで `covered` と `partial` を分けて見ると、次に埋める穴が明確になる
- いきなり全面移行するより、Claude を read-only 参照元に残して段階移行するほうが壊れにくい
