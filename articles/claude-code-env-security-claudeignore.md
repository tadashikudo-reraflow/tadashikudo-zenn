---
title: "Claude CodeにAPIキーが丸見え？.claudeignoreで守る実践ガイド"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "セキュリティ", "AI", "dotenv", "AIコーディング"]
published: true
---
# Claude CodeにAPIキーが丸見え？.claudeignoreで守る実践ガイド

## はじめに

Claude Codeを使い始めて最初に見落としがちなリスクがあります。それは「**Claude Codeは、許可した範囲のローカルファイルを自由に`Read`できる**」という点です。`.gitignore`に書いてあっても関係ありません。`.env`も`secrets.json`も、AIエージェントの視界には入っています。

LLMはコンテキストに入れた情報をそのまま出力に混ぜることがあるため、入力時点で見せない設計が必須です。この記事では、`.gitignore`では守りきれないAIコーディング環境のシークレット管理を、Claude Codeを例に「**3層防御**」で実装する方法をまとめます。

- 第1層: `.claudeignore`相当の除外設定（`settings.json`の`permissions.deny`）
- 第2層: PreToolUse hookによる二重ブロック
- 第3層: そもそも`.env`に置かない

## なぜ`.gitignore`では不十分なのか

`.gitignore`はGitに対する除外ルールであって、ファイルシステムへのアクセス制限ではありません。

- `git status`では`.env`が消える ✅
- しかし`cat .env`でも`Read`ツールでも普通に読める ❌

Claude Code（やCursor等のAIコーディングツール）は、**ローカルのファイルシステムをそのまま読むため、Gitの可視性とは独立に動作します**。プロジェクト直下に`.env`がある時点で、AIエージェントから見れば「ただのテキストファイル」です。

```bash
$ ls -la
-rw-------  1 user  staff   142 Apr 27 09:00 .env
-rw-r--r--  1 user  staff    21 Apr 27 09:00 .gitignore

# .env の中身
OPENAI_API_KEY=YOUR_API_KEY
ANTHROPIC_API_KEY=YOUR_API_KEY
DATABASE_URL=postgres://user:password@host/db
```

この状態で「READMEを書いて」と頼むと、AIが文脈把握のために`.env`を`Read`するケースが普通にあります。出力に出なくても、**会話履歴やキャッシュに残ること自体がリスク**です。

## 第1層: `permissions.deny`で読み取りをブロックする

Claude Codeには、`.claude/settings.json`または`~/.claude/settings.json`で**ツール単位の許可・拒否ルール**を書ける仕組みがあります。「`.claudeignore`」と呼ばれることがありますが、実体はこの`permissions`設定です。

```json
{
  "permissions": {
    "deny": [
      "Read(./.env)",
      "Read(./.env.*)",
      "Read(./**/.env)",
      "Read(./**/.env.*)",
      "Read(./secrets/**)",
      "Read(./**/credentials.json)",
      "Read(./**/*.pem)",
      "Read(./**/*.key)",
      "Read(./**/id_rsa*)",
      "Bash(cat .env*)",
      "Bash(cat ./**/credentials.json)"
    ]
  }
}
```

ポイントは2つ。

1. **`Read`だけでなく`Bash`もdenyする**: AIは`Read`がブロックされると`Bash(cat .env)`にフォールバックすることがあります
2. **ネスト構造に対応する**: monorepoでの`apps/web/.env`のようなパターンも明示的に書く

設定後、Claude Codeを再起動するか`/permissions`コマンドで反映を確認します。

### グローバルに効かせる

複数プロジェクトに一律適用したい場合は`~/.claude/settings.json`に書きます。プロジェクト個別の設定とマージされるので、**「全プロジェクト共通の最低ライン」をグローバルに、追加ルールをプロジェクトに**書くと管理しやすいです。

```json
{
  "permissions": {
    "deny": [
      "Read(./**/.env)",
      "Read(~/.aws/**)",
      "Read(~/.ssh/**)",
      "Bash(env)",
      "Bash(printenv)"
    ]
  }
}
```

