"""美股財報:SEC EDGAR XBRL frames API(免費,需在 User-Agent 附聯絡 email)。

frames API 一次回傳「某個會計項目、某一期」的全市場數值,所以幾百次請求就能涵蓋 2018 年至今。
- 單季數值:CY{年}Q{季};多數公司第四季只在年報給全年 → 第四季 = 全年(CY{年})− 前三季
- 權益:CY{年}Q{季}I(季底時點值)
- 可用日期(避免回測偷看未來):第一~三季 = 季底 + 45 天(10-Q 期限);第四季 = 季底 + 90 天(10-K 期限)

User-Agent 從環境變數 SEC_USER_AGENT 讀取(GitHub Secret),程式碼裡不寫 email。
"""
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache" / "sec"
API = "https://data.sec.gov/api/xbrl/frames/us-gaap"
FLOWS = {                                   # 欄位 → 依序嘗試的 XBRL 項目(第一個有值的為準)
    "ni": ["NetIncomeLoss"],
    "op": ["OperatingIncomeLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "rev": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
}
UNIT = {"eps": "USD-per-shares"}
MIN_INTERVAL = 0.3                           # SEC 上限每秒 10 次;這裡最多約 3 次


class SEC:
    def __init__(self):
        ua = os.environ.get("SEC_USER_AGENT", "").strip()
        if not ua:
            raise RuntimeError("沒有設定 SEC_USER_AGENT,略過美股財報")
        self.headers = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        self.last = 0.0
        self.calls = 0

    def get(self, url):
        for i in range(4):
            wait = MIN_INTERVAL - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.calls += 1
            try:
                r = requests.get(url, headers=self.headers, timeout=60)
            except requests.RequestException as e:
                print(f"  [retry {i + 1}] {e}", flush=True)
                time.sleep(5 * (i + 1))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:                      # 這一期沒有任何公司申報這個項目
                return None
            if r.status_code == 403:
                raise RuntimeError("SEC 拒絕(403):SEC_USER_AGENT 需包含聯絡 email")
            print(f"  [retry {i + 1}] HTTP {r.status_code}", flush=True)
            time.sleep(10 * (i + 1))
        return None

    def frame(self, concept, unit, period, final):
        """抓一期全市場數值 → {cik: val}。已結束且過了申報期限的期別才寫快取。"""
        path = CACHE / f"{concept}_{period}.json"
        if path.exists():
            return {int(k): v for k, v in json.loads(path.read_text()).items()}
        d = self.get(f"{API}/{concept}/{unit}/{period}.json")
        out = {int(row["cik"]): row["val"] for row in (d or {}).get("data", [])}
        if final and d is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out))
        return out

    def tickers(self):
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=self.headers, timeout=60)
        r.raise_for_status()
        m = {}
        for row in r.json().values():
            m.setdefault(int(row["cik_str"]), []).append(str(row["ticker"]).upper().replace(".", "-"))
        return m


def _q_end(y, q):
    return pd.Timestamp(y, 3 * q, 1) + pd.offsets.MonthEnd(0)


def _avail(y, q):
    return _q_end(y, q) + pd.Timedelta(days=90 if q == 4 else 45)


def fundamentals(start_year=2018):
    """回傳 dict 寬表(索引 = 可用日期,欄 = 股票代號):
    roe(近四季淨利 ÷ 權益,%)、op_growth(單季營業利益年增率,%)、eps_ttm、rev_q(單季營收)、rev_yoy(%)。"""
    sec = SEC()
    today = pd.Timestamp.today()
    quarters = [(y, q) for y in range(start_year, today.year + 1) for q in (1, 2, 3, 4) if _q_end(y, q) < today]
    recs = {}
    for n, (y, q) in enumerate(quarters, 1):
        final = _avail(y, q) + pd.Timedelta(days=30) < today      # 過了申報期限一個月 → 不會再變
        print(f"[sec] {y}Q{q} ({n}/{len(quarters)}),累計 {sec.calls} 次請求", flush=True)
        row = {}
        for field, concepts in FLOWS.items():
            unit = UNIT.get(field, "USD")
            vals = {}
            for c in concepts:                                     # 前面的項目優先
                for cik, v in sec.frame(c, unit, f"CY{y}Q{q}", final).items():
                    vals.setdefault(cik, v)
            if q == 4:                                             # 第四季:全年 − 前三季
                annual = {}
                for c in concepts:
                    for cik, v in sec.frame(c, unit, f"CY{y}", final).items():
                        annual.setdefault(cik, v)
                prev = [recs.get((y, k), {}).get(field, {}) for k in (1, 2, 3)]
                for cik, v in annual.items():
                    if cik not in vals and all(cik in p for p in prev):
                        vals[cik] = v - sum(p[cik] for p in prev)
            row[field] = vals
        row["equity"] = sec.frame("StockholdersEquity", "USD", f"CY{y}Q{q}I", final)
        recs[(y, q)] = row

    rows = []
    for (y, q), row in recs.items():
        ciks = set().union(*[set(v) for v in row.values()])
        for cik in ciks:
            rows.append({"cik": cik, "y": y, "q": q, **{k: row[k].get(cik, np.nan) for k in row}})
    f = pd.DataFrame(rows).sort_values(["cik", "y", "q"])
    f["qidx"] = f["y"] * 4 + f["q"]
    f["avail"] = [_avail(y, q) for y, q in zip(f["y"], f["q"])]
    g = f.groupby("cik")
    consecutive4 = (f["qidx"] - g["qidx"].shift(3)) == 3
    for col, name in (("ni", "ni_ttm"), ("eps", "eps_ttm")):
        f[name] = g[col].rolling(4).sum().reset_index(level=0, drop=True).where(consecutive4)
    f["roe"] = f["ni_ttm"] / f["equity"].where(f["equity"] > 0) * 100
    last_year = (f["qidx"] - g["qidx"].shift(4)) == 4
    op_ly, rev_ly = g["op"].shift(4), g["rev"].shift(4)
    f["op_growth"] = ((f["op"] - op_ly) / op_ly.abs() * 100).where(last_year)
    f["rev_yoy"] = ((f["rev"] / rev_ly - 1) * 100).where(last_year & (rev_ly > 0))
    f["rev_q"] = f["rev"]

    cik2tk = sec.tickers()
    out = {}
    for col in ("roe", "op_growth", "eps_ttm", "rev_q", "rev_yoy"):
        w = f.pivot_table(index="avail", columns="cik", values=col, aggfunc="last")
        cols = {}
        for cik in w.columns:
            for tk in cik2tk.get(int(cik), []):
                cols[tk] = w[cik]
        out[col] = pd.DataFrame(cols).sort_index()
    print(f"[sec] 完成:{f['cik'].nunique()} 家公司、{len(quarters)} 季,共 {sec.calls} 次請求", flush=True)
    return out
