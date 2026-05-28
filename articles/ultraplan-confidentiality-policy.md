---
title: "Ultraplan の機密性ベース選別運用：公開リポと機密リポでAI送信先を切り分けた話"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "Ultraplan", "AI", "セキュリティ", "ガバナンス"]
published: false
---
# Ultraplan の機密性ベース選別運用：公開リポと機密リポでAI送信先を切り分けた話

## はじめに

Claude Code on the web の **Ultraplan**（Anthropic-managed VM 上で Plan Mode を走らせる機能）が登場したとき、筆者の環境では十数のリポジトリを横断する開発をしていたため、「どのリポなら出して良いか」を組織として線引きする必要に迫られました。

最初は「全面禁止が安全」「業務価値が高いので使うべき」と両極の議論になりましたが、結論として **機密性ベースの選別運用（許可リスト・禁止リスト＋チェックリスト）** に落ち着きました。本記事はその過程と、運用に組み込んだ具体的なチェックフローを共有します。

対象は、Claude Code / Codex CLI / Cursor などのAIコーディング支援を組織導入しようとしている開発者・テックリードです。

## なぜ「全面禁止」でも「全面許可」でもダメだったか

検討当初、Ultraplan には次の懸念がありました。

| 観点 | 当初懸念 | 確認後の評価 |
|---|---|---|
| 追加課金 | VM時間・ストレージ課金がありそう | no separate compute charge。Claude usage の rate limit を共有 |
| データ送信先 | 中国系プロバイダ等への経由が無いか | Anthropic-managed VM のみ |
| データ保持 | ZDR との関係 | ZDR org は Ultraplan 利用不可。通常テナントは標準保持 |
| Secrets | env vars の閲覧範囲 | dedicated secrets store は未整備、UI で編集者に見える |
| 連携 | GitHub前提か | GitHub clone or ≤100MB の local bundle |

整理すると、**コスト・送信先・性能の懸念は解消**したものの、**Secrets管理がまだ弱い**点だけ残ります。

「全面禁止」だと、公開予定のOSSやLP・ブログ草案など秘匿不要なリポまで生産性を落とす。「全面許可」だと、医療・自治体・顧客個人情報を扱うリポまで巻き込むリスクが残る。そこで、**リポジトリ単位で「公開／非公開」「機密データの有無」をフラグ化し、許可リストでホワイトリスト運用する** 方式に決めました。

## ホワイトリスト／ブラックリストの設計

設計したルールは概ね以下です。実運用ではグローバル `CLAUDE.md`（AIエージェントが必ず読む設定ファイル）に箇条書きで記述します。

```markdown
## [G] Ultraplan 利用ルール

- 利用OK:
  - 公開済み or OSS化前提のリポ
  - 汎用スクリプト置き場（機密性ゼロ）
  - 公開ブログ・LP の構成草案
  - ハーネス改善の RFC 草案

- 利用禁止:
  - 医療・ヘルスケア領域のリポ
  - RAG chunks に機密文書が混在しうるリポ
  - 政治・選挙関連リポ
  - 課金・決済データを保持するリポ
  - 顧客リード情報を含むリポ
  - .env / credentials* / secrets* を含むリポ全般
```

「リポ単位」で線引きしたのは、ファイル・コミット単位での選別が運用コストに見合わないからです。`.gitignore` していても、過去履歴・近接設定・README の連絡先などから漏れる経路は多い。**「このリポはまるごと出して良いか？」という単位で判断する**ほうがシンプルです。

## 起動前に必ず通すチェックリスト

ホワイトリストに入っていても、状況次第で送るべきでないファイルが入り込みます。そこで、`/ultraplan` または `claude --remote` 起動前に毎回通すチェックリストを定義しました。

```text
□ 対象リポは「利用OK」リストに該当するか？
□ .env / secrets / credentials* がコミットされていないか？
□ env vars に登録する値は、組織内で共有可能か？
□ GitHub に push 済み、または ≤100MB の bundle 化が可能か？
```

このチェックリストは `CLAUDE.md` に書いておくと、AI側が起動前に自分でチェックしてくれます。

実装上のポイントとして、`.env` 検出はリポ全体に走らせます。

```bash
# Ultraplan 起動前のプリチェック例
set -e
REPO_ROOT=$(git rev-parse --show-toplevel)

FOUND=$(find "$REPO_ROOT" \
  -type f \
  \( -name ".env*" -o -name "credentials*" -o -name "secrets*" \) \
  -not -path "*/node_modules/*" \
  -not -path "*/.git/*")

if [ -n "$FOUND" ]; then
  echo "🚨 Secrets-like files found:"
  echo "$FOUND"
  exit 1
fi

# 直近50コミットの履歴混入チェック
LEAKED=$(git log -50 --all --diff-filter=A --name-only --pretty=format: \
  | grep -E '\.env|credentials|secrets' || true)
[ -n "$LEAKED" ] && echo "⚠️ 履歴に secrets らしきパスあり: $LEAKED"

echo "✅ プリチェック通過"
```

CI で同等チェックを走らせ、PRラベル `ultraplan-safe` を付ける運用にすると、許可リストとリポ実態の乖離も検知できます。

完成版（許可/禁止リスト・履歴混入・push状態・サイズ・`--install-hook` でpre-commit自動設置）を Gist に公開しました: <https://gist.github.com/tadashikudo-reraflow/31998f7634954180653ca66ed681e5b1>

## env vars の最小権限運用

Ultraplan 側の env vars はチーム全員が UI で編集・閲覧できる前提なので、本番DBの接続情報や課金APIキーは置きません。**Plan Mode で必要な範囲の最小トークンに絞る**運用にしました。

```bash
# OK 例: 公開API・読み取り専用トークン
GITHUB_TOKEN=YOUR_READONLY_PAT          # repo:read のみ
OPENAI_API_KEY=YOUR_API_KEY             # 月次予算上限を別途設定
NEXT_PUBLIC_SITE_URL=https://example.com

# NG 例: 本番影響のあるシークレット
# STRIPE_SECRET_KEY=...   # 課金実行が可能なキー
# DATABASE_URL=...        # 本番DB直接接続
# ADMIN_SESSION_SECRET=...# 管理画面奪取に直結
```

Plan Mode の用途は「設計・構造化・差分提案」です。実装と本番接続はローカルで行えば良いので、**Ultraplan 側に本番影響キーを渡さない**設計が現実的でした。

## まとめ

- Ultraplan は「全面禁止」も「全面許可」も非現実的。**リポジトリ単位の機密性で線引き**するのが現実解
- 許可・禁止の **ホワイトリスト／ブラックリストを明文化**し、`CLAUDE.md` に書いてAI側にも共有する
- 起動前チェックリスト（リポ該当性・secrets混入・push状況）を **CIまたはシェルで自動化**する
- env vars には **本番影響のないトークンのみ**入れ、Plan Mode に役割を絞る
- ZDR要件が発生したら即フォールバックできる撤退手順を用意する

AIコーディング支援は「使うか／使わないか」ではなく、「**どこに、どこまで出して良いか**」を組織として定義できるかが導入成否を分けます。
