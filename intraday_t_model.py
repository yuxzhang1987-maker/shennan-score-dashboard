from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import math
import requests
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
BARS_FILE = DATA_DIR / "t_intraday_5m.csv"
SIGNALS_FILE = DATA_DIR / "t_signal_history.csv"
SYMBOL = "0.002916"


def fetch_5m_bars(limit: int = 3000) -> tuple[pd.DataFrame, str]:
    """Fetch 5-minute adjusted bars from Eastmoney; fail closed on empty/stale data."""
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": SYMBOL, "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "5", "fqt": "1", "beg": (datetime.now()-timedelta(days=120)).strftime("%Y%m%d"),
        "end": datetime.now().strftime("%Y%m%d"), "lmt": str(limit),
    }
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    rows = (r.json().get("data") or {}).get("klines") or []
    if not rows:
        raise RuntimeError("东方财富5分钟K线返回空数据")
    cols = ["时间","开盘","收盘","最高","最低","成交量","成交额","振幅","涨跌幅","涨跌额","换手率"]
    df = pd.DataFrame([str(x).split(",")[:11] for x in rows], columns=cols)
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    for c in ["开盘","收盘","最高","最低","成交量","成交额"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["时间","开盘","收盘","最高","最低"]).sort_values("时间")
    if df.empty:
        raise RuntimeError("5分钟K线清洗后没有有效记录")
    latest = pd.Timestamp(df["时间"].max())
    if (pd.Timestamp.now() - latest).total_seconds() > 5*24*3600:
        raise RuntimeError(f"5分钟行情过旧，最新时间：{latest}")
    return df, "东方财富5分钟K线"


def update_history() -> tuple[pd.DataFrame, str]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fresh, source = fetch_5m_bars()
    if BARS_FILE.exists():
        try:
            old = pd.read_csv(BARS_FILE)
            allbars = pd.concat([old, fresh], ignore_index=True)
        except Exception:
            allbars = fresh
    else:
        allbars = fresh
    allbars["时间"] = pd.to_datetime(allbars["时间"], errors="coerce")
    allbars = allbars.dropna(subset=["时间"]).drop_duplicates("时间", keep="last").sort_values("时间")
    allbars.to_csv(BARS_FILE, index=False, encoding="utf-8-sig")
    return allbars, source


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100/(1+rs)


