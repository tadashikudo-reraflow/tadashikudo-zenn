---
title: "「スクリプトはあるがスキルから参照されない」を防ぐ：技術的負債を自動解消する統合ルール"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "スキル", "自動化", "設計"]
published: true
---
# 「スクリプトはあるがスキルから参照されない」を防ぐ：技術的負債を自動解消する統合ルール

## はじめに

Claude Code を本格運用していると、いつのまにかこんな状態になっていないでしょうか。

- `scripts/` 配下に便利スクリプトが 50 個以上
- 各 SKILL.md にはコマンド例があるが、**実在しないパス**やすでに削除されたコマンドを指している
- 同じ目的のスクリプトがプロジェクト別に 3 つ並んでいて、どれが正本かわからない

筆者の環境では、3 か月ほど運用したところで「スクリプトはあるがスキルから参照されていない」「スキルにあるパスは実在しない」という地雷が 20 件以上見つかりました。これは**最大の技術的負債**です。AI Agent はドキュメント（SKILL.md）に書かれた手順をそのまま実行するため、書かれているパスが嘘だと**毎回サイレントに失敗**します。

この記事では、この負債を**自動で解消する 3 ルール統合運用**を、実例つきで紹介します。Claude Code に限らず、Cursor / Aider / OpenHands など SKILL.md ライクな指示書を持つ AI Agent 全般に応用できます。

## 問題：なぜ「スキルが嘘をつく」のか

典型的な腐敗フローはこうです。

```
[Day 1] PJ-A 内で utility.py を作成 → SKILL.md にパス記載
[Day 30] PJ-B でも同じ処理が必要 → コピペで PJ-B/utility.py を作成
[Day 60] PJ-A の utility.py をリファクタ → PJ-B 側は古いまま
[Day 90] PJ-C の SKILL.md は PJ-A のパスを参照しているが、
         そのスクリプトはすでに別ディレクトリへ移動済み → SKILL.md が嘘になる
```

問題の本質は **「スクリプトの所在」と「スキルが参照するパス」が手動同期** になっていることです。人間が両方を頭で覚えて整合させるしかなく、当然忘れます。

## 解決策：CLAUDE.md に「3 ルール」を書く

筆者は CLAUDE.md（グローバル設定）に以下のセクションを追加してから、この種のドリフトがほぼゼロになりました。

```markdown
## [G] スクリプト↔スキル統合

PJディレクトリにスクリプトを作成・発見したとき**自動で実施**:

1. **汎用スクリプトは `~/workspace/scripts/` に配置**
   （PJローカルのみに置かない・複数PJで使えるなら必ず scripts/）
2. スキルがそのスクリプトを使う場合、スキル内のコマンド例を**実在パス**で書く
3. 新規スクリプト作成時は **CLI インベントリを更新**
   （スクリプト名・用途・必須条件・呼び出し方・🔴🟡🟢フラグ）

> 「スクリプトはあるがスキルから参照されない」状態が最大の技術的負債。
> 発見したら即修正。
```

ポイントは「ルールを 3 つに絞る」「優先順位を明示する」「最後に "発見したら即修正" と書く」の 3 つです。Agent は指示が短いほど忠実に従います。

## ルール 1：汎用化レイヤーを 1 つ決める

最初に「全プロジェクト横断で使うスクリプトの置き場」を**たった 1 つ**決めます。筆者は `~/workspace/scripts/` にしました。

```bash
# 良い例：複数PJから呼ばれるユーティリティ
~/workspace/scripts/kw_volume.py        # SEO KW ボリューム確認
~/workspace/scripts/ga4_cli.py          # GA4 API クライアント
~/workspace/scripts/ultraplan-preflight.sh  # 起動前セキュリティ検査

# 悪い例：PJ-A 内に置いて他PJからは相対パスでアクセス
~/workspace/pj/PJ-A/scripts/kw_volume.py
~/workspace/pj/PJ-B/scripts/kw_volume.py  # コピペ重複
```

判断基準は単純で「**2 つ目の PJ から使いたくなった瞬間** に `~/workspace/scripts/` へ昇格」です。1 PJ専用のうちは PJ ローカルでも構いません。

## ルール 2：SKILL.md には「実在パス」しか書かない

SKILL.md の `## Step 2: 環境準備` のような節で、こう書かれているケースがよくあります。

```markdown
## Step 2: ボリューム確認
SEO 記事を書く前に必ず実行：
\`\`\`bash
python3 kw_volume.py "${KEYWORD}"
\`\`\`
```

これでは Agent が `python3 kw_volume.py` をカレントディレクトリで探して失敗します。**絶対パスまたは展開可能な変数で書く**のが鉄則です。

```markdown
## Step 2: ボリューム確認
\`\`\`bash
python3 ~/workspace/scripts/kw_volume.py "${KEYWORD}"
\`\`\`
```

