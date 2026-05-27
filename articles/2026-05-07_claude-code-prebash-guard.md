---
title: "Claude Code の Pre-Bash フックで課金 API を自動ガードした話（コマンドチェーンバイパス対策あり）"
emoji: "🤖"
type: "tech"
topics: ["Claude", "ClaudeCode", "AIエージェント", "セキュリティ", "ShellScript"]
published: true
---
# Claude Code の Pre-Bash フックで課金 API を自動ガードした話（コマンドチェーンバイパス対策あり）

Claude Code を使い込んでいると、「エージェントが勝手に課金 API を叩いてた」という事故が起きやすい。特に `dalle_gen.py` や `gemini` コマンドのような **実行するだけで従量課金が走るツール** は、明示的に承認してから実行させたい。

この記事では、Pre-Bash フックと CLI レジストリを組み合わせて **AI が勝手に課金ツールを実行するのを防ぐ仕組み**を作った話を紹介する。最終的にコードレビューで発覚した「コマンドチェーンバイパス脆弱性」を直すところまで。

---

## 背景：CLI ツールが散らばって管理できない

ローカル開発環境には気がつけばスクリプトが増えていく。

```
~/.local/bin/
~/workspace/scripts/
~/.npm-global/bin/
```

複数のパスに **80 本以上のスクリプト**が点在しており、どれが課金 API を叩くのか、どれが本番 DB に書き込むのかを把握しきれていなかった。

Claude Code（や Codex CLI）がエージェントとして動作するとき、これらのツールを **確認なしで実行してしまう**ことがある。実際に 1 回の誤実行で数千円の課金が発生したことがあり、ガードの必要性を実感した。

---

## アーキテクチャ概要

作ったものはシンプルで、3 つのコンポーネントで構成される。

```
registry.toml   ← 全 CLI ツールのメタデータ（リスク・課金・ガードアクション）
guard_check.py  ← registry を参照してコマンドの可否を判定
Pre-Bash フック  ← Claude Code / Codex 実行前に guard_check.py を呼ぶ
```

### registry.toml のスキーマ

```toml
[[scripts]]
name = "dalle_gen.py"
path = "~/workspace/scripts/dalle_gen.py"
runtime = ["claude", "codex"]
risk = "high"
billing = "paid"
guard_action = "hardblock"   # hardblock / warn / log / none
purpose = "DALL-E 3 画像生成（課金あり）"
```

`guard_action` の意味は：

| 値 | 動作 |
|----|------|
| `hardblock` | 実行を即ブロック（課金ツール・高リスク） |
| `warn` | 警告を出して続行 |
| `log` | ログ記録のみ |
| `none` | ガードなし |

### Claude Code の Pre-Bash フック設定

`~/.claude/settings.json` の `hooks.PreToolUse` に追加する：

```json
{
  "matcher": "Bash",
  "hooks": [
    {
      "type": "command",
      "command": "bash ~/.claude/hooks/pre-bash-guard.sh",
      "timeout": 5
    }
  ]
}
```

フックは `CLAUDE_TOOL_INPUT` 環境変数に実行しようとしているコマンド（JSON 形式）を受け取り、exit code で制御する。

```
exit 0 → 通過
exit 2 → Claude が実行をブロック
```

### guard_check.py の初版

Pre-Bash フックから呼ばれる Python スクリプト。registry.toml をパースして、コマンド名が登録されていたら guard_action を返す。

```python
def extract_tool_name(cmd):
    tokens = cmd.split()
    for tok in tokens:
        if tok in PREFIX_CMDS:  # sudo, env, nohup など
            continue
        if tok in PYTHON_ALIASES:  # python3 など
            # 次のトークンがスクリプト名
            ...
        return Path(tok).name
    return ""
```

これで基本的なガードは動いた。`dalle_gen.py` や `gemini` を直接実行しようとすると HARDBLOCK が返る。

---

## 落とし穴 1：部分一致で誤検知