def calculate(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    for c in ["开盘","收盘","最高","最低","成交量","成交额"]:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df = df.dropna(subset=["时间","收盘","最高","最低"]).sort_values("时间").reset_index(drop=True)
    if df.empty:
        return df
    df["日期"] = df["时间"].dt.date.astype(str)
    df["EMA9"] = df["收盘"].ewm(span=9, adjust=False).mean()
    df["EMA21"] = df["收盘"].ewm(span=21, adjust=False).mean()
    df["RSI14"] = _rsi(df["收盘"])
    mid = df["收盘"].rolling(20, min_periods=15).mean()
    sd = df["收盘"].rolling(20, min_periods=15).std()
    df["布林中轨"] = mid
    df["布林上轨"] = mid + 2*sd
    df["布林下轨"] = mid - 2*sd
    typical = (df["最高"]+df["最低"]+df["收盘"])/3
    vol = df["成交量"].fillna(0).clip(lower=0)
    df["日内VWAP"] = (typical*vol).groupby(df["日期"]).cumsum() / vol.groupby(df["日期"]).cumsum().replace(0,np.nan)
    df["量比"] = vol / vol.rolling(20, min_periods=10).mean().replace(0,np.nan)
    df["ATR14"] = (df["最高"]-df["最低"]).rolling(14,min_periods=10).mean()
    # Regime filter: use EMA structure and 5-bar slope; avoid mean-reversion buys in persistent downtrends.
    df["趋势状态"] = np.select(
        [(df["EMA9"]>df["EMA21"]) & (df["EMA21"].diff(5)>0),
         (df["EMA9"]<df["EMA21"]) & (df["EMA21"].diff(5)<0)],
        ["偏多趋势","偏空趋势"], default="震荡/不明")
    # Strategy A: mean-reversion only when not in a clear downtrend; require a rebound candle.
    rebound = (df["收盘"]>df["开盘"]) & (df["收盘"]>df["收盘"].shift(1))
    mean_buy = (df["趋势状态"]!="偏空趋势") & (df["RSI14"]<35) & (df["收盘"]<df["布林下轨"]*1.003) & rebound
    mean_sell_raw = (df["RSI14"]>68) & ((df["收盘"]>df["布林上轨"]*0.995) | (df["收盘"]<df["收盘"].shift(1)))
    # Strategy B: trend continuation; avoid chasing an extended move far above VWAP.
    prior_high = df["最高"].rolling(12,min_periods=12).max().shift(1)
    trend_buy = (df["趋势状态"]=="偏多趋势") & (df["收盘"]>prior_high) & (df["量比"]>1.15) & (df["收盘"]<df["日内VWAP"]*1.012)
    trend_sell_raw = ((df["EMA9"]<df["EMA21"]) & (df["EMA9"].shift(1)>=df["EMA21"].shift(1))) | ((df["RSI14"]>75) & (df["收盘"]>df["布林上轨"]))
    buy_raw = mean_buy | trend_buy
    sell_raw = mean_sell_raw | trend_sell_raw
    # Emit only the first bar of a signal episode; this prevents repeated alerts on every bar.
    df["买入信号"] = buy_raw & ~buy_raw.shift(1, fill_value=False)
    df["卖出信号"] = sell_raw & ~sell_raw.shift(1, fill_value=False)
    df["震荡买分"] = np.where(mean_buy, 1, 0)
    df["趋势买分"] = np.where(trend_buy, 1, 0)
    df["卖出分"] = np.where(df["卖出信号"], 1, 0)
    df["策略类型"] = np.select([df["买入信号"] & mean_buy,df["买入信号"] & trend_buy],["震荡回归","趋势跟随"],default="-")
    return df


def backtest_next_session(df: pd.DataFrame, cost_bps: float = 25.0) -> dict:
    """Signal audit: enter at next bar close, exit at next trading session's last close.
    This is deliberately a conservative T+1-compatible directional test, not a full account simulator.
    """
    if df.empty or "买入信号" not in df:
        return {"samples":0,"win_rate":None,"avg_return":None,"profit_factor":None,"note":"尚无有效行情"}
    d = df.copy()
    d["日期"] = pd.to_datetime(d["时间"]).dt.date
    dates = list(pd.Series(d["日期"].unique()).sort_values())
    date_last = {day: d.index[d["日期"]==day][-1] for day in dates}
    date_next = {dates[i]:dates[i+1] for i in range(len(dates)-1)}
    trades=[]
    for i in d.index[d["买入信号"].fillna(False)]:
        if i+1 >= len(d):
            continue
        day = d.loc[i+1,"日期"]
        if day not in date_next:
            continue
        exit_day = date_next[day]
        exit_idx = date_last[exit_day]
        entry = float(d.loc[i+1,"收盘"])
        exitp = float(d.loc[exit_idx,"收盘"])
        if entry<=0: continue
        ret = (exitp/entry-1)*100 - cost_bps/100
        trades.append(ret)
    if not trades:
        return {"samples":0,"win_rate":None,"avg_return":None,"profit_factor":None,"note":"符合条件的完整T+1样本不足"}
    a=np.array(trades,dtype=float)
    gains=a[a>0].sum(); losses=abs(a[a<0].sum())
    return {"samples":len(a),"win_rate":float((a>0).mean()*100),"avg_return":float(a.mean()),
            "profit_factor":float(gains/losses) if losses>0 else (float("inf") if gains>0 else None),
            "note":"按下一根5分钟K线收盘模拟进场、下一交易日收盘退出；已扣单边合计成本假设25bp。不是卖底仓回补的完整账户级做T回测。"}


def current_signal(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"action":"数据不足","reason":"没有可用5分钟行情"}
    x=df.iloc[-1]
    price=float(x["收盘"])
    buy=bool(x.get("买入信号",False))
    sell=bool(x.get("卖出信号",False))
    rsi=x.get("RSI14",np.nan); vwap=x.get("日内VWAP",np.nan); atr=x.get("ATR14",np.nan)
    if buy and not sell:
        action="关注买入"
        reason=f"{x.get('策略类型')}信号；趋势状态={x.get('趋势状态')}，RSI={rsi:.1f}，量比={x.get('量比',np.nan):.2f}。等待下一根K线确认，不追价。"
    elif sell:
        action="关注卖出"
        reason=f"出现止盈/趋势转弱条件；趋势状态={x.get('趋势状态')}，RSI={rsi:.1f}。仅对已有可卖底仓有效。"
    else:
        action="观望"
        reason=f"当前没有达到稳健型入场阈值；趋势状态={x.get('趋势状态')}，RSI={rsi:.1f}。不为交易而交易。"
    return {"action":action,"price":price,"vwap":float(vwap) if pd.notna(vwap) else None,
            "atr":float(atr) if pd.notna(atr) else None,"rsi":float(rsi) if pd.notna(rsi) else None,
            "trend":str(x.get("趋势状态","未知")),"strategy":str(x.get("策略类型","-")),
            "time":str(x.get("时间")),"reason":reason}


def run_update() -> tuple[pd.DataFrame,dict,dict,str]:
    bars, source = update_history()
    scored = calculate(bars)
    sig = current_signal(scored)
    bt = backtest_next_session(scored)
    if not scored.empty:
        row = scored.iloc[-1]
        record = {"信号时间":str(row["时间"]),"收盘":row["收盘"],"趋势状态":row["趋势状态"],
                  "RSI14":row.get("RSI14"),"日内VWAP":row.get("日内VWAP"),"量比":row.get("量比"),
                  "买入信号":bool(row.get("买入信号")),"卖出信号":bool(row.get("卖出信号")),"策略类型":row.get("策略类型")}
        hist = pd.read_csv(SIGNALS_FILE) if SIGNALS_FILE.exists() else pd.DataFrame()
        hist = pd.concat([hist,pd.DataFrame([record])],ignore_index=True).drop_duplicates("信号时间",keep="last").tail(5000)
        hist.to_csv(SIGNALS_FILE,index=False,encoding="utf-8-sig")
    return scored,sig,bt,source


def backtest_sell_first_same_day(df: pd.DataFrame, target_pct: float = 0.45, cost_bps: float = 25.0) -> dict:
    """Historical sell-existing-shares then buy-back-lower test within the same session.
    Uses next bar open as sell fill and first later low touching the target as buyback fill.
    It assumes sufficient old, sellable inventory and does not model partial fills/queue position.
    """
    if df.empty or "卖出信号" not in df:
        return {"signals":0,"completed":0,"completion_rate":None,"avg_net_spread":None,"win_rate":None}
    d=df.copy().reset_index(drop=True)
    d["日期"]=pd.to_datetime(d["时间"]).dt.date
    results=[]; signals=0
    for i in d.index[d["卖出信号"].fillna(False)]:
        if i+1>=len(d): continue
        day=d.loc[i+1,"日期"]
        sell_price=pd.to_numeric(d.loc[i+1,"开盘"],errors="coerce")
        if pd.isna(sell_price) or sell_price<=0: continue
        signals+=1
        target=float(sell_price)*(1-target_pct/100)
        later=d[(d.index>i+1)&(d["日期"]==day)]
        hit=later[later["最低"]<=target]
        if hit.empty: continue
        # Conservative: target limit fill, not the day's low.
        net=(float(sell_price)-target)/float(sell_price)*100-cost_bps/100
        results.append(net)
    if not results:
        return {"signals":signals,"completed":0,"completion_rate":0.0 if signals else None,"avg_net_spread":None,"win_rate":None,
                "note":"未观察到同日触及预设回补价的完整样本；未把未回补的股票风险收益计入。"}
    a=np.asarray(results,dtype=float)
    return {"signals":signals,"completed":len(a),"completion_rate":len(a)/signals*100 if signals else None,
            "avg_net_spread":float(a.mean()),"win_rate":float((a>0).mean()*100),
            "note":f"假设卖出后下跌{target_pct:.2f}%挂单回补；使用下一根K线开盘卖出、后续首次触及目标价回补，扣除往返成本{cost_bps:.0f}bp。未计未回补股票的机会成本、盘口排队和部分成交。"}
