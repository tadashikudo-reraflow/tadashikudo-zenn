---
title: "Claude Code scheduled tasks で朝刊・日次レポートを全自動生成する"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "自動化", "Python"]
published: false
---
# Claude Code scheduled tasks で朝刊・日次レポートを全自動生成する

## はじめに

毎朝のニュースチェック、日次のKPI集計、定例レポート作成 — エンジニアの「自動化したいけど cron + シェルスクリプトだと壊れやすい」系タスクの定番です。

筆者は半年ほど前から、Claude Code の **scheduled tasks** 機能でこの手の日次バッチを置き換えています。LLM の判断力をスケジューラに組み込めるので、「APIが落ちていたらスキップ」「収集件数が0件なら警告」といった条件分岐を自然言語で書けるのが想像以上に効きました。

この記事では、実運用している「朝刊ブリーフィング自動生成タスク」を題材に、scheduled tasks の実装パターンを共有します。

## 1. scheduled tasks とは何か

Claude Code（CLI）には、`~/.claude/scheduled-tasks/<task-name>/SKILL.md` にプロンプトを置いておくと、cron 式で定期起動してくれる仕組みがあります。仕組みは単純で：

- `SKILL.md` の **frontmatter** にモデル・実行モードを書く
- 本文は「あなたは○○エージェントです。以下の手順で…」という普通のプロンプト
- `mcp__scheduled-tasks__*` MCPツールで作成・一覧・更新ができる

普通の cron との違いは、**プロンプト本体が“手順書”として実行される**点です。途中の `bash` ブロックや `python -c` も Claude が解釈して実行し、エラーが出たら自分で別アプローチを試してくれます。

## 2. 最小構成のSKILL.md

朝刊タスクを単純化するとこんな構造になります。

```markdown
---
name: morning-briefing
model: claude-sonnet-4-6
description: 毎朝7時に朝刊ブリーフィングを自動生成しDaily Notesに出力
mode: bypassPermissions
---

あなたはニュース要約エージェントです。
以下の手順で本日の朝刊を生成してください。

## Step 1: ニュース収集

```bash
python3 ~/scripts/morning_preprocess.py
```

- 正常終了 → /tmp/morning_raw.json を読む
- 失敗 → WebSearch でフォールバック収集

## Step 2: 要約

収集結果を「テック / 政策 / マーケット」の3カテゴリに分類し、
各3〜5行で要約してください。

## Step 3: 出力

`~/notes/Daily/$(date +%Y-%m-%d).md` の `## 朝刊` セクションに追記。
```

ポイントは3つです。

- `model:` を必ず明示する（未指定だとデフォルトモデルが日によって変わる事故があった）
- `mode: bypassPermissions` で確認ダイアログを出さない（無人実行のため）
- Step を番号付きで分割すると、長期運用で「どこで失敗したか」が読みやすい

## 3. ゲートキーパーで“空振り”を防ぐ

scheduled tasks 最大の落とし穴は、**外部APIが落ちていたのに気づかず空っぽのレポートを出力すること**です。実運用では必ず「先頭にゲートキーパーを置く」パターンを採用しています。

```python
import json
from datetime import date

try:
    d = json.load(open('/tmp/morning_raw.json'))
    if d.get('date') == str(date.today()) and d.get('total_count', 0) > 0:
        print(f"READY:{d['total_count']}")
    else:
        print('STALE')
except Exception:
    print('MISSING')
```

このスクリプトの出力を SKILL.md に書いておけば、Claude が `READY:42` を見て「以降のWebSearchをスキップしていい」と自分で判断します。`STALE` / `MISSING` のときだけフルパイプラインを走らせる、というハイブリッド制御が自然言語で書けるのが scheduled tasks の真価です。

## 4. 投稿系タスクで遭遇した実エラー

外部API（記事投稿系）への `curl` を含むタスクで、過去に2回ほど **重複POST** を起こしました。原因はどちらもシェル変数経由のJSONパースです。

NG パターン:

```bash
RESPONSE=$(curl -s -X POST https://api.example.com/items \
  -d "$(python3 -c "print(json.dumps({'body': open('article.md').read()}))")")

