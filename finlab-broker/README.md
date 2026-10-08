# FinLab 券商分點買賣超:走勢圖 + 選股回測

用 [FinLab](https://finlab.finance/data/broker-branch-flow) 的券商分點資料(`broker_transactions`,2018 年起每日更新),
示範:

1. **走勢圖**:某檔股票的收盤價 vs「前 15 大分點淨買超」(每日 + 20 日累計)
2. **回測**:每週選出「主力買盤最一面倒」的 20 檔股票

## 用到的資料

| 資料表 | 內容 |
|---|---|
| `etl:broker_transactions:top15_buy` | 前 15 大買超分點的買超量合計(日 × 股票) |
| `etl:broker_transactions:top15_sell` | 前 15 大賣超分點的賣超量合計 |
| `price:收盤價`、`price:成交金額` | 股價與流動性 |

**主力淨買超** = `top15_buy − top15_sell`

> 原始明細表 `data.get("broker_transactions")` 是每檔每天每個分點一列的長表格,資料量很大,本示範用上面整理好的矩陣。

## 選股邏輯

- **因子**:近 20 日 前 15 大分點淨買超 ÷ 前 15 大分點總進出(介於 −1 ~ 1,越接近 1 代表主力買盤越一面倒)
- **過濾**:20 日均成交額 > 5,000 萬,且收盤價站上 60 日均線
- **持股**:因子最高的 20 檔,等權重
- **換股**:每週一次,下一個交易日開盤價成交,手續費與交易稅用 FinLab 預設值

## 會員等級

- **免費會員**:台股歷史資料只到 2023-12-31,可以跑到 2023 年底的回測
- **VIP**:有最新資料,價格請見 FinLab 官網

## 本機執行(Windows)

```bash
pip install -r requirements.txt
python -m finlab login          # 第一次:開瀏覽器登入 FinLab,之後同一台電腦自動沿用
python broker_demo.py           # 預設畫 2330
python broker_demo.py --stock 5452 --days 120 --top 10
```

結果在 `output/`:

| 檔案 | 內容 |
|---|---|
| `branch_flow_<代號>.png` | 價格 + 主力淨買超走勢圖 |
| `backtest_equity.png` | 策略 vs 大盤的累積報酬 |
| `backtest_report.html` | FinLab 完整回測報告(用瀏覽器開) |
| `stats.json` | 年化報酬、最大回撤、夏普值、勝率 |

## 在 GitHub 上執行

1. 在自己電腦先登入一次(見上方),再執行:
   ```bash
   python -m finlab token --env
   ```
   會印出 `FINLAB_REFRESH_TOKEN`、`FINLAB_SESSION_ID`、`FINLAB_API_KEY` 三個值。
2. 到 repo 的 **Settings → Secrets and variables → Actions → New repository secret**,三個值各新增一個 secret(名稱照抄)。
3. 到 **Actions → FinLab 券商分點回測 → Run workflow**,可以填股票代號和持股檔數。
4. 跑完後,執行頁面上方的 Summary 會顯示績效表格,圖和完整報告在下方 **Artifacts → finlab-broker-output**。

> 這三個值等同你的 FinLab 登入權限,只能放在 GitHub Secrets,不要貼到程式碼、issue 或聊天訊息裡。

回測結果僅供研究,過去績效不代表未來。
