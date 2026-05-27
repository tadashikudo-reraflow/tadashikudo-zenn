"""
Qiita → Zenn 記事マイグレーションスクリプト
published の記事のみ対象。実行後に GitHub push + Zenn接続を行うこと。
"""
import json, os, glob, re, sys

VAULT_QIITA = os.path.expanduser(
    "~/Library/CloudStorage/GoogleDrive-tadashi.kudo@reraflow.com"
    "/マイドライブ/ObsidianVault/Qiita"
)
ZENN_ARTICLES = os.path.join(os.path.dirname(__file__), "..", "articles")
ZENN_USERNAME = "tadashikudo"  # Zenn ダッシュボードのユーザー名に合わせること

# タグ→絵文字マッピング（先頭一致）
EMOJI_MAP = [
    (["claude", "claude-code", "anthropic"], "🤖"),
    (["openai", "gpt", "chatgpt"], "🧠"),
    (["oracle", "database", "db", "sql"], "🗄️"),
    (["python", "django", "fastapi"], "🐍"),
    (["cloudflare", "workers", "cf"], "☁️"),
    (["next", "react", "frontend", "ui"], "⚛️"),
    (["docker", "kubernetes", "k8s"], "🐳"),
    (["git", "github", "ci", "cd"], "🔧"),
    (["security", "oauth", "認証"], "🔐"),
    (["ai", "llm", "rag", "vector"], "✨"),
    (["obsidian", "vault", "markdown"], "📝"),
    (["agent", "automation", "自動化"], "⚡"),
    (["seo", "marketing", "google"], "📈"),
    (["aws", "azure", "gcp", "cloud", "クラウド"], "☁️"),
    (["skill", "hook", "scheduled"], "⚙️"),
    (["memory", "context", "prompt"], "💡"),
]

def pick_emoji(tags):
    tags_lower = [t.lower() for t in tags]
    for keywords, emoji in EMOJI_MAP:
        for kw in keywords:
            if any(kw in t for t in tags_lower):
                return emoji
    return "📌"

def sanitize_topic(tag):
    """Zenn topics: 2-20文字, 英数字+日本語のみ"""
    t = re.sub(r"[^\w぀-鿿]", "", tag)  # 記号除去
    t = t[:20]
    return t if len(t) >= 2 else None

def make_slug(raw_slug):
    """50文字以内に収める"""
    slug = raw_slug[:50]
    # 末尾がハイフンで終わらないよう調整
    return slug.rstrip("-")

def convert_article(meta_path: str):
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    if meta.get("status") != "published":
        return None

    article_path = os.path.join(os.path.dirname(meta_path), "article.md")
    if not os.path.exists(article_path):
        return None

    with open(article_path, encoding="utf-8") as f:
        body = f.read()

    raw_slug = meta.get("slug", "") or os.path.basename(os.path.dirname(meta_path))
    slug = make_slug(raw_slug)
    title = meta.get("title", "")
    raw_tags = meta.get("tags", [])
    if isinstance(raw_tags, str):
        raw_tags = [t.strip() for t in raw_tags.split(",")]

    topics = [t for t in (sanitize_topic(tag) for tag in raw_tags) if t][:5]
    if len(topics) < 2:
        topics = (topics + ["tech", "ai"])[:2]

    emoji = pick_emoji(raw_tags)

    title_escaped = title.replace('"', '\\"')
    frontmatter = f"""---
title: "{title_escaped}"
emoji: "{emoji}"
type: "tech"
topics: {json.dumps(topics, ensure_ascii=False)}
published: true
---
"""
    # 既存の frontmatter があれば除去（Qiita形式は本文のみのはずだが念のため）
    body_clean = re.sub(r"^---[\s\S]*?---\s*", "", body)

    zenn_url = f"https://zenn.dev/{ZENN_USERNAME}/articles/{slug}"
    return {
        "slug": slug,
        "raw_slug": raw_slug,
        "zenn_url": zenn_url,
        "content": frontmatter + body_clean,
        "qiita_id": meta.get("qiita_id"),
    }


def main():
    os.makedirs(ZENN_ARTICLES, exist_ok=True)
    results = []
    errors = []

    for meta_path in sorted(glob.glob(f"{VAULT_QIITA}/*/meta.json")):
        result = convert_article(meta_path)
        if not result:
            continue

        out_path = os.path.join(ZENN_ARTICLES, f"{result['slug']}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result["content"])

        slug_note = f" (slug truncated)" if result["slug"] != result["raw_slug"] else ""
        print(f"✅ {result['slug']}.md{slug_note}")
        results.append(result)

    print(f"\n変換完了: {len(results)}記事 → {ZENN_ARTICLES}/")
    print(f"次のステップ:")
    print(f"  1. cd ~/workspace/pj/tadashikudo-zenn && git init && git add . && git commit -m 'initial import'")
    print(f"  2. GitHub で {ZENN_USERNAME}/tadashikudo-zenn を作成して push")
    print(f"  3. https://zenn.dev/dashboard/deploys で GitHub連携を設定")
    print(f"  4. 記事が公開されたことを確認してから scripts/delete_qiita_articles.py を実行")

    # qiita_id リストを保存（削除スクリプトが参照）
    ids_path = os.path.join(os.path.dirname(__file__), "qiita_ids_to_delete.json")
    ids = [{"slug": r["slug"], "qiita_id": r["qiita_id"]} for r in results if r["qiita_id"]]
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)
    print(f"\nQiita削除用IDリスト → {ids_path}")


if __name__ == "__main__":
    main()
