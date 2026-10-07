# Zsigma 籌碼微結構掃描 · 美股版

把原本的「Zsigma 籌碼掃描 · 全台股」搬到美股(NASDAQ / NYSE / AMEX 全部普通股)。

## 線上版(每天自動更新)

👉 美股:https://peterpita-tech.github.io/Peterpita/ · 台股:https://peterpita-tech.github.io/Peterpita/tw/

GitHub Actions(`.github/workflows/zsigma-us.yml`)會在每個美股交易日收盤後(台灣時間週二~週六 06:30)自動抓資料、重新發布網頁。
也可以到 repo 的 **Actions → Zsigma 美股掃描 → Run workflow** 手動更新。

第一次需要啟用 Pages:repo **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。

## 使用方式(Windows 本機)

1. 安裝 Python 3.10 以上
2. 雙擊 `run.bat`:會自動安裝套件、抓全美股資料(約 5~10 分鐘),完成後打開 `Zsigma籌碼掃描_美股.html`

或用命令列:

```bash
pip install -r requirements.txt
python fetch_zsigma_us.py                        # 全美股
python fetch_zsigma_us.py --tickers AAPL,NVDA,TSLA
python fetch_zsigma_us.py --min-dollar-vol 5     # 20日均成交額 ≥ 500 萬美元才收
```

產出:
- `Zsigma籌碼掃描_美股.html`:資料內嵌的單檔版,可直接分享
- `data.js`:給 `index.html` 讀取

## 和台股版的差異

| 項目 | 台股版 | 美股版 |
|---|---|---|
| 資料來源 | TWSE + TPEx 日線 | NASDAQ 代號表 + Yahoo Finance(還原權值) |
| 市場分類 | 上市 / 上櫃 | NASDAQ / NYSE / AMEX |
| Z-Score、成本線、波動率、集中度(估) | OHLCV 計算 | 公式相同 |
| 原文 Buy_Gini / 洗盤機率 | 3 檔原文標的有 | 無(美股沒有券商分點資料) |
| 新增 | — | 產業、市值、量比、20日均成交額篩選;漲跌顏色可切換 |
| 預設篩除 | — | 股價 < $1、20日均成交額 < $1M |

強勢分數 = Z-Score(30%) + 月線乖離(20%) + 贏家成本乖離(20%) + 集中度(20%) 的全市場百分位,再加上獲利比例(10%)。

成本線採「籌碼分布法」:近 60 日每天的成交量平均攤在當日高低價之間、依天數衰減(半衰期 20 日),以現價切開:
- 贏家成本線 = 現價以下(獲利)籌碼的平均成本
- 受困成本線 = 現價以上(套牢)籌碼的平均成本
- 獲利比例 = 獲利籌碼占全部籌碼的比例

僅供研究參考,不構成投資建議。
