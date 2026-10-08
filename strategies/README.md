# 策略選股 · 台股(FinLab)

線上版(每個交易日 20:40 自動更新):https://peterpita-tech.github.io/Peterpita/strategies/

用 FinLab 的資料和回測引擎跑以下策略,顯示每個策略的回測績效、權益曲線與**最新選股**:

| 策略 | 換股 | 選股 |
|---|---|---|
| 三頻率 RSI | 每週檢查 | RSI120>55、RSI60<75、RSI20 三日漲 >2%、RSI20>75 連 3 天、ROE>0;持有 60 天或跌破季線出場 |
| 低波動 30 | 每月 | 日均成交 >100 張中,60 日波動率最低 30 檔 |
| 風險調整報酬 前 15 | 每季 | ROE>5%、營收年增>0、3/6 月報酬為正,6 月報酬 ÷ 120 日波動 最高 15 檔 |
| 本益成長比 PEG | 每月營收公布 | 營收 3/12 月均 >1.1、月增 >−10%,PEG 最低 10 檔,停損 10% |
| 高波動 30(對照組) | 每月 | 60 日波動率最高 30 檔,用來對照低波動異象 |

「最新選股」是最新資料算出的訊號。月換股、季換股的策略會在下一個換股日才真的調整持股。

## 第一次設定(需要 FinLab 帳號)

1. 在自己電腦登入 FinLab 一次,然後匯出給 GitHub 用的憑證:
   ```bash
   pip install -r requirements.txt
   python -m finlab login
   python -m finlab token --env
   ```
2. 把印出的 `FINLAB_REFRESH_TOKEN`、`FINLAB_SESSION_ID`、`FINLAB_API_KEY` 三個值,存到 repo 的
   **Settings → Secrets and variables → Actions → New repository secret**(名稱照抄)。
3. 到 **Actions → 籌碼結構掃描(美股 + 台股)+ 台股重大訊息 + 策略選股 → Run workflow** 手動跑一次,網站就會出現策略選股頁。

沒有設定 secrets 時,這一步會自動略過,不影響其他頁面。

> 三個值等同你的 FinLab 登入權限,只能放在 GitHub Secrets,不要放進程式碼或貼給別人。

**會員等級**:免費會員的台股資料只到 2023-12-31,選股會停在那天(網頁會顯示黃色提醒);要每天最新的選股需要 FinLab VIP。

## 本機執行

```bash
pip install -r requirements.txt
python -m finlab login
python fetch_strategies.py      # → 策略選股.html(資料內嵌,直接雙擊開)
```

## 新增策略

在 `fetch_strategies.py` 寫一個回傳 `(position, sim 參數, 顯示指標)` 的函式,再加進 `STRATEGIES` 清單即可。

回測績效為歷史模擬,不代表未來報酬。
