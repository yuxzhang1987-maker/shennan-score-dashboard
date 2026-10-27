import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import yfinance as yf
import akshare as ak

st.set_page_config(page_title="深南电路每日评分", page_icon="📈", layout="wide")
BASE = Path(__file__).parent
DATA = BASE / "data" / "history.csv"
AI_FILE = BASE / "data" / "ai_signal.txt"

# 权重合计100%。新增主力资金流指标占10%，其余指标按原权重比例缩放。
WEIGHTS = {
    "SOX评分": .315,
    "NDX评分": .135,
    "收益率评分": .180,
    "深南评分": .180,
    "AI资本开支评分": .090,
    "大资金流评分": .100,
}

def clip(x):
    return x.clip(0, 100)

def trend(s):
    ret20 = s.pct_change(20) * 100
    ma = s.rolling(50, min_periods=30).mean()
    gap = (s / ma - 1) * 100
    return .6 * clip((ret20 + 10) / 20 * 100) + .4 * clip((gap + 5) / 10 * 100)

def fetch(ticker):
    d = yf.download(ticker, period="1y", interval="1d", auto_adjust=True,
                    progress=False, threads=False, timeout=20)
    if d is None or d.empty:
        raise RuntimeError(f"{ticker} 没有返回行情")
    s = d["Close"]
    if isinstance(s, pd.DataFrame):
        s = s.iloc[:, 0]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s

def fetch_fund_flow():
    """东方财富个股资金流，返回最近约100个交易日；单位按接口原始数据保存。"""
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
    # 以主力净流入占比评分：-5%及以下为0分，0%为50分，+5%及以上为100分。
    f["大资金流评分"] = ((f["主力净流入占比（%）"] + 5) / 10 * 100).clip(0, 100)
    f = f.dropna(subset=["日期"]).drop_duplicates("日期", keep="last").sort_values("日期")
    cols = ["主力净流入（亿元）", "主力净流入占比（%）", "大资金流评分"]
    out = f.set_index("日期")[cols]
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out

