---
title: "Anthropic \"Dreaming\"をscheduled-taskで再現する——週次cross-agent記憶整理の実装"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "AIAgent", "Dreaming", "Anthropic", "記憶管理"]
published: true
---
# Anthropic "Dreaming"をscheduled-taskで再現する——週次cross-agent記憶整理の実装

## はじめに

Claude Code は1セッション完結型のAIだ。`/session-end` スキルで作業ログを `working-memory.md` に書き出しても、「複数セッションを跨いで同じミスを繰り返す」「先週うまくいった手順を忘れる」という問題は消えない。

この問題を解決するために Anthropic が内部で採用しているのが **Dreaming** という概念だ。人間の睡眠中の記憶整理になぞらえた、セッション外で動く非同期の記憶統合プロセスである。本記事では、この Dreaming を Claude Code の `scheduled-task` として再現し、週次で cross-session パターンを抽出・記憶昇格する仕組みの実装方法を解説する。

---

## Dreaming とは何か

Anthropic の公開資料によれば、Dreaming はモデルの **out-of-band**（帯域外）プロセスとして動く記憶固定化メカニズムだ。具体的には次の役割を担う:

- **繰り返し失敗パターンの検出**: 複数セッションで同じエラーや回避策が登場していれば昇格候補とする
- **stale エントリの刈り込み**: 30日以上放置された未完了タスクをアーカイブ候補として特定する
- **成功パターンの固定化**: 「うまくいった手順」を正式なルールとして記録する

重要なのは「Dreaming はセッション内では動かない」点だ。セッション内は `/session-end` が単一セッションの記録を担い、Dreaming はその蓄積を cross-session 視点で整理する役割分担になる。

```
セッション1 → /session-end → working-memory.md
セッション2 → /session-end → working-memory.md
セッション3 → /session-end → working-memory.md
                                     ↓
           [週次 Dreaming scheduled-task]
                                     ↓
                             dreams.md（昇格済みパターン）
                             feedback_*.md（恒久ルール）
```

---

## 実装アーキテクチャ

筆者の環境では `~/.claude/scheduled-tasks/vault-dreaming-weekly/SKILL.md` として定義し、毎週月曜 10:00 JST に自動実行するよう設定している。

```yaml
# scheduled-task の SKILL.md 冒頭例
---
name: vault-dreaming-weekly
description: 過去7日の作業ログを横断してパターン抽出し、
             dreams.md を更新・feedback 昇格候補を出力する。
---
```

scheduled-task は Claude Code のリモートエージェント機能を使って、ユーザーが不在の状態で自律実行される。`<scheduled-task>` タグで SKILL.md がプロンプトとして注入され、Claude がそのまま実行する。

---

## 実装: 週次 Dreaming タスクの主要ステップ

### Step 1: 過去7日の Daily Notes 収集

```bash
VAULT_ROOT=~/path/to/vault
SINCE=$(date -v-7d +%Y-%m-%d 2>/dev/null || date -d '7 days ago' +%Y-%m-%d)

find "$VAULT_ROOT/Daily Notes" -name "*.md" \
  -newer "$VAULT_ROOT/Daily Notes/2026/01/2026-01-01.md" \
  | sort | tail -7 \
  | xargs grep -l "session-end\|失敗学習\|未完了" 2>/dev/null
```

これで「セッション終了記録が書き込まれた日」だけを絞り込む。

### Step 2: working-memory.md の現状把握

```bash
cat "$VAULT_ROOT/memory/working-memory.md"
```

注目するのは次の3種類:

| 観点 | 条件 | アクション |
|------|------|-----------|
| 繰り返し失敗 | `repeat_count >= 2` | `feedback_*.md` 昇格候補に追加 |
| stale エントリ | `last_observed` が30日以上前 | アーカイブ候補としてリストアップ |
| トリガー補正 | `trigger_user_correction: true` | 最優先で feedback 昇格 |

### Step 3: dreams.md への追記

```bash
DREAMS="$VAULT_ROOT/memory/dreams.md"
NEW_ENTRY="
<!-- vault-dreaming-weekly: $(date +%Y-%m-%d) -->
### $(date +%Y-%m-%d) 週次パターン抽出

[Claude が Step 2 の分析結果を記述]
"

# OCC で安全に追記（後述）
python3 ~/path/to/scripts/memory_write_guard.py --merge "$DREAMS" "$(cat "$DREAMS")${NEW_ENTRY}"
```

---

## OCC（楽観的同時実行制御）による競合防止

