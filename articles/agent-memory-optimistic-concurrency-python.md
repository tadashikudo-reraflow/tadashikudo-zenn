---
title: "AIエージェントの記憶ファイルにOptimistic Concurrencyを実装して並行書き込み競合を防いだ"
emoji: "🤖"
type: "tech"
topics: ["ClaudeCode", "Python", "OptimisticConcurrenc", "AIAgent", "記憶管理"]
published: true
---
# AIエージェントの記憶ファイルにOptimistic Concurrencyを実装して並行書き込み競合を防いだ

## はじめに

Claude Codeで複数のAIエージェントセッションを並列実行すると、共有の記憶ファイル（`working-memory.md` や `skill-traces.md` など）への書き込みが同時に発生することがある。

read → write の間に別セッションがファイルを更新してプッシュした場合、後から書き込むセッションが**相手の変更を黙って上書き**してしまう。ロック機構のない Git + Markdown 運用では特に起きやすい問題だ。

この記事では、Anthropic Memory APIの「Optimistic Concurrency」と同じ原理を、ローカルファイルと Supabase DB の2パターンで実装した例を紹介する。

---

## 問題の具体的な発生シナリオ

```
Session A                         Session B
  |                                   |
  |-- read memory.md (hash: abc) --→  |
  |                                   |-- read memory.md (hash: abc) --→
  |                                   |-- write memory.md (new hash: def) --→
  |-- write memory.md (Bの変更を上書き) --→  # 競合！
```

Session B の変更は Session A のwriteで消える。ログ型のメモリ（学習記録・エラーログ）で発生すると、片方のエントリが静かに失われる。

複数エージェントが夜間バッチで同時に記憶を更新するような構成では、これが毎回起きる。

---

## ファイルベースの実装：MD5ハッシュ + git pull

### 基本アイデア

1. 書き込み前にファイルの MD5 を取る（`pre_hash`）
2. `git pull --rebase` で最新を取得する
3. pull後のハッシュが `pre_hash` と一致 → 競合なし → 書き込む
4. 不一致 → 別セッションが変更 → リトライまたはアボート

```python
import hashlib
import subprocess
import time
from pathlib import Path

MAX_RETRIES = 3
RETRY_DELAY_BASE = 1.5  # 秒、指数バックオフ


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def git_pull(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "pull", "--rebase", "-q"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def safe_write(file_path: str, new_content: str) -> bool:
    path = Path(file_path).expanduser().resolve()
    repo_root = find_repo_root(path)

    for attempt in range(1, MAX_RETRIES + 1):
        pre_hash = file_hash(path)

        # pull 失敗時は必ず abort — 未取得変更を上書きするとデータ破壊になる
        if not git_pull(repo_root):
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_BASE * attempt)
                continue
            return False

        post_hash = file_hash(path)

        if pre_hash == post_hash:
            # 競合なし → 書き込む
            path.write_text(new_content, encoding="utf-8")
            print(f"[guard] Written: {path} (attempt {attempt})")
            return True
        else:
            # 競合検知 → リトライ
            print(f"[guard] CONFLICT (attempt {attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_BASE * attempt)
    return False
```

`git pull --rebase` を書き込み直前に挟むことで、リモートの変更を取り込んでからハッシュを検証できる。pull 失敗時は必ず書き込みを拒否する（fail-closed）。

### 追記専用モード（--append）

ログ型のメモリには全置換ではなく追記が正しい操作になる。

```python
def safe_append(file_path: str, new_content: str) -> bool:
    path = Path(file_path).expanduser().resolve()
    repo_root = find_repo_root(path)

    for attempt in range(1, MAX_RETRIES + 1):
        if not git_pull(repo_root):
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_BASE * attempt)
                continue
            return False
        cur = path.read_text(encoding="utf-8")
        sep = "" if cur.endswith("\n") or cur == "" else "\n"
        tail = new_content if new_content.endswith("\n") else new_content + "\n"
        path.write_text(cur + sep + tail, encoding="utf-8")
        print(f"[guard] Appended: {path} (+{len(new_content.splitlines())} line(s))")
        return True
    return False
```

pull 後の最新状態に差分を追記するため、**競合が起きても情報が失われない**。

CLI からの使い方:
```bash
# 上書きモード
python3 memory_write_guard.py /path/to/memory.md "$NEW_CONTENT"

# 追記モード（log型memoryに1エントリを追加する正しい方法）
python3 memory_write_guard.py --append /path/to/skill-traces.md "$NEW_LINE"
```

---

## DBベースの実装：Supabase + PATCH WHERE hash = known_hash

Supabase の `agent_memory` テーブルに `content_hash` カラムを持たせ、PATCH 時に WHERE 句で一致する行だけ更新するパターン。

### テーブル設計

