---
title: "AIフィードバックを「資産化」する3層構造：feedback_*.md → feedback-index → MEMORY.md"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AI", "自己改善", "メモリ管理", "Obsidian"]
published: false
---
# AIフィードバックを「資産化」する3層構造：feedback_*.md → feedback-index → MEMORY.md

## はじめに

Claude Code・Cursor・各種AI Agentを使い込んでいくと、必ず遭遇する問題があります。

> 「同じ修正指示を3回目もしている気がする」
> 「先週言ったことを覚えていない」
> 「memory.mdに全部突っ込んだら肥大化して逆に参照されない」

筆者の環境（あるSaaSプロジェクトで26のサブPJを横断管理）では、フィードバックファイルが90件を超えました。これを単一ファイルにまとめるとコンテキスト窓を圧迫し、逆にバラバラに置くとAIが見つけられない。

そこで導入したのが **「フィードバック資産化の3層構造」** です。本記事ではその設計思想と実装、そして1年運用してわかったコツを共有します。

## 3層構造の全体像

```
┌─────────────────────────────────────────────┐
│ Layer 3: MEMORY.md（インデックス・常時ロード）│
│   - 全セッションで自動読み込み               │
│   - 最重要3〜10件＋下層へのリンクのみ        │
│   - 150〜200行に強制圧縮                     │
└──────────────┬──────────────────────────────┘
               │ links to
┌──────────────▼──────────────────────────────┐
│ Layer 2: feedback-index.md（カテゴリ目次）   │
│   - カテゴリ別1行サマリ                      │
│   - 「ツール責務分離」「Next.js」等の章立て │
│   - AIが関連トピックを発見する入口           │
└──────────────┬──────────────────────────────┘
               │ links to
┌──────────────▼──────────────────────────────┐
│ Layer 1: feedback_*.md（詳細・1ファイル1事象）│
│   - frontmatter + Why + How to apply         │
│   - 発生日・再発回数・根拠リンク             │
│   - AIが必要時にRead/RAG検索で取りに行く     │
└─────────────────────────────────────────────┘
```

ポイントは **「常時ロードするのはインデックスだけ」** という点です。詳細はAIが必要に応じて取りに行く設計にすることで、コンテキスト窓を節約しつつ知識は無制限に積み上げられます。

## Layer 1: feedback_*.md の書き方

1ファイル1事象。**Why（なぜ）** と **How to apply（どこで適用するか）** を必ず分けて書きます。

```markdown
---
name: 自律実行モード（承認ゲートスキップ）
description: スケジュールタスク・自動フローで承認確認なしで自律実行する
type: feedback
---

承認ゲートをスキップして最後まで自律実行する。

**Why:** 「許可なしで完遂して欲しい」と明示的に指示
（2026-03-28: スケジュールタスク、2026-03-29: 通常会話でも同様）。
都度確認は不要。

**How to apply:** スケジュールタスク・自動フロー・通常会話いずれでも、
ファイル更新・DB更新・git push 等を確認なしで実行する。

**例外（確認必須）:**
- 外部LLM API課金ゲート
- X投稿・メール送信
- 破壊的操作（rm -rf、force push、DB DROP）
```

`type: feedback` の frontmatter があると、後段でスクリプト集計しやすくなります。

### Why と How to apply を分ける理由

理由（Why）だけ書くと、AIは「なるほど、でもこの状況は当てはまるのか？」と判断できません。逆に適用条件（How）だけ書くと、エッジケースで誤適用します。

両方あると、AIは **「Why に書かれた背景と現状を照らし合わせて、How に書かれていないケースでも合理的に推論」** できます。これが重要。

## Layer 2: feedback-index.md の役割

カテゴリ別の1行サマリを並べます。実例：

```markdown
## ツール責務分離

- **Codex側作業はCodexで行う**: `~/.codex/` 配下の追加・編集を
  Claudeから実施しない → `feedback_codex_work_in_codex.md`

## エージェント・スキル設計

- **Bashを使うAgentはtools必須**: frontmatterに
  `tools: [Bash, Read, Write...]` がないとsubagentでBashがブロック
  → `feedback_agent_tools_field.md`

## Next.js・フロントエンド

- **force-dynamic禁止（公開ページ）→ ISR/revalidate必須**:
  CPU爆食の原因 → `feedback_nextjs_force_dynamic.md`
```

