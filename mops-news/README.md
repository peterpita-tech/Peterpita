# 台股重大訊息 · 公開資訊觀測站

線上版(每天自動更新):https://peterpita-tech.github.io/Peterpita/news/

- 資料來源:
  - 觀測站「即時重大訊息」`mopsov.twse.com.tw/mops/web/ajax_t05sr01_1`:當天的訊息,並逐則補抓說明
  - 證交所 / 櫃買中心 OpenAPI(上市 `t187ap04_L`、上櫃 `mopsfin_t187ap04_O`):前一個發言日的完整清單,隔天清晨才更新,用來補漏與校正
- 更新時間:每天台灣 17:50 / 19:50 / 21:50 / 23:50(含假日),台股盤中與收盤後的掃描排程也會順便更新
- 每次和網站上一版 `data.json` 合併,累積保留最近 90 天
- 網頁可依日期、上市/上櫃、主旨分類、代號 / 公司 / 關鍵字篩選,點開看完整說明

本機執行:

```bash
pip install requests
python fetch_mops_news.py            # → data.json、重大訊息_YYYY-MM-DD.csv
python -m http.server                # 打開 http://localhost:8000 看網頁
```