def live():
    tickers = {
        "SOX收盘": "^SOX",
        "NDX收盘": "^NDX",
        "美国10年期收益率": "^TNX",
        "深南电路收盘": "002916.SZ",
    }
    raw = {}
    errors = []
    for k, t in tickers.items():
        try:
            raw[k] = fetch(t)
        except Exception as e:
            errors.append(f"{k} ({t}): {e}")
    if not raw:
        raise RuntimeError("所有行情下载失败，请稍后重试或更换数据源。")

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
        flow = None
        errors.append(f"深南电路大资金流：{e}")

    try:
        ai = float(AI_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        ai = 0
    ai = max(-2, min(2, ai))

    series_list = list(raw.values())
    if flow is not None:
        series_list.extend([flow[c] for c in flow.columns])
    dates = sorted(set().union(*[set(s.index) for s in series_list]))
    df = pd.DataFrame(index=pd.DatetimeIndex(dates))
    for k, s in raw.items():
        df[k] = s
    for k, s in scores.items():
        df[k] = s
    if flow is not None:
        df = df.join(flow, how="left")
    df["AI资本开支评分"] = (ai + 2) / 4 * 100

    cols = [c for c in WEIGHTS if c in df.columns]
    num = sum(df[c] * WEIGHTS[c] for c in cols)
    den = sum(df[c].notna() * WEIGHTS[c] for c in cols)
    df["综合评分"] = num / den.where(den != 0)
    df["数据覆盖率"] = den * 100
    df.index.name = "日期"
    df = df.reset_index()
    df["日期"] = pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    return df, errors

def label(x):
    if pd.isna(x):
        return "数据不足"
    if x < 40:
        return "偏弱｜风险较高"
    if x < 60:
        return "中性｜观察"
    if x < 75:
        return "改善｜等待确认"
    return "偏强｜环境较有利"

st.title("📈 深南电路每日跟踪评分")
st.caption("SOX + 纳斯达克100 + 美国10年期收益率 + 深南电路走势 + AI资本开支 + 主力资金流向")
st.warning("这是规则模型环境评分，不是上涨概率或买卖指令。资金流向为东方财富统计口径，可能与其他平台不同；行情/资金流数据可能延迟、缺失或抓取失败。")

with st.sidebar:
    st.header("控制面板")
    refresh = st.button("🔄 刷新最新行情与资金流", type="primary", use_container_width=True)
    st.caption("权重：SOX 31.5% · NDX 13.5% · 美债收益率 18% · 深南走势 18% · AI资本开支 9% · 主力资金流 10%")
    st.caption("资金流评分：主力净流入占比 -5%映射0分，0%映射50分，+5%及以上映射100分。")

if refresh:
    with st.spinner("正在下载行情与深南电路资金流数据……"):
        try:
            now, errs = live()
            if DATA.exists():
                old = pd.read_csv(DATA)
                combo = pd.concat([old, now], ignore_index=True)
                combo["日期"] = combo["日期"].astype(str)
                # 逐列保留最近的非空值，避免资金流接口只返回近100日时覆盖更早的历史资金流。
                combo = combo.sort_values("日期").groupby("日期", as_index=False).last().sort_values("日期")
            else:
                combo = now
            combo.to_csv(DATA, index=False, encoding="utf-8-sig")
            st.success(f"刷新完成，累计 {len(combo)} 个日期。")
            if errs:
                st.warning("部分指标抓取失败（其他指标仍可能正常）：\n\n" + "\n".join(errs))
        except Exception as e:
            st.error(f"刷新失败：{e}")

if DATA.exists():
    hist = pd.read_csv(DATA)
else:
    hist = pd.DataFrame()

if hist.empty:
    st.info("暂无历史评分。部署后点击左侧“刷新最新行情与资金流”进行首次更新；之后可由 GitHub Actions 工作日自动积累。")
else:
    hist["日期"] = pd.to_datetime(hist["日期"], errors="coerce")
    hist = hist.dropna(subset=["日期"]).sort_values("日期")
    last = hist.iloc[-1]
    score = pd.to_numeric(last.get("综合评分"), errors="coerce")
    cov = pd.to_numeric(last.get("数据覆盖率"), errors="coerce")
    a, b, c = st.columns(3)
    a.metric("最新综合评分", f"{score:.1f}/100" if pd.notna(score) else "暂无")
    b.metric("评分状态", label(score))
    c.metric("数据覆盖率", f"{cov:.0f}%" if pd.notna(cov) else "暂无")
    st.caption("数据日期：" + last["日期"].strftime("%Y-%m-%d"))

    st.subheader("各项指标当日评分")
    metrics = [
        ("费城半导体指数（SOX）", "SOX评分", "31.5%"),
        ("纳斯达克100（NDX）", "NDX评分", "13.5%"),
        ("美国10年期收益率环境", "收益率评分", "18%"),
        ("深南电路自身走势", "深南评分", "18%"),
        ("AI资本开支信号", "AI资本开支评分", "9%"),
        ("大资金流向", "大资金流评分", "10%"),
    ]
    cs = st.columns(3)
    for i, (name, col, w) in enumerate(metrics):
        v = pd.to_numeric(last.get(col), errors="coerce")
        cs[i % 3].metric(name, f"{v:.1f}" if pd.notna(v) else "暂无", help="权重 " + w)

    flow_amt = pd.to_numeric(last.get("主力净流入（亿元）"), errors="coerce")
    flow_pct = pd.to_numeric(last.get("主力净流入占比（%）"), errors="coerce")
    st.subheader("💰 深南电路主力资金流向")
    f1, f2, f3 = st.columns(3)
    f1.metric("当日主力净流入/出", f"{flow_amt:+.2f} 亿元" if pd.notna(flow_amt) else "暂无")
    f2.metric("主力净流入占比", f"{flow_pct:+.2f}%" if pd.notna(flow_pct) else "暂无")
    f3.metric("大资金流评分", f"{pd.to_numeric(last.get('大资金流评分'), errors='coerce'):.1f}/100" if pd.notna(pd.to_numeric(last.get("大资金流评分"), errors="coerce")) else "暂无")
    st.caption("正数表示统计口径下净流入，负数表示净流出。这里的“主力资金”是平台根据成交单分类估算，并不等于能直接识别真实机构账户。")

    if "主力净流入（亿元）" in hist.columns:
        flow_hist = hist[["日期", "主力净流入（亿元）"]].copy()
        flow_hist["主力净流入（亿元）"] = pd.to_numeric(flow_hist["主力净流入（亿元）"], errors="coerce")
        flow_hist = flow_hist.dropna(subset=["主力净流入（亿元）"]).set_index("日期")
        if not flow_hist.empty:
            st.subheader("主力资金净流入/出历史（亿元）")
            st.bar_chart(flow_hist, y="主力净流入（亿元）", height=260)

    st.subheader("综合评分历史走势")
    ch = hist[["日期", "综合评分"]].copy().set_index("日期")
    ch["综合评分"] = pd.to_numeric(ch["综合评分"], errors="coerce")
    st.line_chart(ch, y="综合评分", height=340)

    st.subheader("历史评分记录")
    wanted = [
        "日期", "SOX评分", "NDX评分", "收益率评分", "深南评分",
        "AI资本开支评分", "主力净流入（亿元）", "主力净流入占比（%）",
        "大资金流评分", "综合评分", "数据覆盖率"
    ]
    show = hist[[x for x in wanted if x in hist.columns]].copy()
    for col in show.columns:
        if col != "日期":
            show[col] = pd.to_numeric(show[col], errors="coerce").round(2)
    st.dataframe(show.sort_values("日期", ascending=False), use_container_width=True, hide_index=True)
    st.download_button(
        "下载历史评分 CSV",
        hist.to_csv(index=False).encode("utf-8-sig"),
        "深南电路历史评分.csv",
        "text/csv"
    )
