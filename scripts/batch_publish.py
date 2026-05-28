"""
Zennレートリミット対策: published: false の記事をN件ずつ true に変換してpush。
毎日1回実行する（Zennの上限目安: 10件/日）

使い方:
  python3 scripts/batch_publish.py          # 次の10件を有効化してpush
  python3 scripts/batch_publish.py --n 5   # 次の5件
  python3 scripts/batch_publish.py --dry-run  # 変更せず確認のみ
"""
import os, re, subprocess, sys

ARTICLES_DIR = os.path.join(os.path.dirname(__file__), "..", "articles")
DEFAULT_BATCH = 10


def get_false_articles():
    result = []
    for f in sorted(os.listdir(ARTICLES_DIR)):
        if not f.endswith(".md"):
            continue
        path = os.path.join(ARTICLES_DIR, f)
        content = open(path, encoding="utf-8").read()
        if re.search(r"^published: false$", content, re.MULTILINE):
            result.append((f[:-3], path, content))
    return result


def main():
    dry_run = "--dry-run" in sys.argv
    n = DEFAULT_BATCH
    for i, arg in enumerate(sys.argv):
        if arg == "--n" and i + 1 < len(sys.argv):
            n = int(sys.argv[i + 1])

    pending = get_false_articles()
    print(f"未デプロイ: {len(pending)}件 / 今回有効化: {min(n, len(pending))}件")

    if not pending:
        print("全記事デプロイ済みです。")
        return

    batch = pending[:n]
    slugs = []
    for slug, path, content in batch:
        new_content = re.sub(r"^published: false$", "published: true", content, flags=re.MULTILINE)
        if not dry_run:
            open(path, "w", encoding="utf-8").write(new_content)
        print(f"  {'[DRY]' if dry_run else '✅'} {slug}")
        slugs.append(f"articles/{slug}.md")

    if dry_run:
        print("\n--dry-run: 変更は保存されていません。")
        return

    # git add & commit & push
    subprocess.run(["git", "add"] + slugs, cwd=os.path.join(ARTICLES_DIR, ".."), check=True)
    msg = f"Zenn batch publish: {len(batch)}件 有効化 (残{len(pending)-len(batch)}件)"
    subprocess.run(["git", "commit", "-m", msg], cwd=os.path.join(ARTICLES_DIR, ".."), check=True)
    subprocess.run(["git", "push"], cwd=os.path.join(ARTICLES_DIR, ".."), check=True)
    print(f"\npush完了。残り{len(pending)-len(batch)}件。明日また実行してください。")


if __name__ == "__main__":
    main()
