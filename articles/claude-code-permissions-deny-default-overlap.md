---
title: "Claude Codeのpermissions.denyを厚く書いてる人へ：たぶん全部デフォルトで防がれてる"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "セキュリティ", "permissions", "設定"]
published: false
---
# Claude Codeのpermissions.denyを厚く書いてる人へ：たぶん全部デフォルトで防がれてる

## はじめに

Claude Codeを使い始めると、`settings.json`の`permissions.deny`に色々書きたくなる気持ちはわかる。

```json
{
  "permissions": {
    "deny": [
      "Bash(rm -rf /)",
      "Bash(rm -rf ~)",
      "Bash(chmod -R 777 /)",
      "Bash(git push --force*)"
    ]
  }
}
```

でも正直に言う。**インタラクティブセッションで使ってるなら、これらはほぼ全部Claude自身が既に判断してブロックしてる**。

この記事では「どこで何が防がれているか」の実際の防御レイヤーを整理し、denyリストに何を書くべきかをはっきりさせる。

---

## Claude Codeの権限モデルを正確に理解する

まずは基本から。Claude Codeのpermissions設定には3つの重要な概念がある。

### defaultMode

```json
{
  "permissions": {
    "defaultMode": "default"
  }
}
```

`defaultMode`の値によって動作が大きく変わる。

| defaultMode | 動作 |
|---|---|
| `default` | 通常インタラクティブ。危険な操作は都度ユーザー確認 |
| `acceptEdits` | 編集系は自動許可。Bashなどは確認あり |
| `bypassPermissions` | **全操作を確認なしで実行**。許可プロンプトが一切出ない |

`default`モードで動かしているなら、Claudeは危険と判断した操作を自分から確認してくる。`rm -rf`系コマンドをユーザーが明示的に頼まない限り、Claudeはそもそも実行しようとしない。

### 重要な気づき：defaultモードなら「安全」はClaudeが担う

`defaultMode: default`（デフォルト値）で動かしているインタラクティブセッションでは：

- Claudeは破壊的なコマンドを実行する前に**必ず確認を求める**
- ユーザーが承認しなければ実行されない
- `permissions.deny`がなくても、ユーザーが「やめて」と言えば止まる

つまり、**対話型セッションでは`deny`リストは保険の二重掛けにしかなっていない**ことが多い。

---

## 問題は「bypassPermissionsモード」にある

では`deny`リストが**本当に必要**になるのはいつか。

答えは**`bypassPermissions`モードで動かすとき**だ。

```json
{
  "permissions": {
    "defaultMode": "bypassPermissions"
  }
}
```

このモードでは、許可プロンプトが**一切出ない**。Claudeが「これは危険かもしれない」と思っても、確認なしに実行される。

典型的なユースケース：
- スケジュールドタスク（夜間自動実行）
- CI/CDパイプライン内でのClaude Code実行
- `claude --dangerously-skip-permissions`フラグを使ったAgentic実行

この環境では`deny`リストが**最後の砦**になる。対話的なセーフガードがないので、設定で明示的に防がないとやりたい放題になる。

---

## 実際のdenyリストに何を書くべきか

`bypassPermissions`前提で設計する場合、denyに書く価値があるのはこういう項目だ：

```json
{
  "permissions": {
    "deny": [
      "Bash(rm -rf /)",
      "Bash(rm -rf ~)",
      "Bash(rm -rf /*)",
      "Bash(> /dev/sda*)",
      "Bash(mkfs*)",
      "Bash(dd if=*of=/dev*)",
      "Bash(chmod -R 777 /)",
      "Bash(git add -A*)",
      "Bash(git add --all*)",
      "Bash(git add .*)",
      "Bash(git push --force*)",
      "Bash(git push -f*)",
      "Bash(git reset --hard*)",
      "Bash(git clean -f*)",
      "Bash(git branch -D *)",
      "Bash(git checkout .*)",
      "Bash(git restore .*)"
    ],
    "defaultMode": "bypassPermissions"
  }
}
```

カテゴリ別に整理すると：