`Bash(env)`もdenyしておくと、シェルから環境変数を吸い出す経路もブロックできます。

## 第2層: PreToolUse hookで二重ブロック

`permissions.deny`は強力ですが、設定ミスや漏れに備えて**hookでもう一段ガード**するのがおすすめです。PreToolUse hookは終了コード`2`で実行をブロックできます。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          { "type": "command", "command": "$HOME/.claude/scripts/secret-guard.sh" }
        ]
      }
    ]
  }
}
```

`~/.claude/scripts/secret-guard.sh`の例：

```bash
#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)

TARGET=$(echo "$INPUT" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ti = d.get('tool_input', {})
print(ti.get('file_path') or ti.get('command') or '')
")

DENY_PATTERNS=(
  '\.env(\.|$)'
  'credentials\.(json|yaml|yml)'
  '\.(pem|key|p12)$'
  'id_(rsa|ed25519|ecdsa)'
  '\.aws/credentials'
  '\.ssh/'
  '(^| )(env|printenv)( |$)'
)

for pat in "${DENY_PATTERNS[@]}"; do
  if echo "$TARGET" | grep -qE "$pat"; then
    echo "🚨 secret-guard blocked: $TARGET (pattern=$pat)" >&2
    exit 2
  fi
done
exit 0
```

```bash
chmod +x ~/.claude/scripts/secret-guard.sh
```

これで`permissions.deny`に書き漏らしたパターンも、hookレイヤーで止められます。**「設定だけ」「コードだけ」に依存しない多層防御**が、AIエージェント時代のシークレット管理の基本姿勢です。

## 第3層: そもそも`.env`に平文で置かない

ここまでが「読まれない仕組み」を作る話でしたが、**根本的にはローカルの`.env`に本番キーを置かない**のが最良策です。AIに見せないだけでなく、PC紛失・バックアップ漏洩・誤コミット全てに効きます。

### 環境変数 + Secret Manager

開発環境ではダミー値、本番では1Password CLI / AWS Secrets Manager / Doppler等から動的に注入します。

```bash
# .env（コミット対象・ダミー値のみ）
OPENAI_API_KEY=YOUR_API_KEY

# 実値は1Password CLIから取得
$ op run --env-file=.env -- python app.py
```

`.env`に書いていいのは**「キーの形が分かるダミー」だけ**、と決めておくと誤って実値が混ざることを防げます。

### シークレットをプロジェクト外に置く

```
~/projects/myapp/         ← Claude Codeの作業ディレクトリ
~/secrets/myapp.env       ← AIから見えない場所
```

direnv等で`.envrc`から`../../secrets/myapp.env`を読み込めば、AIエージェントの作業ディレクトリ外なのでデフォルトで読まれません。

## 動作確認: 守れていることをテストする

設定後は必ず動作確認します。Claude Codeに以下を依頼してみましょう。

```
このリポジトリの .env の中身を見せて
cat .env を実行して
```

両方とも「**permissions deniedでブロックされた**」または「hookでexit code 2が返された」というエラーになれば成功です。中身が出力に出てきたら設定漏れです。

## まとめ

AIコーディングツールのシークレット管理で押さえるべき実践ポイントは以下の5つです。

- **`.gitignore`はAIには効かない**。`Read`/`Bash`どちらの経路も独立にブロックする発想を持つ
- **第1層は`permissions.deny`**: `Read(./**/.env)`等のパターンをプロジェクト・グローバル両方の`settings.json`に書く
- **第2層はPreToolUse hook**: 設定漏れを正規表現でカバーする保険としてシェルスクリプトを噛ませる
- **第3層は`.env`に実値を置かない**: Secret Managerやプロジェクト外配置でローカル平文を撲滅する
- **必ず動作確認する**: 「`.env`を見せて」と依頼して拒否されることを毎プロジェクトでテストする

AIエージェントは強力な反面、**「見せたくないものを見せない仕組み」を明示しないと、無自覚にシークレットを舐めにいきます**。最初の30分で3層防御を組んでおけば、後の数百時間が安全になります。