`~/workspace/scripts/` をルール 1 で固定しているからこそ、SKILL.md にハードコードしても腐りにくくなります。逆に「いつか移動するかもしれない」という曖昧な置き場だと SKILL.md も曖昧にしか書けません。

## ルール 3：CLI インベントリで一元台帳化

`02_Knowledge/references/ai-tools/cli-inventory.md` のような**一元台帳**を作り、すべての CLI / スクリプトをカテゴリ別に記録します。

```markdown
## 🔴 HARDBLOCK（billing=paid / 課金確認必須）

| ツール | 用途 | 必須ENV | 備考 |
|--------|------|---------|------|
| `gemini` | Gemini CLI（🔴 従量課金） | `GEMINI_API_KEY` | API課金ゲート対象 |

## ⚠️ WARN（高リスク・write系）

| ツール | 用途 | 必須ENV | 備考 |
|--------|------|---------|------|
| `kw_volume.py` | KWボリューム確認 | `DATAFORSEO_LOGIN` | vol<10で停止 |

## 📋 その他（log / none）

| ツール | 用途 | scope | runtime |
|--------|------|-------|---------|
| `ga4_cli.py` | GA4 REST API クエリ | global | claude, codex |
```

筆者の環境では、この台帳を **registry.toml** から自動生成しています。手書きは腐るので、必ず自動生成のパイプラインを組みます。

```python
# scan-cli.py の骨子
import tomllib
from pathlib import Path

def generate_inventory(registry_path: Path, output_path: Path):
    with open(registry_path, "rb") as f:
        registry = tomllib.load(f)

    tiers = {"hardblock": [], "warn": [], "other": []}
    for tool in registry["tools"]:
        billing = tool.get("billing", "none")
        risk = tool.get("risk", "none")
        if billing == "paid":
            tiers["hardblock"].append(tool)
        elif risk in ("write", "destructive"):
            tiers["warn"].append(tool)
        else:
            tiers["other"].append(tool)

    md = render_markdown(tiers)
    output_path.write_text(md, encoding="utf-8")
```

「ツールが増えた」と思ったら `registry.toml` に 1 行追記して `python3 scan-cli.py --generate-doc` を流すだけ。SKILL.md からは「インベントリを見て該当ツールを使え」と指示できるので、Agent が**未知のスクリプトを誤起動するリスク**も下がります。

## 自動検知：ドリフトを CI で潰す

3 ルールを徹底しても、人間（または Agent）が運用ミスをすることはあります。そこで**ドリフト検知スクリプト**を 1 本書いて、週次で回します。

```python
#!/usr/bin/env python3
"""SKILL.md 内のスクリプト参照が実在するかチェック"""
import re
from pathlib import Path

SKILL_ROOT = Path.home() / ".claude" / "skills"
PATTERN = re.compile(r"(?:python3?|bash|sh)\s+(~?/[^\s`'\")]+\.(?:py|sh))")

def check():
    missing = []
    for skill_md in SKILL_ROOT.rglob("SKILL.md"):
        text = skill_md.read_text(encoding="utf-8")
        for m in PATTERN.finditer(text):
            path = Path(m.group(1).replace("~", str(Path.home())))
            if not path.exists():
                missing.append((skill_md, m.group(1)))
    return missing

if __name__ == "__main__":
    drift = check()
    if drift:
        for skill, ref in drift:
            print(f"[MISSING] {skill.relative_to(SKILL_ROOT)} → {ref}")
        exit(1)
    print("OK: すべての参照スクリプトが実在します")
```

GitHub Actions や cron で週次実行すれば、SKILL.md が嘘をつき始めた瞬間に検知できます。筆者は scheduled-task として登録し、週月曜 7:00 に実行 → Slack 通知という構成にしています。

## まとめ

- **「スクリプトはあるがスキルから参照されない」状態を技術的負債と認識する** — Agent は SKILL.md を信じて動くので、嘘の指示は毎回サイレント失敗を生む
- **汎用スクリプトは 1 か所に集約する**（例：`~/workspace/scripts/`）。2 つ目の PJ から使いたくなった瞬間に昇格させる
- **SKILL.md には実在パスしか書かない**。曖昧な場所への参照を作らない
- **CLI インベントリを自動生成にする**。手書き台帳は必ず腐るので、registry.toml → Markdown のパイプラインを最初に組む
- **ドリフト検知を CI 化する**。週次で SKILL.md 内のパスを実在チェックし、壊れた瞬間に通知

Claude Code をはじめ AI Agent の運用は、コードを書く以上に**「指示書のメンテナンス」が本体**です。3 ルール + 1 検知スクリプトで、指示書の嘘を構造的に潰していきましょう。
