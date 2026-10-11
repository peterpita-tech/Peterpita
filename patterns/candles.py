"""K 線型態偵測(依 Steve Nison《陰線陽線》,型態與頁碼取自「型態總整理」)。

輸入為寬表 DataFrame(日期 × 股票)的 O/H/L/C/V,用還原權值價格(除權息不會被誤判成缺口)。
每個偵測函式回傳布林寬表:True = 型態在當天「完成」。

通用定義(書中是文字描述,這裡量化成可計算的門檻):
- 實體 = |收 − 開|;上影線 = 高 − max(開, 收);下影線 = min(開, 收) − 低
- 長線形:實體 ≥ 近 10 日平均實體 × 1.3,且實體 ≥ 當日振幅一半
- 小實體(紡錘):實體 ≤ 近 10 日平均實體 × 0.5
- 十字線:實體 ≤ 振幅 × 10%(書中:開收盤差 1–2 檔仍可視為十字線)
- 趨勢(原則 1「先有趨勢才有反轉」):型態第一根之前一天,
  上升 = 近 10 日漲幅 > 5% 且收在 20 日均線上;下降 = 近 10 日跌幅 > 5% 且收在 20 日均線下
"""
import numpy as np
import pandas as pd


class K:
    """把 O/H/L/C/V 包起來,提供常用的衍生量與位移。"""

    def __init__(self, o, h, l, c, v):
        self.o, self.h, self.l, self.c, self.v = o, h, l, c, v
        self.top, self.bot = np.maximum(o, c), np.minimum(o, c)
        self.body = (c - o).abs()
        self.rng = (h - l)
        self.upper, self.lower = h - self.top, self.bot - l
        self.white, self.black = c > o, c < o
        self.avg_body = self.body.rolling(10).mean().shift(1)
        self.long = (self.body >= self.avg_body * 1.3) & (self.body >= self.rng * 0.5)
        self.small = self.body <= self.avg_body * 0.5
        self.doji = (self.body <= self.rng * 0.1) & (self.rng > 0)
        self.mid = (o + c) / 2                                 # 實體中點
        ma20 = c.rolling(20).mean()
        ret10 = c / c.shift(10) - 1
        self._up = (ret10 > 0.05) & (c > ma20)
        self._down = (ret10 < -0.05) & (c < ma20)
        self.vol_ratio = v / v.rolling(20).mean().shift(1)

    def up(self, n):
        """n 根型態:第一根前一天處於上升趨勢。"""
        return self._up.shift(n).fillna(False).astype(bool)

    def down(self, n):
        return self._down.shift(n).fillna(False).astype(bool)


def s(df, n):
    """位移 n 天;布林表補 False(避免空值無法取反)。"""
    if (df.dtypes == bool).all():
        return df.shift(n, fill_value=False)
    return df.shift(n)


# ---------------------------------------------------------------- 單根
def hammer(k):          # 鎚子 p.048:跌勢中、小實體在上端、下影線 ≥ 實體 2 倍、幾乎無上影線
    return k.down(1) & (k.lower >= 2 * k.body) & (k.upper <= k.rng * 0.1) & (k.body > 0) & ~k.doji


def hanging_man(k):     # 吊人 p.052:與鎚子同形,發生在漲勢(需隔天確認)
    return k.up(1) & (k.lower >= 2 * k.body) & (k.upper <= k.rng * 0.1) & (k.body > 0) & ~k.doji


def shooting_star(k):   # 流星 p.086:漲勢中、小實體在下端、長上影線、幾乎無下影線
    return k.up(1) & (k.upper >= 2 * k.body) & (k.lower <= k.rng * 0.1) & (k.body > 0) & ~k.doji


def inverted_hammer(k):  # 倒狀鎚子 p.088:與流星同形,發生在跌勢(需隔天確認)
    return k.down(1) & (k.upper >= 2 * k.body) & (k.lower <= k.rng * 0.1) & (k.body > 0) & ~k.doji


def bull_belt_hold(k):  # 多頭執帶 p.103:跌勢中,開在最低價的長陽線,收在高點附近
    return k.down(1) & k.white & k.long & (k.lower <= k.rng * 0.02) & (k.upper <= k.body * 0.15)