```sql
CREATE TABLE agent_memory (
  key          TEXT PRIMARY KEY,
  scope        TEXT NOT NULL DEFAULT 'shared',
  content      TEXT NOT NULL,
  content_hash TEXT NOT NULL,        -- MD5 of content
  written_by   TEXT,
  expires_at   TIMESTAMPTZ
);
```

### 実装

```python
import hashlib
import time
import urllib.request, urllib.parse, json, sys

def read_row(base_url, svc_key, mem_key):
    """現在のコンテンツとハッシュを取得"""
    endpoint = (
        f"{base_url}/rest/v1/agent_memory"
        f"?key=eq.{urllib.parse.quote(mem_key, safe='')}"
        f"&select=content,content_hash"
    )
    headers = {
        "apikey": svc_key,
        "Authorization": f"Bearer {svc_key}",
    }
    status, body = api_request("GET", endpoint, headers)
    if status not in (200, 201) or not body:
        return None, None
    return body[0]["content"], body[0]["content_hash"]


def write_row(base_url, svc_key, mem_key, scope, written_by, content, known_hash):
    new_hash = hashlib.md5(content.encode()).hexdigest()
    headers = {
        "apikey": svc_key,
        "Authorization": f"Bearer {svc_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal,count=exact",
    }

    if known_hash is None:
        # 新規INSERT: 409 は別セッションが先に書いた → リトライで PATCH に切替
        endpoint = f"{base_url}/rest/v1/agent_memory"
        data = {"key": mem_key, "scope": scope, "content": content,
                "content_hash": new_hash, "written_by": written_by}
        status, _ = api_request("POST", endpoint, headers, data)
        if status == 409:
            return False, "insert_conflict"
        return status in (200, 201), "insert"
    else:
        # PATCH WHERE content_hash = known_hash （これがOCCの本体）
        endpoint = (
            f"{base_url}/rest/v1/agent_memory"
            f"?key=eq.{urllib.parse.quote(mem_key, safe='')}"
            f"&content_hash=eq.{known_hash}"
        )
        data = {"content": content, "content_hash": new_hash, "written_by": written_by}
        status, _ = api_request("PATCH", endpoint, headers, data)
        return status in (200, 201, 204), "update"


def write_memory(base_url, svc_key, mem_key, scope, written_by, new_text, max_retries=3):
    for attempt in range(max_retries):
        current_content, current_hash = read_row(base_url, svc_key, mem_key)
        new_content = (
            current_content + "\n\n" + new_text if current_content else new_text
        )
        success, mode = write_row(
            base_url, svc_key, mem_key, scope, written_by, new_content, current_hash
        )
        if success:
            print(f"✅ [{mode}] key={mem_key} attempt={attempt + 1}")
            return
        wait = 0.5 * (attempt + 1)
        print(f"⚠️  conflict on attempt {attempt + 1}, retry in {wait}s", file=sys.stderr)
        time.sleep(wait)
    sys.exit(f"ERROR: write failed after {max_retries} attempts")
```

ポイントは `PATCH WHERE content_hash = known_hash` の部分。自分が読んだ時点のハッシュと一致する行だけ更新するため、**別エージェントが変更済みの場合は 0 行が更新される**（PATCH は成功するが実際には何も変わらない）。次の attempt で `read_row` を再実行し、最新のハッシュを取り直してリトライする。

---

## 2つのアプローチの比較

| 観点 | ファイルベース | DBベース |
|------|------------|---------|
| 競合検知 | MD5比較 + git pull | PATCH WHERE hash = ? |
| バックエンド | Git リポジトリ | Supabase |
| 追記サポート | `--append` モード | content に追記してハッシュ更新 |
| 競合解決 | abort or merge | リトライ (read → write ループ) |
| 向いている用途 | ローカル Markdown メモリ | クラウド共有メモリ・マルチエージェント |

ファイルベースはローカルの Obsidian Vault のような Git 管理下のメモリに、DBベースはクラウドで複数エージェントが共有するメモリに向いている。

---

## まとめ

AIエージェントのメモリ書き込みに Optimistic Concurrency を導入する際のポイントをまとめる。

- **ハッシュで状態を追跡する**: MD5 で「自分が読んだ時点の状態」を記録し、書く直前に照合する
- **git pull は書き込み直前に**: pull 後にハッシュを取り直すことで、リモート変更を取り込んだ状態で競合検知できる
- **fail-closed を原則にする**: pull 失敗・競合検知時は書き込みを拒否する。中途半端なマージよりデータ損失ゼロの方が望ましい
- **log型メモリは全置換しない**: `--append` / 追記専用パスを用意し、read-modify-write の競合ウィンドウを構造的に排除する
- **リトライはExponential Backoff**: 競合時はランダム性を持たせた待機でスパイクを分散する

実装はシンプルで、hashlib と subprocess（またはHTTP）だけで動く。複数 AI セッションを並列で走らせる構成になったタイミングで導入を検討してみてほしい。
