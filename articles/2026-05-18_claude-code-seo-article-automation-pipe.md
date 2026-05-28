---
title: "月額$200のSEOツールを不要にした——Claude Codeで記事生成を全自動化した実録"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "SEO", "コンテンツ自動化", "生成AI"]
published: false
---
# 月額$200のSEOツールを不要にした——Claude Codeで記事生成を全自動化した実録

## はじめに

以前は月額$99〜$200のSEOツール（キーワードリサーチ・コンテンツ品質チェック・重複検知）を複数契約していました。

それを Claude Code のスケジュールタスク＋自作スクリプトで代替し、毎朝7時に「KW選定→記事生成→品質チェック→自動公開」まで無人で回るパイプラインを構築しました。本記事ではそのアーキテクチャと、実際に動いているコードの要点を紹介します。

---

## パイプラインの全体像

```
[毎朝7:00 JST / Claude Code scheduled-task]

① KWプランナー（Excel）から当日KWを選定
    ↓
② 重複チェック（4層防御）
    ↓ 重複なし
③ Claude が記事執筆（2,000〜4,000字）
    ↓
④ 品質スコアチェック（≥ 75点で通過）
    ↓
⑤ Supabase に INSERT → is_published=true で公開
```

1日1本、完全無人。エラー時は次候補KWで最大3回リトライし、それでも失敗したら Telegram 通知で止まります。

---

## ① KWプランナー（Excel）が中央管理

KWの一覧は Excel で管理しています。カラム構成はシンプルです。

| カラム | 内容 |
|--------|------|
| focus_kw | 対象キーワード |
| 実Vol(DataForSEO) | 月間検索ボリューム |
| 現在順位 | Google Search Console の平均順位 |
| ステータス | 未着手 / 公開済 / 重複スキップ / リライト候補 |

DataForSEO API でボリュームを定期取得し、vol=0 のKWは自動スキップします。「キーワード調査に$99/月払っていた時代」がこのExcel＋APIで代替できています。

```python
# DataForSEO でボリューム取得（簡略版）
import requests, base64

cred = base64.b64encode(b"user@example.com:your_password").decode()
resp = requests.post(
    "https://api.dataforseo.com/v3/keywords_data/google/search_volume/live",
    headers={"Authorization": f"Basic {cred}"},
    json=[{"keywords": ["ガバメントクラウド 移行"], "language_code": "ja", "location_code": 1009}]
)
volume = resp.json()["tasks"][0]["result"][0]["search_volume"]
```

月$2〜$5程度のAPI費用で、必要なKWだけ取得できます。

---

## ② 重複チェック：4層防御スクリプト

新規記事を書く前に「すでに似た記事が公開されていないか」を確認するのが最重要です。カニバリゼーションを防ぎ、Googleへの重複シグナルを避けるためです。

`check_kw_duplicate.py` という汎用スクリプトを作り、以下の4層で判定しています。

```
L4（最優先）: Canonical Cluster Ledger
    → YAML台帳でエイリアス・同義語を決定論的に管理。API呼び出しゼロ

L1: 核ワード一致
    → KWをトークン分割して記事タイトル/slugと照合

L2: Embedding 類似度（キャッシュ付き）
    → text-embedding-3-small で cosine similarity を計算
    → 記事のembeddingはキャッシュして2回目以降はKWのみ embed

L3: 閾値判定
    → keyword_match=true なら閾値0.50、それ以外は0.75

GSC ランクチェック（補完）
    → 上位候補slugで既存記事が15位以内にランクインしていればスキップ
```

```python
# 呼び出し例
import subprocess

result = subprocess.run(
    ["python3", "scripts/check_kw_duplicate.py", "--pj", "your_project", kw],
    capture_output=True
)
exit_code = result.returncode
# 0: 重複なし → 続行
# 1: 高類似度 → KWスキップ
# 2: GSCで既存記事ランクイン → リライト候補化
```

これ以前は「記事を書いてから重複に気づく」という無駄が多発していました。このスクリプトで事前にブロックできるようになっています。

---

## ③ Claude が記事執筆

重複チェックを通過したKWで、Claude に記事を生成させます。プロンプトには以下を含めます。

- focus_kw と検索意図（情報収集 / 比較 / 操作方法）
- 競合記事の構成（上位5記事を WebSearch で収集）
- 対象読者・文字数・Markdown 形式指定

生成後はそのまま使わず、次の品質チェックに通します。

---

## ④ 品質スコアチェック（75点ゲート）

`seo-quality-check` スキルで記事を採点し、75点未満なら自動修正ループに入ります。

チェック項目（抜粋）：

| 項目 | 観点 |
|------|------|
| KW密度 | focus_kw が本文に適切な頻度で出現しているか |
| 見出し構造 | H2/H3 が論理的に階層化されているか |
| コード例 | 技術記事なら実装コードが含まれているか |
| 文字数 | 2,000字以上か |
| 内部リンク候補 | 関連記事へのリンクが設定できるか |

```python
# 品質チェックの呼び出しイメージ
score = run_seo_quality_check(article_md, focus_kw)
if score < 75:
    article_md = auto_fix(article_md, score_details)
    score = run_seo_quality_check(article_md, focus_kw)  # 再チェック
```

有料のコンテンツ最適化ツール（Surfer SEO など月$99〜）が担っていた役割を、この自作スキルで代替しています。

---

## ⑤ Supabase に自動公開

品質チェックを通過したら、Supabase の `articles` テーブルに INSERT します。

```python
import os
from supabase import create_client

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
supabase.table("articles").insert({
    "slug": slug,
    "title": title,
    "content": html_content,
    "focus_kw": focus_kw,
    "is_published": True,
    "published_at": today_iso
}).execute()
```

ISR（Incremental Static Regeneration）設定のおかげで、INSERT 後数分でサイトに反映されます。

---

## 残っている有料ツールと理由

全部を代替できたわけではありません。以下は今も課金しています。

| ツール | 月額 | 代替できない理由 |
|--------|------|----------------|
| Google Search Console | 無料 | 公式データなので代替不要 |
| DataForSEO | $2〜5 | ボリュームデータのAPIが他に安定手段なし |
| Cloudflare Workers | ~$5 | インフラ（代替対象外） |

Ahrefs・Semrush などの総合SEOツールは解約しました。バックリンク分析は競合分析の際にのみ無料枠で確認する運用に切り替えています。

---

## まとめ

- **KWリサーチ**: DataForSEO API（月$2〜5）＋ Excel プランナーで代替
- **重複検知**: 4層防御スクリプト（Embedding＋GSCランクチェック）で代替
- **コンテンツ品質**: 自作 `seo-quality-check` スキルで75点ゲート
- **公開自動化**: Claude Code scheduled-task → Supabase INSERT で毎朝無人公開
- **残す有料ツール**: DataForSEO（実測ボリューム）のみ

「AIに書かせると薄い記事になる」という懸念は品質ゲートで担保し、「毎日手動でやると続かない」という問題はスケジュールタスクで解決しました。構築コストは最初の2週間かかりましたが、その後は月々の工数がほぼゼロになっています。