初期実装のバグとして、`ls` というコマンドが `sync-skills-to-cursor.sh` にマッチしてしまうケースがあった。

原因は registry の名前検索に部分一致（`in` 演算子）を使っていたこと。

```python
# 危険：部分一致
matched = [e for e in entries if tool_name in e.get("name", "")]
```

`ls` は `sync-skills-to-cursor.sh` の中に含まれる文字列なので誤検知する。

修正は完全一致に変更するだけ：

```python
# 安全：完全一致
matched = [e for e in entries if e.get("name") == tool_name]
```

---

## 落とし穴 2（本命）：コマンドチェーンバイパス

コードレビューで発覚した **クリティカルな脆弱性**。

```bash
echo ok && dalle_gen.py
```

このコマンドを実行しようとすると、初版の `extract_tool_name()` は先頭の `echo` だけを見て「登録なし → 通過」と判断してしまう。

### PoC

```python
cmd = "echo ok && dalle_gen.py"
tool = extract_tool_name(cmd)
# → "echo"  ← dalle_gen.py を見逃す！
```

同様のバイパスパターン：

```bash
# パイプ
ls | dalle_gen.py

# セミコロン
true; gemini --model flash

# bash -c
bash -c "dalle_gen.py --prompt test"

# sudo プレフィックス
sudo gemini --model flash
```

### 修正：`extract_all_tool_names()` に全面改修

コマンドチェーン内の**全サブコマンドを列挙**して、最も厳しい guard_action を適用する。

```python
# シェル演算子で分割するパターン
_BASH_C_PAT = re.compile(r'\bbash\s+-c\s+(?:"([^"]+)"|\'([^\']+)\')')

def extract_all_tool_names(cmd: str) -> list[str]:
    names = []

    # bash -c "..." の内側を再帰的に展開
    for m in _BASH_C_PAT.finditer(cmd):
        inner = m.group(1) or m.group(2)
        names.extend(extract_all_tool_names(inner))

    # &&, ||, ;, | でサブコマンドに分割
    parts = re.split(r'&&|\|\||;|\|', cmd)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        # bash -c 部分は展開済みなので bash 自体だけ追加
        if _BASH_C_PAT.search(part):
            raw_tokens = part.split()
            if raw_tokens:
                names.append(Path(raw_tokens[0]).name)
            continue
        raw_tokens = part.split()
        name = _extract_one_from_tokens(raw_tokens)
        if name:
            names.append(name)

    return [n for n in names if n]
```

`_extract_one_from_tokens()` はプレフィックスコマンドと環境変数設定をスキップして実ツール名を返す：

```python
# プレフィックスコマンド（ツール名の前に置かれるラッパー）
_PREFIX_CMDS = frozenset({
    "sudo", "env", "nohup", "time", "nice", "ionice",
    "command", "exec", "doas", "run", "uvx", "xargs",
})

def _extract_one_from_tokens(tokens: list[str]) -> str:
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if "=" in tok and not tok.startswith("-"):  # VAR=val 形式をスキップ
            i += 1
            continue
        if tok in _PREFIX_CMDS:
            i += 1
            continue
        if tok in _PYTHON_ALIASES:  # python3 <script.py> 形式
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                return Path(tokens[i + 1]).name
            return Path(tok).name
        return Path(tok).name
    return ""
```

全サブコマンドを列挙したあと、severity 順で最も厳しいものを適用する：

```python
_SEVERITY = {"hardblock": 3, "warn": 2, "log": 1, "none": 0}

worst_entry = None
worst_severity = -1

for tool_name in tool_names:
    matched = [e for e in entries if e.get("name") == tool_name]
    if not matched:
        continue
    entry = matched[0]
    action = entry.get("guard_action", "none")
    sev = _SEVERITY.get(action, 0)
    if sev > worst_severity:
        worst_severity = sev
        worst_entry = entry
```

---

## fail-open から fail-closed へ

もう一つ修正したのが、registry.toml のパース失敗時の挙動。

初版：