def bear_belt_hold(k):  # 空頭執帶 p.103:漲勢中,開在最高價的長陰線
    return k.up(1) & k.black & k.long & (k.upper <= k.rng * 0.02) & (k.lower <= k.body * 0.15)


def dragonfly(k):       # 蜻蜓十字 p.167:開=收=最高,長下影線(跌勢中偏多)
    return k.down(1) & k.doji & (k.upper <= k.rng * 0.1) & (k.lower >= k.rng * 0.7)


def gravestone(k):      # 墓碑十字 p.167:開=收=最低,長上影線(漲勢中偏空)
    return k.up(1) & k.doji & (k.lower <= k.rng * 0.1) & (k.upper >= k.rng * 0.7)


def northern_doji(k):   # 北方十字 p.164:漲勢中、長陽線之後的十字線
    return k.up(2) & s(k.long, 1) & s(k.white, 1) & k.doji


def long_legged_doji(k):  # 長腳十字 p.167:上下影線都很長的十字線(振幅 ≥ 近 10 日平均 1.5 倍)
    avg_rng = k.rng.rolling(10).mean().shift(1)
    return k.doji & (k.rng >= avg_rng * 1.5) & (k.upper >= k.rng * 0.3) & (k.lower >= k.rng * 0.3)


# ---------------------------------------------------------------- 兩根
def bull_engulfing(k):  # 多頭吞噬 p.056:跌勢中,陽線實體包覆前一支陰線實體
    return (k.down(2) & s(k.black, 1) & k.white
            & (k.o <= s(k.c, 1)) & (k.c >= s(k.o, 1)) & (k.body > s(k.body, 1)))


def bear_engulfing(k):  # 空頭吞噬 p.056:漲勢中,陰線實體包覆前一支陽線實體
    return (k.up(2) & s(k.white, 1) & k.black
            & (k.o >= s(k.c, 1)) & (k.c <= s(k.o, 1)) & (k.body > s(k.body, 1)))


def piercing(k):        # 貫穿 p.068:跌勢中長陰線後,開低於前低、收盤穿入前實體上半部
    return (k.down(2) & s(k.black, 1) & s(k.long, 1) & k.white
            & (k.o < s(k.l, 1)) & (k.c > s(k.mid, 1)) & (k.c < s(k.o, 1)))


def dark_cloud(k):      # 烏雲罩頂 p.063:漲勢中長陽線後,開高於前高、收盤深入前實體過半
    return (k.up(2) & s(k.white, 1) & s(k.long, 1) & k.black
            & (k.o > s(k.h, 1)) & (k.c < s(k.mid, 1)) & (k.c > s(k.o, 1)))


def bull_harami(k):     # 多頭母子 p.092:跌勢中長陰線後,小實體完全在前實體內
    return (k.down(2) & s(k.long, 1) & s(k.black, 1) & k.small & ~k.doji
            & (k.top < s(k.top, 1)) & (k.bot > s(k.bot, 1)))


def bear_harami(k):     # 空頭母子 p.092:漲勢中長陽線後,小實體完全在前實體內
    return (k.up(2) & s(k.long, 1) & s(k.white, 1) & k.small & ~k.doji
            & (k.top < s(k.top, 1)) & (k.bot > s(k.bot, 1)))


def bull_harami_cross(k):  # 母子十字 p.093(底部):長陰線後,十字線在前實體內
    return (k.down(2) & s(k.long, 1) & s(k.black, 1) & k.doji
            & (k.top < s(k.top, 1)) & (k.bot > s(k.bot, 1)))


def bear_harami_cross(k):  # 母子十字 p.093(頂部,較可靠)
    return (k.up(2) & s(k.long, 1) & s(k.white, 1) & k.doji
            & (k.top < s(k.top, 1)) & (k.bot > s(k.bot, 1)))


def bull_counterattack(k):  # 多頭反攻線 p.118:長陰線後大幅開低,收盤回到前日收盤
    return (k.down(2) & s(k.long, 1) & s(k.black, 1) & k.white & k.long
            & (k.o < s(k.l, 1)) & ((k.c - s(k.c, 1)).abs() <= s(k.body, 1) * 0.1))


def bear_counterattack(k):  # 空頭反攻線 p.119:長陽線後大幅開高,收盤回到前日收盤
    return (k.up(2) & s(k.long, 1) & s(k.white, 1) & k.black & k.long
            & (k.o > s(k.h, 1)) & ((k.c - s(k.c, 1)).abs() <= s(k.body, 1) * 0.1))


