# 台股重大訊息 · 公開資訊觀測站

線上版(每天自動更新):https://peterpita-tech.github.io/Peterpita/news/

- 資料來源是公開資訊觀測站「每日重大訊息」的官方開放資料:
  上市 `openapi.twse.com.tw/v1/opendata/t187ap04_L`、上櫃 `www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O`
- 更新時間:每天台灣 21:50(含假日),台股盤中與收盤後的掃描排程也會順便更新
- OpenAPI 只提供當天的訊息,所以每次會和網站上一版 `data.json` 合併,累積保留最近 90 天
- 網頁可依日期、上市/上櫃、主旨分類、代號 / 公司 / 關鍵字篩選,點開看完整說明

本機執行:

```bash
pip install requests
python fetch_mops_news.py            # → data.json、重大訊息_YYYY-MM-DD.csv
python -m http.server                # 打開 http://localhost:8000 看網頁
```
