"""台股重大訊息 · 公開資訊觀測站 每日抓取

資料來源:
  1. 觀測站「即時重大訊息」(當天,含說明):
     https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1
  2. 證交所 / 櫃買中心 OpenAPI(前一個發言日的完整清單,隔天清晨才更新,用來補漏):
     上市 https://openapi.twse.com.tw/v1/opendata/t187ap04_L
     上櫃 https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O

兩邊都只給一天的資料,所以每次執行都會和上一版 data.json 合併,累積保留最近 --days 天。

用法:
    pip install requests
    python fetch_mops_news.py
    python fetch_mops_news.py --prev https://peterpita-tech.github.io/Peterpita/news/data.json

輸出(與本檔同資料夾):
    data.json                 ← index.html 會讀這個
    重大訊息_YYYY-MM-DD.csv    ← 當次抓到的最新一天(Excel 可直接開)
"""
import argparse
import csv
import html
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
SOURCES = {"上市": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
           "上櫃": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"}
REALTIME = "https://mopsov.twse.com.tw/mops/web/ajax_t05sr01_1"
TYPEK = {"上市": "sii", "上櫃": "otc"}
DETAIL_MAX = 400      # 每次最多補抓幾則說明(其餘留給下次或隔天的 OpenAPI)
DETAIL_DELAY = 0.5    # 秒;避免觀測站判定查詢過於頻繁
# 欄位名稱在兩個 OpenAPI 間略有不同(有時還帶空白),逐一嘗試
FIELDS = {
    "date":   ("發言日期", "SpeakDate", "Date"),
    "time":   ("發言時間", "SpeakTime", "Time"),
    "code":   ("公司代號", "SecuritiesCompanyCode", "CompanyCode", "Code"),
    "name":   ("公司名稱", "CompanyName", "Name"),
    "subject": ("主旨", "Subject"),
    "clause": ("符合條款", "Clause", "Article"),
    "eventDate": ("事實發生日", "OccurrenceDate", "EventDate"),
    "desc":   ("說明", "Description", "Content"),
}


def roc_date(s):
    """1151007 / 115/10/07 / 20261007 / 2026-10-07 → 2026-10-07;無法解析回傳 ''。"""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 8 and d[:2] in ("19", "20"):
        y, m, dd = int(d[:4]), int(d[4:6]), int(d[6:])
    elif len(d) in (6, 7):
        y, m, dd = int(d[:-4]) + 1911, int(d[-4:-2]), int(d[-2:])
    else:
        return ""
    try:
        return date(y, m, dd).isoformat()
    except ValueError:
        return ""


def hms(s):
    d = re.sub(r"\D", "", str(s or "")).zfill(6)[-6:]
    return f"{d[:2]}:{d[2:4]}:{d[4:]}" if d.strip("0") else ""


def pick(rec, keys):
    for k in keys:
        if k in rec and rec[k] not in (None, ""):
            return str(rec[k]).strip()
    return ""


def normalize(rec, mkt):
    rec = {str(k).strip(): v for k, v in rec.items()}
    r = {f: pick(rec, keys) for f, keys in FIELDS.items()}
    r["date"], r["time"] = roc_date(r["date"]), hms(r["time"])
    r["eventDate"] = roc_date(r["eventDate"]) or r["eventDate"]
    r["subject"] = re.sub(r"\s*[\r\n]+\s*", "", r["subject"])   # 主旨常被硬斷行
    r["desc"] = re.sub(r"\r\n?", "\n", r["desc"]).strip()
    r["mkt"] = mkt
    return r if r["code"] and r["date"] and r["subject"] else None


def fetch(mkt):
    r = requests.get(SOURCES[mkt], headers=UA, timeout=60)
    r.raise_for_status()
    rows = [n for n in map(lambda x: normalize(x, mkt), r.json()) if n]
    print(f"[{mkt}] {len(rows)} 則")
    return rows


def text(h):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", h))).strip()


def post_realtime(form):
    r = requests.post(REALTIME, data=form, headers={"User-Agent": UA["User-Agent"]}, timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def fetch_realtime(mkt):
    """觀測站「即時重大訊息」:當天到目前為止的清單(只有主旨,說明另外抓)。"""
    page = post_realtime({"encodeURIComponent": 1, "step": 0, "firstin": 1, "off": 1, "TYPEK": TYPEK[mkt]})
    if "fm_t05sr01_1" not in page:
        raise RuntimeError("回應格式不符(可能被擋或改版)")
    rows = []
    for tr in re.findall(r"<tr class='(?:odd|even)'>(.*?)</tr>", page, re.S):
        td = [text(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        form = dict(re.findall(r"fm_t05sr01_1\.(\w+)\.value='([^']*)'", tr))
        if len(td) < 5 or not form.get("COMPANY_ID"):
            continue
        r = {"date": roc_date(td[2]), "time": hms(td[3]), "code": td[0], "name": td[1],
             "subject": td[4], "clause": "", "eventDate": "", "desc": "", "mkt": mkt,
             "_form": {"TYPEK": "all", "step": 1, "firstin": "true", **form}}
        if r["code"] and r["date"]:
            rows.append(r)
    print(f"[即時 {mkt}] {len(rows)} 則")
    return rows


def fetch_detail(r):
    """補上即時訊息的符合條款、事實發生日與說明。"""
    page = post_realtime(r["_form"])
    m = re.search(r"<pre[^>]*>(.*?)</pre>", page, re.S)
    if not m:
        raise RuntimeError("找不到說明")
    r["desc"] = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).replace("\r\n", "\n").strip()
    if c := re.search(r"符合條款.*?第</th>\s*<td[^>]*>(.*?)</td>", page, re.S):
        r["clause"] = f"第{text(c.group(1))}款"
    if d := re.search(r"事實發生日</th>\s*<td[^>]*>(.*?)</td>", page, re.S):
        r["eventDate"] = roc_date(text(d.group(1))) or text(d.group(1))


def load_prev(src):
    if not src:
        return []
    try:
        if re.match(r"https?://", src):
            r = requests.get(src, headers=UA, timeout=30)
            r.raise_for_status()
            data = r.json()
        else:
            data = json.loads(Path(src).read_text(encoding="utf-8"))
        rows = data.get("rows", [])
        print(f"[prev] 沿用舊資料 {len(rows)} 則")
        return rows
    except Exception as e:  # noqa: BLE001
        print(f"[prev] 讀不到舊資料({e}),從頭開始", file=sys.stderr)
        return []


def key(r):
    # 主旨在即時頁與 OpenAPI 的斷行寫法不同,不能拿來比對
    return (r["code"], r["date"], r["time"])


def main():
    ap = argparse.ArgumentParser(description="公開資訊觀測站 台股重大訊息抓取")
    ap.add_argument("--prev", default=str(HERE / "data.json"),
                    help="上一版 data.json 的路徑或網址(用來累積歷史)")
    ap.add_argument("--days", type=int, default=90, help="保留最近幾天(預設 90)")
    a = ap.parse_args()

    merged = {key(r): r for r in load_prev(a.prev)}
    before = set(merged)
    failed, ok = [], 0

    # 1) 即時:當天的新訊息先放進來,沒有說明的再逐則補抓
    for mkt in TYPEK:
        try:
            live = fetch_realtime(mkt)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append(f"即時{mkt}")
            print(f"[即時 {mkt}] 失敗: {e}", file=sys.stderr)
            continue
        for r in live:
            old = merged.get(key(r))
            if old and old.get("desc"):
                continue
            merged[key(r)] = r
    todo = [r for r in merged.values() if "_form" in r][:DETAIL_MAX]
    got = 0
    for r in todo:
        try:
            fetch_detail(r)
            got += 1
        except Exception as e:  # noqa: BLE001
            print(f"[說明] {r['code']} {r['time']} 失敗: {e}", file=sys.stderr)
        time.sleep(DETAIL_DELAY)
    if todo:
        print(f"[說明] 補抓 {got}/{len(todo)} 則")

    # 2) OpenAPI:前一個發言日的完整版,蓋過即時資料並補上漏抓的
    for mkt in SOURCES:
        try:
            merged.update({key(r): r for r in fetch(mkt)})
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed.append(mkt)
            print(f"[{mkt}] 失敗: {e}", file=sys.stderr)
    if not ok:
        sys.exit("所有資料來源都失敗")

    for r in merged.values():
        r.pop("_form", None)
    added = len(set(merged) - before)
    cutoff = (datetime.now(TPE).date() - timedelta(days=a.days)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff),
                  key=lambda r: (r["date"], r["time"], r["code"]), reverse=True)

    if not rows:
        sys.exit("沒有任何重大訊息")
    latest = rows[0]["date"]
    data = {
        "generatedAt": int(datetime.now(timezone.utc).timestamp() * 1000),
        "latest": latest,
        "failed": failed,
        "days": a.days,
        "total": len(rows),
        "rows": rows,
    }
    (HERE / "data.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    today = [r for r in rows if r["date"] == latest]
    with open(HERE / f"重大訊息_{latest}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["市場", "發言日期", "發言時間", "公司代號", "公司名稱", "主旨", "符合條款", "事實發生日", "說明"])
        for r in today:
            w.writerow([r["mkt"], r["date"], r["time"], r["code"], r["name"], r["subject"],
                        r["clause"], r["eventDate"], r["desc"]])
    print(f"[done] {latest}: {len(today)} 則(本次新增 {added})· 累積 {len(rows)} 則 → data.json")


if __name__ == "__main__":
    main()
