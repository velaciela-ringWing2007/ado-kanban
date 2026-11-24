#!/usr/bin/env python3
"""Azure DevOps の My Query の結果を、手動ログインしたブラウザのセッションで取得して CSV に保存する。

使い方（単体でも動く）:
    python fetch_ado.py --url "https://dev.azure.com/組織/プロジェクト/_queries/query/<GUID>/" --out latest_ado.csv

しくみ:
  1. Playwright で専用プロファイルのブラウザを起動（ログイン状態はこのプロファイルに残る）
  2. まず画面を出さずに API を叩いてみる → ログイン済みならそのまま取得
  3. 未ログインなら画面付きでクエリを開き直し、ねぶきんが手でログインするのを待つ
  4. ブラウザのログインセッションで REST API を呼ぶ
     - GET _apis/wit/wiql/{クエリID}   … クエリを実行して ID 一覧（ツリー型なら親子関係も）を取る
     - GET _apis/wit/workitems?ids=…  … 200件ずつ、各項目の中身を取る
  5. カンバンが読める列名（ID, Title, State, Parent …）で CSV に書く
"""
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = [
    "System.Id", "System.WorkItemType", "System.Title", "System.AssignedTo",
    "System.State", "System.Parent", "System.Tags", "System.IterationPath",
]
HEADER = ["ID", "Work Item Type", "Title", "Assigned To", "State", "Parent", "Tags", "Iteration Path"]
GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class NeedLogin(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def parse_query_url(url):
    """クエリのURLから (base, project, queryId) を取り出す。
    例: https://dev.azure.com/org/proj/_queries/query/<GUID>/
        -> ("https://dev.azure.com/org", "proj", "<GUID>")
    org.visualstudio.com/proj/… やオンプレの server/tfs/Coll/proj/… も同じルールで分解できる。"""
    sp = urlsplit(url.strip())
    segs = [s for s in sp.path.split("/") if s]
    if "_queries" not in segs:
        raise SystemExit(f"ERROR クエリのURLに /_queries/ が見つからないよ: {url}")
    i = segs.index("_queries")
    m = GUID.search("/".join(segs[i:]))
    if i < 1 or not m:
        raise SystemExit(f"ERROR URLからプロジェクト名かクエリIDが読めないよ: {url}")
    base = f"{sp.scheme}://{sp.netloc}" + ("/" + "/".join(segs[: i - 1]) if i > 1 else "")
    return base, segs[i - 1], m.group(0)


def api_get(ctx, url):
    """ブラウザのログイン Cookie を使って GET。未ログインっぽい応答なら NeedLogin。"""
    r = ctx.request.get(url, headers={"Accept": "application/json"}, max_redirects=0, fail_on_status_code=False)
    ctype = r.headers.get("content-type", "")
    # 未ログインだと 401/403、ログイン画面へのリダイレクト(3xx)、
    # もしくは 203 + HTML のサインイン画面が返ってくることがある
    if r.status in (401, 403) or 300 <= r.status < 400 or r.status == 203 or (r.ok and "json" not in ctype):
        raise NeedLogin(f"HTTP {r.status}")
    if not r.ok:
        raise RuntimeError(f"HTTP {r.status}: {r.text()[:400]}")
    return r.json()


def fetch_rows(ctx, base, project, qid, api):
    w = api_get(ctx, f"{base}/{project}/_apis/wit/wiql/{qid}?api-version={api}")
    ids, seen, parents = [], set(), {}
    for x in w.get("workItems") or []:          # フラットなクエリ
        if x["id"] not in seen:
            seen.add(x["id"])
            ids.append(x["id"])
    for rel in w.get("workItemRelations") or []:  # ツリー / 1ホップのクエリ
        t = (rel.get("target") or {}).get("id")
        s = (rel.get("source") or {}).get("id")
        if t is None:
            continue
        if t not in seen:
            seen.add(t)
            ids.append(t)
        if s and str(rel.get("rel", "")).endswith("Hierarchy-Forward"):
            parents[t] = s
    log(f"クエリ結果: {len(ids)}件")

    fields = list(FIELDS)
    got = {}
    i = 0
    while i < len(ids):
        chunk = ids[i:i + 200]
        url = (f"{base}/{project}/_apis/wit/workitems?ids={','.join(map(str, chunk))}"
               f"&fields={','.join(fields)}&errorPolicy=omit&api-version={api}")
        try:
            d = api_get(ctx, url)
        except RuntimeError as e:
            # 古いオンプレ版などで System.Parent が無いときは外して取り直す
            if "System.Parent" in fields and "Parent" in str(e):
                log("System.Parent が使えないみたいなので外して取り直すね")
                fields.remove("System.Parent")
                continue
            raise
        for it in d.get("value") or []:
            if it:
                got[it["id"]] = it.get("fields", {})
        i += 200

    rows = []
    for wid in ids:
        f = got.get(wid)
        if f is None:
            continue
        at = f.get("System.AssignedTo")
        at = at.get("displayName", "") if isinstance(at, dict) else (at or "")
        rows.append([
            wid, f.get("System.WorkItemType", ""), f.get("System.Title", ""), at,
            f.get("System.State", ""), f.get("System.Parent") or parents.get(wid) or "",
            f.get("System.Tags", ""), f.get("System.IterationPath", ""),
        ])
    return rows


def main():
    ap = argparse.ArgumentParser(description="ADO の My Query を CSV に保存")
    ap.add_argument("--url", required=True, help="My Query を開いたときのURL")
    ap.add_argument("--out", required=True, help="保存先 CSV")
    ap.add_argument("--profile", default=str(Path.home() / ".ado-kanban" / "browser-profile"),
                    help="ログイン状態を保存するブラウザプロファイルの場所")
    ap.add_argument("--channel", default="msedge", choices=["msedge", "chrome", "chromium"],
                    help="msedge/chrome はPCに入っているものを使う。chromium は playwright install chromium が必要")
    ap.add_argument("--headed", action="store_true", help="ログイン済みでも毎回ブラウザを表示する")
    ap.add_argument("--api-version", default="7.0")
    ap.add_argument("--login-timeout", type=int, default=300, help="手動ログインを待つ秒数")
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright

    base, project, qid = parse_query_url(a.url)
    probe = f"{base}/{project}/_apis/wit/wiql/{qid}?api-version={a.api_version}"
    Path(a.profile).mkdir(parents=True, exist_ok=True)
    channel = None if a.channel == "chromium" else a.channel

    with sync_playwright() as p:
        for headless in ([False] if a.headed else [True, False]):
            ctx = p.chromium.launch_persistent_context(a.profile, headless=headless, channel=channel)
            try:
                try:
                    api_get(ctx, probe)
                    logged_in = True
                except NeedLogin:
                    logged_in = False
                if not logged_in and headless:
                    log("ログインが必要みたい。ブラウザを表示して開き直すね")
                    continue
                if not logged_in or a.headed:
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    page.goto(a.url)
                if not logged_in:
                    log(f"開いたブラウザでログインしてね（{a.login_timeout}秒待つよ）")
                    deadline = time.time() + a.login_timeout
                    while not logged_in and time.time() < deadline:
                        page.wait_for_timeout(3000)
                        try:
                            api_get(ctx, probe)
                            logged_in = True
                        except NeedLogin:
                            pass
                    if not logged_in:
                        raise SystemExit("ERROR ログインを待ちきれなかったよ（タイムアウト）")
                    log("ログインを確認したよ")
                rows = fetch_rows(ctx, base, project, qid, a.api_version)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
            out = Path(a.out)
            with out.open("w", encoding="utf-8-sig", newline="") as fp:
                w = csv.writer(fp)
                w.writerow(HEADER)
                w.writerows(rows)
            log("RESULT " + json.dumps({"count": len(rows), "out": str(out)}, ensure_ascii=False))
            return
    raise SystemExit("ERROR 取得できなかったよ")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # ブラウザを途中で閉じた等
        print(f"ERROR {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
