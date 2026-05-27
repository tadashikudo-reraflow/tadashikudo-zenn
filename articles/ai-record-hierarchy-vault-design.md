---
title: "AIにとっての「記録階層」設計：feedback / ADR / working-memory / project_*.md の使い分け"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "Obsidian", "ドキュメント", "設計"]
published: true
---
# AIにとっての「記録階層」設計：feedback / ADR / working-memory / project_*.md の使い分け

## はじめに

Claude Code や Cursor などの AI コーディングエージェントを長期運用していると、ある問題に必ずぶつかります。

**「同じミスを何度も繰り返す」「数日前の判断を覚えていない」「PJごとの状態が混ざる」**

これは AI が悪いのではなく、**人間側の記録設計が雑** だからです。AI に渡す記録は「全部 README に書く」では破綻します。

本記事では、筆者が複数の SaaS / 自動化プロジェクトを Obsidian Vault + Claude Code で並行運用する中で固まった、4 種類の記録ファイルの使い分けを共有します。

- `feedback_*.md`: 恒久ルール（同じミスを二度としない）
- ADR (`decisions/YYYY-MM-DD_*.md`): 重要な設計判断の根拠
- `working-memory.md`: セッション跨ぎの一時メモ
- `project_*.md` / `pjXX-*.md`: PJ ごとの最新状態

この 4 層を分けるだけで、AI の挙動が「同僚レベル」に変わります。

## なぜ 1 ファイルに全部書いてはいけないのか

`CLAUDE.md` や `.cursorrules` に全ルールを詰め込むアプローチは、最初の数週間は機能します。が、確実に破綻します。

**理由 1: コンテキストウィンドウを食い潰す**

Claude Code は起動時に `CLAUDE.md` を全文ロードします。ここが 1,000 行を超えると、毎回のターンで数千トークンが消費されます。実用上 **160〜400 行に収めるべき**（公式推奨は 400 行以下）。

**理由 2: 更新タイミングが違うものは分離する**

- ルール（恒久）: 一度書いたら半年は変えない
- 設計判断（履歴）: 過去の判断は変わらない・追記のみ
- 作業状態（流動）: 毎日変わる
- PJ 状態（中期）: 週単位で変わる

**更新サイクルが違うものを 1 ファイルに混ぜると、git diff がノイズだらけになり、AI が「どれが今の真実か」を判別できなくなります。**

## 4 層モデルの定義

### 層 1: `feedback_*.md`（恒久ルール）

**目的**: 同じ失敗を二度と繰り返さないための「禁止事項」を AI に注入する。

**書き込みトリガー**:
- ユーザーから修正指示・方向転換が入った瞬間
- AI が独自判断でやらかして怒られた瞬間
- 「今後はこうして」と明示された瞬間

**ファイル構成**:
```
memory/
  feedback-index.md       # 全 feedback への索引
  feedback_no_force_push.md
  feedback_no_hardcoded_keys.md
  feedback_git_add_guardrail.md
  ...
```

**フォーマット例**:
```markdown
---
name: no-hardcoded-fallback-keys
type: feedback
created: 2026-04-15
severity: critical
---

# APIキーフォールバック値禁止

## ルール
`os.environ.get("KEY", "sk-xxx...")` のような **実値フォールバック** は絶対に書かない。
フォールバックは空文字 `""` か `None` のみ。

## なぜ
過去に `git log` から実キーが漏洩しかけた事故あり。
コミットしてしまうと履歴から完全削除は困難。

## 適用条件
- バックエンドコード全般
- CI/CD の env 設定
- `.env.example` を作る場合は値を `YOUR_API_KEY` に置換
```

**運用のコツ**:
- 1 ファイル 1 ルール（粒度を細かく）
- `severity: critical / high / medium` を frontmatter に入れて優先順位付け
- `feedback-index.md` に 1 行サマリで全件リスト化（AI はインデックスだけ読めば全体把握できる）

### 層 2: ADR（Architecture Decision Records）

**目的**: 「なぜこの技術を選んだか」「なぜこの方針を捨てたか」を時系列で残す。

**書き込みトリガー**:
- アーキテクチャ選定（DB・フレームワーク・ホスティング）
- 大きな方針転換（マイクロサービス化・モノリス回帰など）
- 廃止・撤退の決定

**置き場**: `02_Knowledge/decisions/YYYY-MM-DD_decision-name.md`

**フォーマット**（軽量 ADR）:
```markdown
---
title: モデルポリシー Sonnet デフォルト化
date: 2026-03-09
status: accepted
---

## Context
Opus を全ターンで使うとトークン消費が 1 日 $50 を超えていた。

## Decision
デフォルトを Sonnet に切り替え、Plan Mode のみ Opus を許可する。

## Consequences
- ✅ 月コスト 1/3
- ⚠️ 複雑な設計タスクで Opus 明示切替が必要
- ⚠️ Agent Team のリーダーも Sonnet 統一
```

**ポイント**: ADR は **追記のみ・既存ファイルを書き換えない**。方針が変わったら新しい ADR を起こして `supersedes: 2026-03-09_xxx` で上書き関係を示す。

