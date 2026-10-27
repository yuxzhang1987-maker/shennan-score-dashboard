from pathlib import Path
import pandas as pd
import yfinance as yf
import akshare as ak

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "history.csv"
AI_FILE = ROOT / "data" / "ai_signal.txt"
W = {
    "SOX评分": .315,
    "NDX评分": .135,
    "收益率评分": .180,
    "深南评分": .180,
    "AI资本开支评分": .090,
    "大资金流评分": .100,
}

def clip(s):
    return s.clip(0, 100)

def fetch(t):
    d = yf.download(t, period="1y", interval="1d", auto_adjust=True,
                    progress=False, threads=False, timeout=20)
    if d is None or d.empty:
        raise RuntimeError(f"{t} 没有下载到数据")
    s = d["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s

def trend(s):
    r = s.pct_change(20) * 100
    ma = s.rolling(50, min_periods=30).mean()
    gap = (s / ma - 1) * 100
    return .6 * clip((r + 10) / 20 * 100) + .4 * clip((gap + 5) / 10 * 100)

def fetch_fund_flow():
    f = ak.stock_individual_fund_flow(stock="002916", market="sz")
    if f is None or f.empty:
        raise RuntimeError("东方财富没有返回深南电路资金流数据")
    required = ["日期", "主力净流入-净额", "主力净流入-净占比"]
    missing = [c for c in required if c not in f.columns]
    if missing:
        raise RuntimeError("资金流数据缺少字段：" + "、".join(missing))
    f = f.copy()
    f["日期"] = pd.to_datetime(f["日期"], errors="coerce").dt.normalize()
    f["主力净流入（亿元）"] = pd.to_numeric(f["主力净流入-净额"], errors="coerce") / 1e8
    f["主力净流入占比（%）"] = pd.to_numeric(f["主力净流入-净占比"], errors="coerce")
    f["大资金流评分"] = ((f["主力净流入占比（%）"] + 5) / 10 * 100).clip(0, 100)
    f = f.dropna(subset=["日期"]).drop_duplicates("日期", keep="last").sort_values("日期")
    out = f.set_index("日期")[["主力净流入（亿元）", "主力净流入占比（%）", "大资金流评分"]]
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out

def main():
    raw = {}
    errs = []
    for k, t in [
        ("SOX收盘", "^SOX"),
        ("NDX收盘", "^NDX"),
        ("美国10年期收益率", "^TNX"),
        ("深南电路收盘", "002916.SZ"),
    ]:
        try:
            raw[k] = fetch(t)
        except Exception as e:
            print("WARNING", k, t, e)
            errs.append(str(e))
    if not raw:
        raise RuntimeError("全部行情抓取失败")

    scores = {}
    if "SOX收盘" in raw:
        scores["SOX评分"] = trend(raw["SOX收盘"])
    if "NDX收盘" in raw:
        scores["NDX评分"] = trend(raw["NDX收盘"])
    if "美国10年期收益率" in raw:
        scores["收益率评分"] = clip((.5 - raw["美国10年期收益率"].diff(20)) * 100)
    if "深南电路收盘" in raw:
        s = raw["深南电路收盘"]
        r20 = s.pct_change(20) * 100
        r5 = s.pct_change(5) * 100
        ma = s.rolling(50, min_periods=30).mean()
        gap = (s / ma - 1) * 100
        scores["深南评分"] = (
            .5 * clip((r20 + 10) / 20 * 100)
            + .3 * clip((gap + 5) / 10 * 100)
            + .2 * clip((r5 + 5) / 10 * 100)
        )

    try:
        flow = fetch_fund_flow()
    except Exception as e:
        print("WARNING 深南电路大资金流", e)
        errs.append(str(e))
        flow = None

    try:
        ai = float(AI_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        ai = 0
    ai = max(-2, min(2, ai))

    all_series = list(raw.values())
    if flow is not None:
        all_series.extend([flow[c] for c in flow.columns])
    dates = sorted(set().union(*[set(s.index) for s in all_series]))
    df = pd.DataFrame(index=pd.DatetimeIndex(dates))
    for k, s in raw.items():
        df[k] = s
    for k, s in scores.items():
        df[k] = s
    if flow is not None:
        df = df.join(flow, how="left")
    df["AI资本开支评分"] = (ai + 2) / 4 * 100

    cols = [c for c in W if c in df.columns]
    num = sum(df[c] * W[c] for c in cols)
    den = sum(df[c].notna() * W[c] for c in cols)
    df["综合评分"] = num / den.where(den != 0)
    df["数据覆盖率"] = den * 100
    df.index.name = "日期"
    df = df.reset_index()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")

    if DATA.exists():
        old = pd.read_csv(DATA)
        df = pd.concat([old, df], ignore_index=True)
        df["日期"] = df["日期"].astype(str)
        # 逐列保留最近的非空值，避免资金流接口只返回近100日时覆盖更早的历史资金流。
        df = df.sort_values("日期").groupby("日期", as_index=False).last().sort_values("日期")
    DATA.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA, index=False, encoding="utf-8-sig")
    print("Updated rows:", len(df), "latest:", df["日期"].max())
    if errs:
        print("Warnings:", " | ".join(errs))

if __name__ == "__main__":
    main()
