"""
Qiita記事一括削除スクリプト
【注意】Zenn で記事公開を確認してから実行すること。この操作は取り消しできません。
"""
import json, os, sys, time
import urllib.request, urllib.error

QIITA_TOKEN = os.environ.get("QIITA_TOKEN", "")
IDS_PATH = os.path.join(os.path.dirname(__file__), "qiita_ids_to_delete.json")


def delete_item(qiita_id: str) -> bool:
    req = urllib.request.Request(
        f"https://qiita.com/api/v2/items/{qiita_id}",
        method="DELETE",
        headers={"Authorization": f"Bearer {QIITA_TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 204
    except urllib.error.HTTPError as e:
        print(f"  ERROR {e.code}: {e.reason}", file=sys.stderr)
        return False


def main():
    if not QIITA_TOKEN:
        print("エラー: QIITA_TOKEN 環境変数が未設定です", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(IDS_PATH):
        print(f"エラー: {IDS_PATH} が見つかりません。先に migrate_from_qiita.py を実行してください", file=sys.stderr)
        sys.exit(1)

    with open(IDS_PATH, encoding="utf-8") as f:
        items = json.load(f)

    print(f"削除対象: {len(items)}記事")
    print("⚠️  この操作は取り消しできません。Zennで記事公開を確認しましたか？")
    ans = input("続行しますか？ (yes と入力) > ")
    if ans.strip() != "yes":
        print("中断しました")
        sys.exit(0)

    ok, fail = 0, 0
    for item in items:
        qiita_id = item["qiita_id"]
        slug = item["slug"]
        if not qiita_id:
            print(f"  SKIP {slug} (qiita_id なし)")
            continue

        if delete_item(qiita_id):
            print(f"  ✅ 削除: {slug} ({qiita_id})")
            ok += 1
        else:
            print(f"  ❌ 失敗: {slug} ({qiita_id})")
            fail += 1

        time.sleep(0.5)  # レートリミット対策

    print(f"\n完了: 成功={ok} 失敗={fail}")


if __name__ == "__main__":
    main()
