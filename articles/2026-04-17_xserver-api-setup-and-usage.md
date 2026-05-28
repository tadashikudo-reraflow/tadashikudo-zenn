---
title: "XServer APIキーの取得・設定と活用ガイド——ドメイン管理・SSL自動化をCLIで実現する"
emoji: "🐍"
type: "tech"
topics: ["Xserver", "API", "Python", "ドメイン管理", "SSL"]
published: false
---
# XServer APIキーの取得・設定と活用ガイド——ドメイン管理・SSL自動化をCLIで実現する

## はじめに

XServer（エックスサーバー）は国内シェアNo.1クラスのレンタルサーバーですが、管理パネルをポチポチ操作している方が多いのではないでしょうか。実は **XServer API** を使えば、ドメイン追加・削除・SSL証明書インストールをすべてコマンドラインや自動化スクリプトから実行できます。

この記事では以下をカバーします。

- APIキーの取得と安全な設定方法
- APIでできること（エンドポイント概要）
- Python製CLIツールによる実践的な自動化例
- 運用で役立つベストプラクティス

複数ドメインを管理している方や、CI/CDパイプラインからドメイン操作を行いたい方に特に参考になる内容です。

---

## APIキーの取得方法

### 1. XServerパネルにログイン

[XServerサーバーパネル](https://www.xserver.ne.jp/login/) にログインし、上部メニューから **「アカウント」→「APIキー設定」** を選択します。

### 2. APIキーを発行

「APIキーを発行する」ボタンをクリックすると、以下の情報が表示されます。

| 項目 | 内容 |
|------|------|
| APIキー | `xs_xxxxxxxxxxxxxxxxxxxx...`（64文字程度） |
| 有効期限 | 発行から約1年 |
| 権限 | full（全操作可能） |

> ⚠️ APIキーは**発行直後の1回しか表示されません**。必ずコピーしてすぐに安全な場所に保存してください。

### 3. APIエンドポイントのベースURL

```
https://api.xserver.ne.jp/v1
```

すべてのリクエストは `Authorization: Bearer <APIキー>` ヘッダーを付けて送信します。

---

## 環境変数での安全な設定

APIキーをコードにハードコードするのは禁止事項です。`.env` ファイルで管理しましょう。

```bash
# ~/.config/xserver/.env（または ~/workspace/scripts/.env.xserver）
XS_API_KEY=YOUR_API_KEY_HERE
XS_SERVER=yourserver.xsrv.jp
```

```bash
# .gitignore に必ず追加
echo ".env.xserver" >> .gitignore
echo ".env*" >> .gitignore
```

Python での読み込み例：

```python
import os

def load_env(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env(os.path.expanduser("~/.config/xserver/.env"))
API_KEY = os.environ.get("XS_API_KEY", "")  # フォールバックは空文字
```

---

## XServer APIでできること

### 主要エンドポイント一覧

| カテゴリ | メソッド | エンドポイント | 説明 |
|---------|---------|--------------|------|
| アカウント | GET | `/me` | APIキー情報・サーバー名取得 |
| ドメイン | GET | `/server/{server}/domain` | ドメイン一覧取得 |
| ドメイン | POST | `/server/{server}/domain` | ドメイン追加 |
| ドメイン | GET | `/server/{server}/domain/{domain}` | ドメイン詳細 |
| ドメイン | DELETE | `/server/{server}/domain/{domain}` | ドメイン削除 |
| サブドメイン | GET | `/server/{server}/subdomain` | サブドメイン一覧 |
| サブドメイン | POST | `/server/{server}/subdomain` | サブドメイン追加 |
| サブドメイン | DELETE | `/server/{server}/subdomain/{subdomain}` | サブドメイン削除 |
| SSL | POST | `/server/{server}/ssl` | SSL証明書インストール |

---

## Python CLIで自動化する

### インストール

```bash
pip install requests
```

### 基本的なリクエスト関数

```python
import requests
import json
import os
import sys

BASE_URL = "https://api.xserver.ne.jp/v1"

def headers():
    api_key = os.environ.get("XS_API_KEY", "")
    if not api_key:
        print("XS_API_KEY が未設定です")
        sys.exit(1)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

def req(method, path, **kwargs):
    url = f"{BASE_URL}{path}"
    resp = getattr(requests, method)(url, headers=headers(), **kwargs)
    if resp.status_code >= 400:
        print(f"[ERROR {resp.status_code}] {resp.text}")
        sys.exit(1)
    return resp.json() if resp.text else {}
```

### ドメイン一覧を取得する

```python
def list_domains(server):
    data = req("get", f"/server/{server}/domain")
    for d in data.get("domains", []):
        ssl_status = "✓" if d.get("ssl") else "—"
        print(f"{d['domain']:<40} SSL:{ssl_status}")
```

### ドメインをSSL付きで追加する

```python
def add_domain(server, domain):
    payload = {
        "domain": domain,
        "ssl": True,
        "https_redirect": True   # HTTPを自動的にHTTPSへ転送
    }
    req("post", f"/server/{server}/domain", json=payload)
    print(f"✓ 追加完了: {domain}")
```

### SSL未設定ドメインに一括インストール

```python
def bulk_ssl_install(server):
    domains = req("get", f"/server/{server}/domain").get("domains", [])
    targets = [d["domain"] for d in domains if not d.get("ssl")]
    
    if not targets:
        print("SSL未設定ドメインはありません")
        return
    
    print(f"SSL未設定: {len(targets)}件")
    for domain in targets:
        result = req("post", f"/server/{server}/ssl", json={"common_name": domain})
        print(f"  ✓ {domain}: {result.get('message', 'OK')}")
```

### 実行例

```bash
# サーバー名の自動取得
python3 xserver_cli.py servers

# ドメイン一覧＋SSL状況
python3 xserver_cli.py domain-list

# ドメイン追加（SSL+HTTPS転送付き）
python3 xserver_cli.py domain-add example.com --ssl --https-redirect

# サブドメイン追加
python3 xserver_cli.py subdomain-add dev.example.com --ssl

# SSL未設定を一括対応
python3 xserver_cli.py ssl-install
```

---

## CI/CDでの活用例

GitHub Actionsからドメインを自動追加する例：

```yaml
# .github/workflows/add-domain.yml
name: Add Domain to XServer
on:
  workflow_dispatch:
    inputs:
      domain:
        description: 'Domain to add'
        required: true

jobs:
  add-domain:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install dependencies
        run: pip install requests
      - name: Add domain
        env:
          XS_API_KEY: ${{ secrets.XSERVER_API_KEY }}
          XS_SERVER: ${{ secrets.XSERVER_SERVER }}
        run: |
          python3 scripts/xserver_cli.py domain-add ${{ inputs.domain }} --ssl --https-redirect
```

GitHub SecretsにAPIキーを登録することで、コードにキーを含めず安全に実行できます。

---

## ベストプラクティス

- **APIキーは有効期限（約1年）を管理する**: カレンダーや監視ツールで更新リマインダーを設定する
- **ドメイン追加時は必ず `--ssl --https-redirect` をセットにする**: SSL忘れによる通信暗号化漏れを防ぐ
- **`www` サブドメインは登録不要**: XServerの仕様でベースドメインと自動的に同じルーティングになる
- **削除操作には確認プロンプトを挟む**: 自動化スクリプトでも `-y` フラグは本番環境で使わない
- **APIキーはフォールバックに実値を書かない**: `os.environ.get("XS_API_KEY", "")` のように空文字にする

---

## まとめ

XServer APIを使うことで、これまで管理パネルでしかできなかった操作をすべてコードから実行できます。

- **APIキー取得**: XServerパネルの「アカウント→APIキー設定」から発行
- **認証**: `Authorization: Bearer <APIキー>` ヘッダーを全リクエストに付与
- **できること**: ドメイン追加/削除、サブドメイン管理、SSL一括インストール
- **セキュリティ**: キーは`.env`ファイルで管理し、Gitには含めない
- **CI/CD連携**: GitHub Secretsと組み合わせてパイプライン内から自動実行可能

複数サイトを管理している場合や、新規サービスのドメイン設定をデプロイフローに組み込みたい場合に非常に効果的です。ぜひ活用してみてください。
