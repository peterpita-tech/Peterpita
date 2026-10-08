"""FinLab 券商分點買賣超:走勢圖 + 選股回測示範

資料表(FinLab broker_transactions 衍生的日 × 股票矩陣):
    etl:broker_transactions:top15_buy   前 15 大買超分點的買超量合計
    etl:broker_transactions:top15_sell  前 15 大賣超分點的賣超量合計
  → 主力淨買超 = top15_buy − top15_sell

用法:
    pip install -r requirements.txt
    python -m finlab login          # 第一次:開瀏覽器登入 FinLab,之後自動沿用
    python broker_demo.py           # 預設畫 2330
    python broker_demo.py --stock 5452 --days 120

無瀏覽器環境(GitHub Actions)改用環境變數
FINLAB_REFRESH_TOKEN / FINLAB_SESSION_ID / FINLAB_API_KEY(本機執行 `python -m finlab token --env` 取得)。

輸出到 output/:
    branch_flow_<代號>.png   價格 + 主力淨買超走勢
    backtest_equity.png      策略 vs 大盤 權益曲線
    backtest_report.html     FinLab 完整回測報告
    stats.json               主要績效數字
"""
import argparse
import json
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from finlab import data  # noqa: E402
from finlab.backtest import sim  # noqa: E402

OUT = Path(__file__).resolve().parent / "output"
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK TC", "Noto Sans CJK JP", "Microsoft JhengHei",
                                   "PingFang TC", "Heiti TC", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
UP, DOWN = "#e5484d", "#30a46c"   # 台股慣例:紅買綠賣


def load():
    buy = data.get("etl:broker_transactions:top15_buy")
    sell = data.get("etl:broker_transactions:top15_sell")
    close = data.get("price:收盤價")
    amount = data.get("price:成交金額")
    print(f"[data] 分點資料 {buy.index.min():%Y-%m-%d} ~ {buy.index.max():%Y-%m-%d}, "
          f"{buy.shape[1]} 檔;股價到 {close.index.max():%Y-%m-%d}")
    return buy, sell, close, amount


def plot_stock(sid, days, buy, sell, close):
    """上:收盤價;下:每日主力淨買超(柱)+ 20 日累計(線)。"""
    net = (buy - sell)[sid].dropna()
    px = close[sid].dropna()
    idx = net.index.intersection(px.index)[-days:]
    net, px, cum20 = net.loc[idx], px.loc[idx], net.rolling(20).sum().reindex(idx)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1.4]})
    a1.plot(px.index, px.values, color="#2b6cb0", lw=1.6)
    a1.set_title(f"{sid} 收盤價 vs 前 15 大分點淨買超(近 {len(idx)} 個交易日)", fontsize=13)
    a1.set_ylabel("收盤價")
    a1.grid(alpha=.25)
    a2.bar(net.index, net.values, color=[UP if v >= 0 else DOWN for v in net.values], width=1.0, alpha=.8)
    a2.axhline(0, color="#888", lw=.8)
    a2.set_ylabel("每日淨買超")
    a2.grid(alpha=.25)
    b2 = a2.twinx()
    b2.plot(cum20.index, cum20.values, color="#805ad5", lw=1.6, label="20 日累計")
    b2.set_ylabel("20 日累計", color="#805ad5")
    b2.legend(loc="upper left")
    fig.tight_layout()
    path = OUT / f"branch_flow_{sid}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"[chart] {path}")


def backtest(buy, sell, close, amount, top_n):
    """選股:近 20 日「前 15 大分點淨買超 ÷ 前 15 大分點總進出」最高的 N 檔(主力買盤最一面倒),
    限 20 日均成交額 > 5,000 萬、收盤站上季線;每週換股,隔日開盤價成交。"""
    net20 = (buy - sell).rolling(20).sum()
    gross20 = (buy + sell).rolling(20).sum()
    factor = net20 / gross20.where(gross20 > 0)          # 介於 -1 ~ 1,與單位無關
    cond = (amount.average(20) > 5e7) & (close > close.average(60))
    position = factor.where(cond).is_largest(top_n)

    report = sim(position, resample="W", trade_at_price="open",
                 name=f"前15大分點淨買超 Top{top_n}", upload=False)
    stats = report.get_stats()
    keep = ["startDate", "endDate", "cagr", "max_drawdown", "daily_sharpe", "win_ratio", "avg_n_stock"]
    summary = {k: (round(float(v), 4) if isinstance(v, (int, float)) else str(v))
               for k, v in stats.items() if k in keep}
    bstats = getattr(report, "get_benchmark_stats", lambda: {})()
    if bstats:
        summary["benchmark_cagr"] = round(float(bstats.get("cagr", float("nan"))), 4)
        summary["benchmark_max_drawdown"] = round(float(bstats.get("max_drawdown", float("nan"))), 4)
    print("[backtest]", json.dumps(summary, ensure_ascii=False))

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(report.creturn.index, report.creturn.values, color=UP, lw=1.6, label="策略")
    bench = getattr(report, "benchmark", None)
    if bench is not None and len(bench):
        bench = bench / bench.iloc[0]
        ax.plot(bench.index, bench.values, color="#888", lw=1.2, label="大盤(報酬指數)")
    ax.set_title(f"前 15 大分點淨買超 Top{top_n} · 每週換股", fontsize=13)
    ax.set_ylabel("累積報酬(倍)")
    ax.grid(alpha=.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "backtest_equity.png", dpi=130)
    plt.close(fig)
    report.to_html(str(OUT / "backtest_report.html"), title=f"前15大分點淨買超 Top{top_n}")
    (OUT / "stats.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def write_summary(sid, summary):
    """GitHub Actions 的執行摘要頁。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    pct = lambda k: f"{summary[k] * 100:.1f}%" if isinstance(summary.get(k), float) else "—"  # noqa: E731
    lines = [f"## 券商分點買賣超回測(示範 {sid})", "",
             "| 指標 | 策略 | 大盤 |", "|---|---|---|",
             f"| 年化報酬 | {pct('cagr')} | {pct('benchmark_cagr')} |",
             f"| 最大回撤 | {pct('max_drawdown')} | {pct('benchmark_max_drawdown')} |",
             f"| 日夏普 | {summary.get('daily_sharpe', '—')} | |",
             f"| 勝率 | {pct('win_ratio')} | |",
             f"| 期間 | {summary.get('startDate', '—')[:10]} ~ {summary.get('endDate', '—')[:10]} | |", "",
             "圖表與完整報告在本次執行的 Artifacts(finlab-broker-output)。"]
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description="FinLab 券商分點買賣超示範")
    ap.add_argument("--stock", default="2330", help="畫走勢圖的股票代號")
    ap.add_argument("--days", type=int, default=250, help="走勢圖顯示天數")
    ap.add_argument("--top", type=int, default=20, help="回測每期持股檔數")
    a = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    buy, sell, close, amount = load()
    plot_stock(a.stock, a.days, buy, sell, close)
    summary = backtest(buy, sell, close, amount, a.top)
    write_summary(a.stock, summary)


if __name__ == "__main__":
    pd.set_option("display.width", 160)
    main()