**システム破壊系**
- `rm -rf /`, `rm -rf ~`, `rm -rf /*`
- `mkfs*`, `dd if=*of=/dev*`, `> /dev/sda*`

→ OSレベルの取り返しのつかない操作。bypassモードでも絶対に走らせない。

**セキュリティ弱化系**
- `chmod -R 777 /`

→ 権限を全開放する操作。

**Git危険操作系**
- `git add -A`, `git add --all`, `git add .` → 機密ファイルの誤コミット防止
- `git push --force`, `git push -f` → 共有ブランチの上書き防止
- `git reset --hard`, `git clean -f` → 未コミット作業の消失防止
- `git branch -D`, `git checkout .`, `git restore .` → ローカル変更の破棄防止

---

## denyだけでは足りない：Hooksとの使い分け

`deny`は**グロブマッチング（`*`ワイルドカード）**でしかブロックできない。正規表現や文脈依存の判断が必要なら`PreToolUse`フックの出番だ。

フックはinlineのbashコマンドで書ける。`CLAUDE_TOOL_INPUT`環境変数にツールへの入力JSONが入るので、コマンド文字列を取り出して判定する：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'CMD=$(python3 -c \"import os,json; d=json.loads(os.environ.get(\\\"CLAUDE_TOOL_INPUT\\\",\\\"{}\\\")); print(d.get(\\\"command\\\",\\\"\\\"))\"); if echo \"$CMD\" | grep -qE \"rm\\s+-rf\\s+[~/.]|DROP\\s+(TABLE|DATABASE)\"; then echo \"BLOCKED: Dangerous command\" >&2; exit 2; fi'",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

可読性を上げたいなら外部スクリプトに切り出すのが現実的だ：

```bash
# ~/.claude/hooks/my-guard.sh
#!/bin/bash

# CLAUDE_TOOL_INPUT からコマンド文字列を取得
CMD=$(python3 -c "
import os, json
d = json.loads(os.environ.get('CLAUDE_TOOL_INPUT', '{}'))
print(d.get('command', ''))
")

# 正規表現で危険パターンを検出
if echo "$CMD" | grep -qE "rm\s+-rf\s+[~/.]|DROP\s+(TABLE|DATABASE)"; then
  echo "BLOCKED: Dangerous command detected" >&2
  exit 2
fi

# 外部API CLIを明示的な承認なしに呼ぼうとしていないか
if echo "$CMD" | grep -qiE "curl.*api\.openai\.com|gemini\s"; then
  echo "BLOCKED: External API call requires explicit approval" >&2
  exit 2
fi
```

`exit 2` を返すとClaudeはそのコマンドをブロックする（`exit 1` は通過扱いになるので注意）。

denyとhooksの使い分けの基準：

| 判断基準 | deny | hooks |
|---|---|---|
| 単純なグロブマッチでOK | ✅ | — |
| 正規表現が必要 | — | ✅ |
| コンテキストによって変える | — | ✅ |
| 設定ファイルをシンプルに保ちたい | ✅ | — |
| 複数条件の組み合わせが必要 | — | ✅ |

---

## まとめ：設計を決めてからdenyリストを書く

1. **まず`defaultMode`を決める** — インタラクティブ専用なら`default`で十分。Agentic/スケジュール実行なら`bypassPermissions`が現実的
2. **`bypassPermissions`を使うならdenyは必須** — 確認プロンプトが出ないので、ハードブロックが最後の防衛線になる
3. **denyの限界を知る** — グロブマッチングのみで正規表現は使えない。コンテキスト依存のブロックはhooksで書く
4. **二重防御の設計をする** — deny（シンプルな絶対禁止）+ hooks（柔軟な文脈判断）の組み合わせが堅牢
5. **Git操作の`deny`は地味に重要** — `git add -A`や`git push --force`をスケジュールドタスクで走らせると取り返しがつかない

インタラクティブで使っているだけなら`deny`の追加は気分の問題かもしれないが、自動実行系を組む瞬間から設計は変わる。`defaultMode`の選択とdeny/hooksの棲み分けを意識して設定しよう。
