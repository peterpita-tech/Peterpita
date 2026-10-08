"""玩股網可行性測試:只發少量請求,看 GitHub runner 能否取得分點資料。不做大量抓取。"""
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
BASE = "https://www.wantgoo.com"
PAGES = [
    "/robots.txt",
    "/terms-and-policies",
    "/stock/major-investors/broker-buy-sell-rank",
    "/stock/2330/major-investors/main-trend",
    "/stock/2330/major-investors/branch-buysell",
]
KEYWORDS = r"爬蟲|機器人|自動化|程式擷取|擷取|重製|轉載|robot|crawler|scrap"


def show(path, r):
    t = r.text
    title = re.search(r"<title>(.*?)</title>", t, re.S)
    print(f"\n=== {path}\nstatus={r.status_code} bytes={len(t)} server={r.headers.get('server')} "
          f"cf-mitigated={r.headers.get('cf-mitigated')} final_url={r.url}")
    print("title:", title.group(1).strip()[:120] if title else None)
    low = t.lower()
    flags = {k: (k in low) for k in ("just a moment", "cf-chl", "captcha", "turnstile", "登入", "會員", "vip")}
    print("flags:", flags)
    if path == "/robots.txt":
        print(t[:3000])
        return
    if path == "/terms-and-policies":
        text = re.sub(r"<[^>]+>", " ", t)
        for m in re.finditer(KEYWORDS, text):
            s = text[max(0, m.start() - 120): m.end() + 160]
            print("  ...", re.sub(r"\s+", " ", s), "...")
        return
    # 找出頁面背後載資料的 API / script
    urls = sorted(set(re.findall(r"""["'](/[A-Za-z0-9_\-/]*(?:api|data|investrue|major-investors)[A-Za-z0-9_\-/\.\?=&]*)["']""", t)))
    print("api-like paths:", urls[:40])
    scripts = re.findall(r'<script[^>]+src="([^"]+)"', t)
    print("scripts:", scripts[:15])
    # 是否直接含有券商 / 分點字樣與數字表格
    print("contains 券商/分點:", "券商" in t, "分點" in t, "| <table:", t.count("<table"), "| <tr:", t.count("<tr"))


for p in PAGES:
    try:
        show(p, requests.get(BASE + p, headers=UA, timeout=30))
    except Exception as e:  # noqa: BLE001
        print(f"\n=== {p}\nERROR {e}")
    time.sleep(3)
