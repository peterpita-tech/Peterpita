# Zsigma 籌碼微結構掃描 · 全台股

線上版(每個交易日 15:30 自動更新):https://peterpita-tech.github.io/Peterpita/tw/

- 介面沿用原版;代號清單來自 TWSE / TPEx OpenAPI,日線來自 Yahoo Finance(`.TW` / `.TWO`)
- 指標計算和美股版共用 `../zsigma-us/fetch_zsigma_us.py`(強勢分數公式見美股版 README)
- 原文三檔標的(加捷 4109 / 佶優 5452 / 鈦昇 8027)的真實 Buy_Gini 寫在 `fetch_zsigma_tw.py` 的 `ARTICLES`

本機執行:

```bash
pip install -r ../zsigma-us/requirements.txt
python fetch_zsigma_tw.py
```