```python
try:
    entries = load_registry(registry_path)
except Exception:
    sys.exit(0)  # fail-open：パース失敗でも通過させる
```

これだと **registry.toml が破損したとき、すべての HARDBLOCK ツールが素通り**になる。

修正後：

```python
except Exception as e:
    print(
        f"[GUARD:ERROR] registry.toml の読み込みに失敗しました: {e}\n"
        f"  → 修復まで一部ガードが無効化されている可能性があります。",
        file=sys.stderr
    )
    sys.exit(2)  # fail-closed：WARN を出して通過（完全ブロックはしない）
```

exit 2 にすることで警告は出るが実行は通す（呼び出し元が判断できる）。完全ブロック（exit 1）にするとすべての Bash コマンドが止まってしまうため、WARN 止まりが妥当。

---

## Codex CLI 対応

Claude Code だけでなく Codex CLI にも同じガードを適用する。Codex のフックは Python スクリプトで、stdin から JSON イベントを受け取る形式。

```python
# ~/.codex/hooks/pre_tool_use_bash.py

GUARD_SCRIPT = Path.home() / "workspace/cli-registry/guard_check.py"
REGISTRY     = Path.home() / "workspace/cli-registry/registry.toml"

# Homebrew python3 を明示（system python 3.9.6 では型アノテーション失敗）
PYTHON = "/opt/homebrew/bin/python3"
if not Path(PYTHON).exists():
    PYTHON = "python3"

result = subprocess.run(
    [PYTHON, str(GUARD_SCRIPT), command, str(REGISTRY)],
    capture_output=True, text=True, timeout=4
)

if result.returncode == 1:
    emit({"decision": "block", "reason": result.stderr.strip()})
    return

if result.returncode == 2:
    # WARN：stderr を転送して続行
    warn_msg = result.stderr.strip()
    if warn_msg:
        print(warn_msg, file=sys.stderr, flush=True)
```

ポイントは `/opt/homebrew/bin/python3` を明示すること。macOS 付属の Python 3.9 系では型アノテーション構文でエラーが出るため、Homebrew 版を優先する。

---

## 動作確認：11 パターンのスモークテスト

修正後に以下のパターンで全件確認した。

| コマンド | 期待値 | 結果 |
|---------|--------|------|
| `dalle_gen.py` | HARDBLOCK | ✅ |
| `echo ok && dalle_gen.py` | HARDBLOCK | ✅ |
| `sudo gemini --model flash` | HARDBLOCK | ✅ |
| `bash -c "stripe charge ..."` | HARDBLOCK | ✅ |
| `env VAR=val dalle_gen.py` | HARDBLOCK | ✅ |
| `gemini; ls` | HARDBLOCK | ✅ |
| `ls \| gemini` | HARDBLOCK | ✅ |
| `nohup dalle_gen.py` | HARDBLOCK | ✅ |
| `ls ~/workspace` | 通過 | ✅ |
| `ls \| grep .py` | 通過 | ✅ |
| registry 破損時 | WARN | ✅ |

チェーンバイパス・プレフィックス剥ぎ・bash -c 展開・回帰テストすべて通過。

---

## まとめ

Claude Code の Pre-Bash フックを使えば、課金 API やリスクの高い CLI ツールの実行を **AI 側で自動的にガード**できる。

実装のポイントは：

1. **registry.toml で一元管理**：ツール名・リスク・guard_action を宣言的に管理
2. **コマンドチェーン対策**：`&&/||/;/|` と `bash -c` を再帰展開して全サブコマンドをチェック
3. **fail-closed 原則**：registry パース失敗は通過させず WARN を出す
4. **最悪ケース適用**：チェーン内で最も厳しい guard_action を採用

コードは GitHub に公開予定。フック設定と registry.toml を整備するだけで、Claude Code でも Codex CLI でも同じガードが適用できる。

---

## 参考

- [Claude Code hooks ドキュメント](https://docs.anthropic.com/ja/docs/claude-code/hooks)
- [Codex CLI hooks ドキュメント](https://github.com/openai/codex)