このファイルは **「目次」** に徹します。詳細は書かない。書くと「Layer 1 と Layer 2 のどちらが正本か」問題が起きて必ず腐ります。

ファイル肥大化を防ぐため、**1カテゴリ10エントリを超えたらサブカテゴリに分割** します。

## Layer 3: MEMORY.md の絞り込み

最上位は「セッション開始時に絶対に知っておくべきこと」だけ。例：

```markdown
## フィードバック（最重要）

> 全件: → `feedback-index.md`

- **APIキー・認証シークレットの安全フォールバック**:
  実値ハードコード禁止＋認証用途は空文字フォールバックも禁止。
  違反例: 課金事故 + 15ルート無認証脆弱性
  → `feedback_no_hardcoded_fallback_keys.md`

- **クライアントアプリにAPIキー埋め込み禁止**:
  バイナリから抽出される。バックエンド経由必須
  → `feedback_api_key_binary_isolation.md`

- **git add -A/--all/. はdeny+hookで二重ブロック**:
  ファイル名個別指定のみ → `feedback_git_add_guardrail.md`
```

選定基準は **「セキュリティ事故 or データ損失に直結するもの」** のみ。150〜200行を超えたら `/compact-memory` で再分離します。

## 昇格フロー：working-memory → feedback → ADR

知識は3段階で昇格させます。

| 段階 | 置き場所 | 寿命 | 例 |
|------|---------|------|-----|
| 一時メモ | `working-memory.md` | セッション内 | 「今日のデバッグで気づいた」 |
| フィードバック | `feedback_*.md` | 恒久ルール | 「同じ間違いを2回した」 |
| ADR | `02_Knowledge/decisions/` | アーキ判断 | 「DB を Postgres に決定」 |

トリガーは明確に：

```
修正指示・方向転換が来た → feedback_*.md に新規 or 既存更新
                       → feedback-index.md にリンク追加
                       → 最重要なら MEMORY.md にも昇格

重要な設計判断           → 02_Knowledge/decisions/ に ADR 形式

スキル/ツール失敗         → memory/skill-traces.md に追記
```

このトリガーをCLAUDE.md（プロジェクトルール）に明記しておくと、AIが自動でファイル更新してくれます。

## 実装スニペット：自動集計スクリプト

frontmatterを使うと一覧化が簡単です。

```python
import os, re, glob

def parse_frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta

memory_dir = os.path.expanduser("~/path/to/memory")
for path in sorted(glob.glob(f"{memory_dir}/feedback_*.md")):
    meta = parse_frontmatter(path)
    if meta and meta.get("type") == "feedback":
        print(f"- {meta['name']}: {meta['description']}")
```

これで feedback-index.md の下書きを自動生成できます。
週次で差分チェックすれば、「Layer 1にあるのにLayer 2に登録されていない」迷子フィードバックを検出可能です。

## 1年運用してわかったコツ

- **「Why」を書かないフィードバックは半年で死ぬ**: 状況が変わったとき、適用すべきか判断できない
- **「再発回数」をdescriptionに書く**: 「2回発生」と書くと、AIがその警告を最優先で守る
- **同じテーマで3件溜まったらカテゴリを切る**: feedback-index.md の章立てを動的に増やす
- **MEMORY.md は150行で限界**: 超えたら例外なく分離（人間も読まなくなる）
- **削除を恐れない**: 古くなったルールはADRに昇格 or アーカイブ。残すと邪魔になる

## まとめ

- **3層分離が肝**: 詳細（Layer 1）/ 索引（Layer 2）/ 最重要（Layer 3）の責務を混ぜない
- **Why と How to apply を必ず両方書く**: AIが推論できる粒度に保つ
- **frontmatterで構造化する**: 後段の自動集計・分析が楽になる
- **昇格フローを明示する**: working-memory → feedback → ADR の動線を CLAUDE.md に書く
- **インデックスは肥らせない**: MEMORY.md は150行、feedback-index.md は1カテゴリ10件まで

Claude Code・Cursor・自作Agentを問わず、AIと長く付き合うなら「フィードバックを資産化する仕組み」は必須です。本記事の構造をそのまま真似していただいてOKです。あなたのAIが、昨日より少しだけ賢くなる助けになれば幸いです。