### 層 3: `working-memory.md`（セッション跨ぎ一時メモ）

**目的**: 「今日やりかけた作業」「次回続きから始めるためのメモ」を残す。

**書き込みトリガー**:
- セッション終了時に未完了タスクが残っている
- 失敗した試みの学習を次回に持ち越したい
- まだルール化するほどではないが忘れたくない

**1 ファイル 1 タイムライン**:
```markdown
# Working Memory

## 2026-05-01
- [ ] あるサイトの DB 51 件に画像 alt 属性を追加（13 件未対応）
- [x] 別サイトの Stripe webhook 本番登録 → 完了
- ⚠️ 学習: Supabase の RLS ポリシー追加時に `service_role` を忘れて全 INSERT がブロックされた

## 2026-04-30
- [ ] ...
```

**昇格フロー**:
```
working-memory.md
   ├─ 恒久ルール化 → feedback_*.md
   ├─ 重要判断    → 02_Knowledge/decisions/
   └─ PJ状態反映  → project_*.md
```

**運用のコツ**: 30 日経過した項目は機械的に削除 or アーカイブ。working は流動的でなければ意味がない。

### 層 4: `project_*.md` / `pjXX-*.md`（PJ ごとの最新状態）

**目的**: PJ ごとの「現在地」を 1 ファイルに集約。AI が PJ にコンテキストスイッチする際の入り口。

**書き込みトリガー**:
- 機能追加・リリース
- 技術スタック変更
- ステータス遷移（α → β → 本番）

**ファイル構成例**:
```
memory/
  pj-real-estate-navi.md
  pj-housing-score.md
  pj-wp-setup-kit.md
```

**最低限入れる項目**:
```markdown
# 物件スコア（住宅投資リスク診断 SaaS）

## 概要
住宅購入時のリスクを 100 点満点でスコアリングする SaaS。

## 技術スタック
- Next.js 15 + Tailwind v4
- Supabase（PostgreSQL + RLS）
- Stripe Checkout / Webhook / Customer Portal

## 現在のステータス（2026-05-01）
- 本番稼働中（example.com）
- Stripe 決済実装済み
- 残: webhook エンドポイントのダッシュボード登録確認

## 直近の判断
- 2026-04-20: comparable-rent + バッジ UI を導入、calcEstimatedPrice を削除

## 関連 ADR
- [[2026-03-15_pricing-tier-design]]
```

## 4 層を運用する自動化フック

手動で記録するのは続きません。Claude Code の場合、以下のような自動化が効きます。

**1. Stop hook で session-end を強制実行**
```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "echo 'Run /session-end before stopping' && exit 1"}]
      }
    ]
  }
}
```

**2. `/session-end` スキルで working-memory を自動更新**

セッション終了時に以下を判定して書き分ける:
- 修正指示があった → `feedback_*.md` 候補を提案
- 設計判断があった → ADR テンプレを生成
- 未完了タスクあり → `working-memory.md` に追記

**3. PJ ファイルは PJ ローカル `CLAUDE.md` から `Read` させる**

`.claude/CLAUDE.md` ではなく、PJ フォルダ直下の `CLAUDE.md` に「このセッションでは `pj29-xxx.md` を最初に読め」と書く。グローバルファイルを肥大化させない。

## 実際に運用してみた効果

筆者が複数 PJ を並行運用するなかで、この 4 層化を導入してから:

- **同じミス再発率**: 月 5〜6 件 → 月 0〜1 件
- **「あれどうしたんだっけ？」の再調査時間**: セッションあたり 15 分 → 2 分
- **`CLAUDE.md` の行数**: 600 行 → 160 行（必要な詳細は別ファイルへ分離）
- **AI のコンテキストロード時間**: 体感で半分以下

特に **feedback と ADR を分けたのが効きました**。「ルール（やってはいけないこと）」と「判断履歴（なぜそう決めたか）」を混ぜると、AI が「これは絶対ルールなのか参考情報なのか」を判別できず、無視するか過剰に守るかのどちらかになります。

## まとめ

AI コーディングを長期運用するための記録設計、実践ポイント:

- **更新サイクルの違うものを 1 ファイルに混ぜない**（恒久 / 履歴 / 流動 / 中期）
- **`feedback_*.md`** は同じ失敗を防ぐ「禁止事項」専用。1 ファイル 1 ルール
- **ADR** は追記のみ・上書き禁止。`supersedes` で関係を示す
- **`working-memory.md`** は流動的・30 日で機械的に剪定
- **`project_*.md`** は PJ の入り口。技術スタック・現状・直近判断を 1 ページに
- **`CLAUDE.md` は 160〜400 行に抑え**、詳細は層別ファイルに `Read` で参照させる
- **Stop hook + `/session-end` で記録自動化**。手動運用は必ず破綻する

「AI が同僚として機能するか」は、モデルの性能ではなく **人間側の記録設計** で決まります。明日からあなたの `CLAUDE.md` を分割してみてください。