def tweezers_bottom(k):  # 鑷底 p.097:跌勢中兩根最低價相同,第 2 根為陽線(搭配長線接小線)
    return (k.down(2) & ((k.l - s(k.l, 1)).abs() <= s(k.c, 1) * 0.001) & k.white
            & s(k.long, 1) & s(k.black, 1))


def tweezers_top(k):    # 鑷頂 p.097:漲勢中兩根最高價相同,第 2 根為陰線
    return (k.up(2) & ((k.h - s(k.h, 1)).abs() <= s(k.c, 1) * 0.001) & k.black
            & s(k.long, 1) & s(k.white, 1))


def rising_window(k):   # 上升窗口 p.134:今天最低價高於昨天最高價(影線不重疊)
    return k.l > s(k.h, 1)


def falling_window(k):  # 下降窗口 p.134:今天最高價低於昨天最低價
    return k.h < s(k.l, 1)


# ---------------------------------------------------------------- 三根以上
def _star_gap_down(k):  # 第 2 根實體與第 1 根實體之間向下跳空
    return s(k.top, 1) < s(k.bot, 2)


def _star_gap_up(k):
    return s(k.bot, 1) > s(k.top, 2)


def morning_star(k):    # 晨星 p.074:長陰線 → 向下跳空小實體 → 陽線收盤深入第 1 根實體
    return (k.down(3) & s(k.long, 2) & s(k.black, 2) & s(k.small, 1) & ~s(k.doji, 1)
            & _star_gap_down(k) & k.white & (k.c > s(k.mid, 2)))


def morning_doji_star(k):  # 十字晨星 p.082:晨星的中間為十字線
    return (k.down(3) & s(k.long, 2) & s(k.black, 2) & s(k.doji, 1)
            & _star_gap_down(k) & k.white & (k.c > s(k.mid, 2)))


def evening_star(k):    # 夜星 p.078:長陽線 → 向上跳空小實體 → 陰線收盤深入第 1 根實體
    return (k.up(3) & s(k.long, 2) & s(k.white, 2) & s(k.small, 1) & ~s(k.doji, 1)
            & _star_gap_up(k) & k.black & (k.c < s(k.mid, 2)))


def evening_doji_star(k):  # 十字夜星 p.082
    return (k.up(3) & s(k.long, 2) & s(k.white, 2) & s(k.doji, 1)
            & _star_gap_up(k) & k.black & (k.c < s(k.mid, 2)))


def three_white_soldiers(k):  # 前進三白兵 p.108:低檔連三根陽線,收盤遞增、開在前實體內、收在高點附近
    ok = k.down(3) | (k.c.shift(3) < k.c.rolling(60).mean().shift(3))      # 低價區或跌勢之後
    cond = ok
    for i in (0, 1, 2):
        cond = cond & s(k.white, i) & (s(k.upper, i) <= s(k.body, i) * 0.3) & (s(k.body, i) >= s(k.avg_body, i) * 0.8)
    for i in (0, 1):
        cond = cond & (s(k.c, i) > s(k.c, i + 1)) & (s(k.o, i) >= s(k.bot, i + 1)) & (s(k.o, i) <= s(k.top, i + 1))
    return cond


def three_black_crows(k):  # 三烏鴉 p.106:高檔連三根陰線,收盤遞減、開在前實體內、收在低點附近
    cond = k.up(3)
    for i in (0, 1, 2):
        cond = cond & s(k.black, i) & (s(k.lower, i) <= s(k.body, i) * 0.3) & (s(k.body, i) >= s(k.avg_body, i) * 0.8)
    for i in (0, 1):
        cond = cond & (s(k.c, i) < s(k.c, i + 1)) & (s(k.o, i) >= s(k.bot, i + 1)) & (s(k.o, i) <= s(k.top, i + 1))
    return cond


def upside_gap_two_crows(k):  # 雙鴉躍空 p.104:長陽 → 向上跳空小陰 → 開更高收更低、吞噬前陰
    return (k.up(3) & s(k.long, 2) & s(k.white, 2) & s(k.black, 1) & (s(k.bot, 1) > s(k.top, 2))
            & k.black & (k.o > s(k.o, 1)) & (k.c < s(k.c, 1)) & (k.c > s(k.c, 2)))


