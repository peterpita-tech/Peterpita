"""Minervini《超級績效》的趨勢模板與 VCP(波動收縮型態),台股、美股共用。

VCP 判定(在第 t 天收盤後,只用 t 以前的資料):
1. 底部起點 = 近 65 週最高價那天;底部長度 3~65 週
2. 從起點往右切收縮:高點 H1 → 之後最低 L1 → 之後最高 H2 → 之後最低 L2 …
   每段幅度 (H − L) ÷ H,小於 2% 的當雜訊,不算一次收縮
3. 收縮 2~4 次,每次約前一次的一半(後 ÷ 前 介於 0.25~0.75)
4. 總深度(第一次收縮)10~35%
5. 最後一次收縮(從最後高點到今天)的平均量 < 50 日均量
樞紐點 = 最後一次收縮的高點;隔天收盤突破樞紐點、且成交量 > 50 日均量 × 1.5 才算買點。
"""
import numpy as np
import pandas as pd

MIN_BASE, MAX_BASE = 15, 325          # 底部 3~65 週(交易日)
MIN_CONTRACTIONS, MAX_CONTRACTIONS = 2, 4
DEPTH_RANGE = (0.10, 0.35)            # 總深度
RATIO_RANGE = (0.25, 0.75)            # 每次收縮 ÷ 前一次(約一半)
NOISE = 0.02                          # 小於 2% 的回檔不算收縮
BREAKOUT_VOL = 1.5                    # 突破日量 > 50 日均量 × 1.5


def rs_rating(adj, liquid):
    """IBD 式相對強度:近 3/6/9/12 月報酬加權,換算成全市場百分位(0~99)。"""
    raw = 0.4 * adj.pct_change(63) + 0.2 * adj.pct_change(126) + 0.2 * adj.pct_change(189) + 0.2 * adj.pct_change(252)
    return (raw.where(liquid).rank(axis=1, pct=True) * 99).round()


def trend_template(adj, high, low, rs):
    """Minervini 趨勢模板(第二階段上升趨勢)8 條件。"""
    ma50, ma150, ma200 = adj.rolling(50).mean(), adj.rolling(150).mean(), adj.rolling(200).mean()
    hi52, lo52 = high.rolling(252).max(), low.rolling(252).min()
    return ((adj > ma150) & (adj > ma200)                  # 1. 股價在 150、200 日均線之上
            & (ma150 > ma200)                              # 2. 150 日均線 > 200 日均線
            & (ma200 > ma200.shift(21))                    # 3. 200 日均線至少上升 1 個月
            & (ma50 > ma150) & (ma50 > ma200)              # 4. 50 日均線在 150、200 之上
            & (adj > ma50)                                 # 5. 股價在 50 日均線之上
            & (adj >= lo52 * 1.30)                         # 6. 比 52 週低點高 30% 以上
            & (adj >= hi52 * 0.75)                         # 7. 距 52 週高點 25% 以內
            & (rs >= 70))                                  # 8. 相對強度 ≥ 70


def contractions(h, l, t):
    """回傳第 t 天的 (底部起點, [(高點位置, 幅度), ...]);底部太短或資料有缺回傳 None。"""
    s0 = max(0, t - MAX_BASE + 1)
    seg = h[s0:t + 1]
    if np.isnan(seg).any() or np.isnan(l[s0:t + 1]).any():
        return None
    pos = s0 + int(np.argmax(seg))
    if t - pos < MIN_BASE:
        return None
    start, out = pos, []
    while pos < t:
        j = pos + 1 + int(np.argmin(l[pos + 1:t + 1]))
        d = 1 - l[j] / h[pos]
        if d < NOISE:
            break
        out.append((pos, d))
        if len(out) > MAX_CONTRACTIONS or j >= t:
            break
        pos = j + 1 + int(np.argmax(h[j + 1:t + 1]))
    return start, out


def detect(high, low, volume, candidates):
    """在 candidates 為 True 的日子判定 VCP。

    回傳 dict:setup(bool)、pivot(樞紐點)、vol50、depth(總深度)、last(最後收縮幅度)、
    desc(最後一天的收縮描述,Series)。
    """
    vol50 = volume.rolling(50).mean()
    shape = high.shape
    setup = np.zeros(shape, bool)
    pivot, depth, last = (np.full(shape, np.nan) for _ in range(3))
    H, L, V, V50 = (x.to_numpy(float) for x in (high, low, volume, vol50))
    C = candidates.reindex_like(high).fillna(False).to_numpy(bool)
    desc = {}
    for k in range(shape[1]):
        h, l, v, v50 = H[:, k], L[:, k], V[:, k], V50[:, k]
        for t in np.flatnonzero(C[:, k]):
            r = contractions(h, l, t)
            if r is None:
                continue
            start, cs = r
            if not MIN_CONTRACTIONS <= len(cs) <= MAX_CONTRACTIONS:
                continue
            ds = [d for _, d in cs]
            if not DEPTH_RANGE[0] <= ds[0] <= DEPTH_RANGE[1]:
                continue
            if not all(RATIO_RANGE[0] <= b / a <= RATIO_RANGE[1] for a, b in zip(ds, ds[1:])):
                continue
            p = cs[-1][0]
            if not np.nanmean(v[p:t + 1]) < v50[t]:          # 最後收縮量縮
                continue
            setup[t, k], pivot[t, k], depth[t, k], last[t, k] = True, h[p], ds[0], ds[-1]
            if t == shape[0] - 1:
                desc[high.columns[k]] = (f"{len(ds)} 次:" + "→".join(f"{d * 100:.0f}%" for d in ds)
                                         + f" · 底部 {(t - start) / 5:.0f} 週")
    frame = lambda a: pd.DataFrame(a, high.index, high.columns)  # noqa: E731
    return {"setup": frame(setup), "pivot": frame(pivot), "vol50": vol50, "depth": frame(depth),
            "last": frame(last), "desc": pd.Series(desc, dtype=object)}


def breakout(adj, volume, v):
    """前一天已形成 VCP,今天收盤突破樞紐點且放量。"""
    return (v["setup"].shift(1).fillna(False).astype(bool)
            & (adj > v["pivot"].shift(1)) & (volume > BREAKOUT_VOL * v["vol50"].shift(1)))
