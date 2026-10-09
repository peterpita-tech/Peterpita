"""一次性測試:GitHub runner 能否用 SEC EDGAR frames API 取得全市場季度財報。"""
import time

import requests

UA = {"User-Agent": "Peterpita strategy dashboard (https://github.com/peterpita-tech/Peterpita)",
      "Accept-Encoding": "gzip, deflate"}
CIKS = {320193: "AAPL", 1045810: "NVDA", 789019: "MSFT"}

r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30)
print("company_tickers", r.status_code, len(r.content), r.text[:150])
time.sleep(0.5)
for concept, unit, period in [("NetIncomeLoss", "USD", "CY2024Q2"), ("NetIncomeLoss", "USD", "CY2023"),
                              ("StockholdersEquity", "USD", "CY2024Q2I"),
                              ("EarningsPerShareDiluted", "USD-per-shares", "CY2024Q2"),
                              ("OperatingIncomeLoss", "USD", "CY2024Q2"),
                              ("Revenues", "USD", "CY2024Q2"),
                              ("RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2024Q2"),
                              ("NetIncomeLoss", "USD", "CY2018Q1")]:
    url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/{period}.json"
    r = requests.get(url, headers=UA, timeout=60)
    print(f"\n{concept} {period}: status={r.status_code} bytes={len(r.content)}")
    if r.ok:
        d = r.json()
        rows = d.get("data", [])
        print("  n =", len(rows), "keys:", list(rows[0].keys()) if rows else None)
        for row in rows:
            if row.get("cik") in CIKS:
                print("  ", CIKS[row["cik"]], row)
    else:
        print("  ", r.text[:300])
    time.sleep(0.5)