複数の Claude Code セッションが同時に `working-memory.md` を書き込もうとする場合、read → write の間に他セッションが push すると **サイレントな上書き破壊** が起きる。

Anthropic の Memory API はこれを **Optimistic Concurrency Control（OCC）** で防いでいる。筆者の環境では同じ原理を `memory_write_guard.py` として実装した:

```python
def safe_write(file_path: str, new_content: str, merge: bool = False) -> bool:
    path = Path(file_path).expanduser().resolve()
    repo_root = find_repo_root(path)

    for attempt in range(1, MAX_RETRIES + 1):
        pre_hash = file_hash(path)          # 書き込み前の hash

        # git pull --rebase で最新取得
        if not git_pull(repo_root):
            # pull 失敗 = 書き込まずに abort（データ破壊防止）
            time.sleep(RETRY_DELAY_BASE * attempt)
            continue

        post_hash = file_hash(path)         # pull 後の hash

        if pre_hash == post_hash:
            # 競合なし → 安全に書き込む
            path.write_text(new_content, encoding="utf-8")
            return True
        else:
            # 競合検知 → merge モードなら merge して続行
            if merge:
                original = path.read_text(encoding="utf-8")
                merged = merge_contents(original, new_content, path.name)
                path.write_text(merged, encoding="utf-8")
                return True
            else:
                # 通常モードはリトライ→最終的に abort
                continue

    return False  # MAX_RETRIES 超過 → 書き込み断念
```

ポイントは「pull 前後の hash 比較」だ。他セッションが push していれば pull 後に hash が変わる。これを検知して merge またはリトライに切り替える。

**追記専用の `--append` モード**も用意した。`working-memory.md` のような「ログ型メモリ」には全置換でなく差分1行を渡す方が安全で、切り詰め事故を構造的に防げる:

```bash
python3 memory_write_guard.py --append dreams.md "新しいパターン1行"
```

---

## hooks との役割分担

Claude Code の hooks（`settings.json` の `hooks:` セクション）とは明確に役割が分かれる:

| 仕組み | タイミング | 役割 |
|--------|-----------|------|
| `PreCompact` hook | コンテキスト圧縮直前 | 未完了タスク・進行中PJ状態を `compact-log.md` に退避 |
| `PostCompact` hook | コンテキスト圧縮直後 | `AI-AGENT.md` / `CLAUDE.md` を再インジェクトして文脈復元 |
| `/session-end` スキル | セッション終了時（手動） | 単セッションの作業ログを `working-memory.md` に記録 |
| `vault-dreaming-weekly` | 毎週月曜 10:00（自動） | 複数セッション横断のパターン抽出と `dreams.md` 更新 |

`PreCompact` / `PostCompact` が「1セッション内の記憶継続性」を担うのに対し、Dreaming scheduled-task が「セッションを超えた長期記憶の品質維持」を担う。

---

## feedback 昇格フロー

Dreaming の最終アウトプットは「昇格候補リスト」だ。実際に `feedback_*.md` を作成するのは次のインタラクティブセッション内でユーザーが確認してから行う設計にしている。これには理由がある:

1. **誤検知防止**: 自動化が `feedback_*.md` を直接書くと、誤ったルールが恒久化するリスクがある
2. **人間のゲート**: Claude の判断に対するヒューマン・イン・ザ・ループを維持する

```
vault-dreaming-weekly（月曜 10:00）
    ↓ 昇格候補レポートを Daily Note に書く
次のインタラクティブセッション
    ↓ ユーザーが候補を確認・承認
    ↓ feedback_*.md を作成
    ↓ CLAUDE.md の恒久ルールとして機能し始める
```

---

## まとめ

Anthropic Dreaming を Claude Code で再現する際の実装ポイントをまとめる:

- **単セッション記録（/session-end）と cross-session 整理（Dreaming）を分離**する。両者を混在させると責任範囲が曖昧になる
- **OCC（楽観的同時実行制御）**で concurrent write を防ぐ。`git pull` 前後の hash 比較が最もシンプルな実装だ
- **追記モード（`--append`）をログ型メモリに使う**。全置換は切り詰め事故の温床になる
- **feedback 昇格は自動化しない**。Dreaming は「候補を出す」まで。最終判断は人間に委ねる
- **hooks（PreCompact/PostCompact）と scheduled-task Dreaming は相補関係**。前者はセッション内継続性、後者はセッション横断品質維持と役割が違う

Claude Code の記憶管理を「1セッションで完結させる」発想から「Dreaming で週次統合する」発想に切り替えると、同じミスを繰り返す頻度が明確に減る。ぜひ自分の scheduled-task として試してほしい。