URL=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['url'])")
```

何が起きるか：

- 記事本文の改行・タブ・制御文字がシェル展開で壊れる
- `python3` 側が `Invalid control character` で落ちる
- それでも `curl` は完了しているので投稿は成功する
- 次回リトライで **同じ記事をもう一度投稿** してしまう

OK パターン（ファイル経由でシェルを完全に介さない）:

```bash
python3 -c "
import json
body = open('article.md').read()
payload = json.dumps({'title': 'x', 'body': body})
open('/tmp/payload.json', 'w').write(payload)
"
curl -s -X POST https://api.example.com/items \
  -H "Authorization: Bearer ${YOUR_API_TOKEN}" \
  --data-binary @/tmp/payload.json \
  -o /tmp/response.json

python3 -c "
import json
d = json.load(open('/tmp/response.json'))
print(d.get('url', 'ERROR'))
"
```

ポイント：

- JSONはファイルに書き出して `--data-binary @file` で渡す
- レスポンスも `-o` でファイル保存し、`json.load(open(...))` で読む
- シェル変数を経由した瞬間に制御文字事故の可能性が生まれる

## 5. 自動修正 → 再スキャンの2段ループ

もう一つの実用パターンが「**チェック → 自動修正 → 再チェック → それでも駄目なら保留**」の2段ループです。投稿系タスクで個人情報・APIキー漏洩を防ぐのに使っています。

```python
ERROR_PATTERNS = {
    "実APIキー":  (r'sk_live_[A-Za-z0-9]{20,}', 'YOUR_API_KEY'),
    "実メール":   (r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}', 'user@example.com'),
}

text = open('article.md').read()
errors = {n: re.findall(p, text) for n, (p, _) in ERROR_PATTERNS.items() if re.findall(p, text)}

if errors:
    fixed = text
    for _, (pat, repl) in ERROR_PATTERNS.items():
        fixed = re.sub(pat, repl, fixed)
    open('article.md', 'w').write(fixed)

    # 再スキャン
    text2 = open('article.md').read()
    errors2 = {n: re.findall(p, text2) for n, (p, _) in ERROR_PATTERNS.items() if re.findall(p, text2)}
    if errors2:
        # status を hold に更新して投稿しない
        sys.exit(1)
```

LLMはどうしても確率的に守れないルールがあります。**決定論的なregexで物理的に止める層**を一段挟んでおくと、心理的な安心感が段違いです。

## 6. 運用してわかった3つの教訓

最後に、半年運用した中で得た知見を3つ挙げます。

- **冪等性を最優先する**: 同じタスクを2回走らせても結果が同じになるよう設計する。投稿系は「本日分が既にあるか」を最初にAPIで確認する
- **モデルを必ず明示する**: frontmatter の `model:` を省略しない。日次レポートのトーンが日によってブレる原因になる
- **ログは Markdown で残す**: scheduled tasks の実行結果を別ファイル（例: Daily Notesの末尾）に追記しておくと、後から `grep` で「どのステップが何回失敗したか」を可視化できる

## まとめ

- Claude Code の scheduled tasks は `~/.claude/scheduled-tasks/<name>/SKILL.md` を置くだけで動く
- 「ゲートキーパー → メイン処理 → 自動修正 → 再チェック」の4段構成が安定する
- 外部API投稿は **シェル変数経由のJSONを避けて** ファイル渡しにする
- LLMに守らせたいルールは、決定論的なregex/スクリプトで二重化する
- frontmatter の `model:` 明示と冪等性設計を最初から意識する

cron + bash で組んでいた日次バッチを少しずつ置き換えていますが、「APIが落ちていたら自分で別ルートを試す」「壊れたデータが来たら保留してSlackに通知する」のような“判断を伴う自動化”との相性が圧倒的に良いです。次の自動化タスクで試してみてください。