def rising_three(k):    # 上升三法 p.148:長陽 → 3 根小線形在其區間內回檔 → 長陽收在第 1 根收盤之上
    cond = s(k.long, 4) & s(k.white, 4) & k.long & k.white & (k.c > s(k.c, 4))
    for i in (1, 2, 3):
        cond = cond & (s(k.h, i) <= s(k.h, 4)) & (s(k.l, i) >= s(k.l, 4)) & (s(k.body, i) < s(k.body, 4) * 0.6)
    return cond & (s(k.c, 1) < s(k.c, 3))                                  # 中間小幅回檔


def falling_three(k):   # 下降三法 p.148
    cond = s(k.long, 4) & s(k.black, 4) & k.long & k.black & (k.c < s(k.c, 4))
    for i in (1, 2, 3):
        cond = cond & (s(k.h, i) <= s(k.h, 4)) & (s(k.l, i) >= s(k.l, 4)) & (s(k.body, i) < s(k.body, 4) * 0.6)
    return cond & (s(k.c, 1) > s(k.c, 3))


# ---------------------------------------------------------------- 型態清單
# side:bull / bear / neutral;kind:reversal / star / continuation / doji
# stop:型態極端價(原則 4)— "low" = 型態最低價為支撐/停損,"high" = 型態最高價為壓力/停損
P = [
    # id, 中文名, fn, 根數, 多空, 類別, 頁碼, 需確認, 停損參考, 說明
    ("hammer", "鎚子", hammer, 1, "bull", "reversal", "048", False, "low", "跌勢中,小實體在上端、下影線 ≥ 實體 2 倍;下影線低點是支撐"),
    ("inverted_hammer", "倒狀鎚子", inverted_hammer, 1, "bull", "star", "088", True, "low", "跌勢末端的長上影線;隔天開盤(最好收盤)高於其實體才算確認"),
    ("bull_engulfing", "多頭吞噬", bull_engulfing, 2, "bull", "reversal", "056", False, "low", "陽線實體包覆前一支陰線實體;兩支整體低點是支撐"),
    ("piercing", "貫穿", piercing, 2, "bull", "reversal", "068", False, "low", "長陰後開低於前低,收盤穿入前實體上半部"),
    ("morning_star", "晨星", morning_star, 3, "bull", "star", "074", False, "low", "長陰 → 向下跳空小實體 → 陽線深入第 1 根實體;型態最低點是支撐"),
    ("morning_doji_star", "十字晨星", morning_doji_star, 3, "bull", "star", "082", False, "low", "晨星的中間為十字線,可能是重要底部"),
    ("bull_harami", "多頭母子", bull_harami, 2, "bull", "reversal", "092", False, "low", "長陰後小實體完全在其實體內;常使跌勢轉為橫盤"),
    ("bull_harami_cross", "母子十字(底)", bull_harami_cross, 2, "bull", "reversal", "093", False, "low", "母子的第 2 根為十字線,意涵強於一般母子"),
    ("bull_counterattack", "多頭反攻線", bull_counterattack, 2, "bull", "reversal", "118", False, "low", "長陰後大幅開低,收盤回到前日收盤;弱於貫穿"),
    ("bull_belt_hold", "多頭執帶", bull_belt_hold, 1, "bull", "reversal", "103", False, "low", "低價區開在最低價的長陽線;跌破執帶低點則跌勢恢復"),
    ("tweezers_bottom", "鑷底", tweezers_bottom, 2, "bull", "reversal", "097", False, "low", "兩根最低價相同,長陰接陽線"),
    ("dragonfly", "蜻蜓十字", dragonfly, 1, "bull", "doji", "167", False, "low", "開=收=最高、長下影線;形似鎚子但沒有實體"),
    ("three_white_soldiers", "前進三白兵", three_white_soldiers, 3, "bull", "reversal", "108", False, "low", "低檔連三根收盤遞增的陽線;完成後常修正,第 1、2 根提供支撐"),
    ("hanging_man", "吊人", hanging_man, 1, "bear", "reversal", "052", True, "high", "與鎚子同形但在漲勢中;須確認:隔天收盤跌破吊人實體"),
    ("shooting_star", "流星", shooting_star, 1, "bear", "star", "086", False, "high", "漲勢中小實體在下端、長上影線;強度不如空頭吞噬或夜星"),
    ("bear_engulfing", "空頭吞噬", bear_engulfing, 2, "bear", "reversal", "056", False, "high", "陰線實體包覆前一支陽線實體;兩支整體高點是壓力"),
    ("dark_cloud", "烏雲罩頂", dark_cloud, 2, "bear", "reversal", "063", False, "high", "長陽後開高於前高、收盤深入前實體過半;型態高點是壓力與停損"),
    ("evening_star", "夜星", evening_star, 3, "bear", "star", "078", False, "high", "長陽 → 向上跳空小實體 → 長陰顯著穿入第 1 根;型態高點是壓力"),
    ("evening_doji_star", "十字夜星", evening_doji_star, 3, "bear", "star", "082", False, "high", "夜星的中間為十字線"),
    ("bear_harami", "空頭母子", bear_harami, 2, "bear", "reversal", "092", False, "high", "長陽後小實體完全在其實體內"),
    ("bear_harami_cross", "母子十字(頂)", bear_harami_cross, 2, "bear", "reversal", "093", False, "high", "又稱癱瘓型態;頂部較底部可靠"),
    ("bear_counterattack", "空頭反攻線", bear_counterattack, 2, "bear", "reversal", "119", False, "high", "長陽後大幅開高,收盤回到前日收盤;弱於烏雲罩頂"),
    ("bear_belt_hold", "空頭執帶", bear_belt_hold, 1, "bear", "reversal", "103", False, "high", "高價區開在最高價的長陰線"),
    ("tweezers_top", "鑷頂", tweezers_top, 2, "bear", "reversal", "097", False, "high", "兩根最高價相同,長陽接陰線"),
    ("upside_gap_two_crows", "雙鴉躍空", upside_gap_two_crows, 3, "bear", "reversal", "104", False, "high", "長陽 → 跳空小陰 → 開更高收更低吞噬前陰;罕見"),
    ("three_black_crows", "三烏鴉", three_black_crows, 3, "bear", "reversal", "106", False, "high", "高檔連三根收盤遞減的陰線;訊號較晚,適合長線"),
    ("gravestone", "墓碑十字", gravestone, 1, "bear", "doji", "167", False, "high", "開=收=最低、長上影線;流星的終極形式"),
    ("northern_doji", "北方十字", northern_doji, 2, "bear", "doji", "164", False, "high", "漲勢中長陽後的十字線;收盤站上十字線高點才算突破"),
    ("rising_window", "上升窗口", rising_window, 2, "bull", "continuation", "134", False, "window", "影線不重疊的向上缺口;整個窗口是支撐,收盤跌破下緣才失效"),
    ("falling_window", "下降窗口", falling_window, 2, "bear", "continuation", "134", False, "window", "影線不重疊的向下缺口;整個窗口是壓力"),
    ("rising_three", "上升三法", rising_three, 5, "bull", "continuation", "148", False, "low", "長陽 → 小線形在其區間內回檔 → 長陽創新高;類似旗形"),
    ("falling_three", "下降三法", falling_three, 5, "bear", "continuation", "148", False, "high", "長陰 → 小線形在其區間內反彈 → 長陰創新低"),
    ("long_legged_doji", "長腳十字", long_legged_doji, 1, "neutral", "doji", "167", False, None, "上下影線都很長的十字線;市場失去方向感"),
]


def detect(o, h, l, c, v):
    """回傳 (K, {型態 id: 布林寬表})。"""
    k = K(o, h, l, c, v)
    return k, {p[0]: p[2](k).fillna(False).astype(bool) for p in P}


def level(k, kind, bars, when, code):
    """型態的支撐 / 壓力(原則 4:型態極端價即停損點)。"""
    i = k.c.index.get_loc(when)
    span = slice(i - bars + 1, i + 1)
    if kind == "low":
        return float(k.l[code].iloc[span].min())
    if kind == "high":
        return float(k.h[code].iloc[span].max())
    if kind == "window":                                     # 窗口:上升取下緣(前高)、下降取上緣(前低)
        return float(k.h[code].iloc[i - 1]) if k.l[code].iloc[i] > k.h[code].iloc[i - 1] else float(k.l[code].iloc[i - 1])
    return None
