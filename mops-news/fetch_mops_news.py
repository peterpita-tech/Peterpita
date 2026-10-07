"""台股重大訊息 · 公開資訊觀測站 每日抓取

資料來源(公開資訊觀測站「每日重大訊息」的官方開放資料):
    上市 https://openapi.twse.com.tw/v1/opendata/t187ap04_L
    上櫃 https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap04_O

OpenAPI 只提供「當天」的重大訊息,所以每次執行都會和上一版 data.json 合併,
累積保留最近 --days 天。

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
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
TPE = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
SOURCES = {"上市": "https://openapi.twse.com.tw/v1/opendata/t187ap04_L",
           "上櫃": "https://www.tpex.org.tw/openapi/v1/mopsfe_t187ap04_O"}
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
    r["desc"] = re.sub(r"\r\n?", "\n", r["desc"]).strip()
    r["mkt"] = mkt
    return r if r["code"] and r["date"] and r["subject"] else None


def fetch(mkt):
    r = requests.get(SOURCES[mkt], headers=UA, timeout=60)
    r.raise_for_status()
    rows = [n for n in map(lambda x: normalize(x, mkt), r.json()) if n]
    print(f"[{mkt}] {len(rows)} 則")
    return rows


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
    return (r["mkt"], r["code"], r["date"], r["time"], r["subject"])


def main():
    ap = argparse.ArgumentParser(description="公開資訊觀測站 台股重大訊息抓取")
    ap.add_argument("--prev", default=str(HERE / "data.json"),
                    help="上一版 data.json 的路徑或網址(用來累積歷史)")
    ap.add_argument("--days", type=int, default=90, help="保留最近幾天(預設 90)")
    a = ap.parse_args()

    new, failed = [], []
    for mkt in SOURCES:
        try:
            new += fetch(mkt)
        except Exception as e:  # noqa: BLE001
            failed.append(mkt)
            print(f"[{mkt}] 失敗: {e}", file=sys.stderr)
    if not new:
        sys.exit("上市、上櫃都沒有抓到任何重大訊息")

    merged = {key(r): r for r in load_prev(a.prev)}
    added = sum(1 for r in new if key(r) not in merged)
    merged.update({key(r): r for r in new})
    cutoff = (datetime.now(TPE).date() - timedelta(days=a.days)).isoformat()
    rows = sorted((r for r in merged.values() if r["date"] >= cutoff),
                  key=lambda r: (r["date"], r["time"], r["code"]), reverse=True)

    latest = max(r["date"] for r in new)
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
