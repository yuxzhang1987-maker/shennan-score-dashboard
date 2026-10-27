import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import yfinance as yf

st.set_page_config(page_title="深南电路每日评分", page_icon="📈", layout="wide")
BASE=Path(__file__).parent
DATA=BASE/"data"/"history.csv"
AI_FILE=BASE/"data"/"ai_signal.txt"
WEIGHTS={"SOX评分":.35,"NDX评分":.15,"收益率评分":.20,"深南评分":.20,"AI资本开支评分":.10}

def clip(x): return x.clip(0,100)
def trend(s):
    ret20=s.pct_change(20)*100
    ma=s.rolling(50,min_periods=30).mean()
    gap=(s/ma-1)*100
    return .6*clip((ret20+10)/20*100)+.4*clip((gap+5)/10*100)
def fetch(ticker):
    d=yf.download(ticker,period="1y",interval="1d",auto_adjust=True,progress=False,threads=False,timeout=20)
    if d is None or d.empty: raise RuntimeError(f"{ticker} 没有返回行情")
    s=d["Close"]
    if isinstance(s,pd.DataFrame): s=s.iloc[:,0]
    return pd.to_numeric(s,errors="coerce").dropna()
def live():
    tickers={"SOX收盘":"^SOX","NDX收盘":"^NDX","美国10年期收益率":"^TNX","深南电路收盘":"002916.SZ"}
    raw={}; errors=[]
    for k,t in tickers.items():
        try: raw[k]=fetch(t)
        except Exception as e: errors.append(f"{k} ({t}): {e}")
    if not raw: raise RuntimeError("所有行情下载失败，请稍后重试或更换数据源。")
    scores={}
    if "SOX收盘" in raw: scores["SOX评分"]=trend(raw["SOX收盘"])
    if "NDX收盘" in raw: scores["NDX评分"]=trend(raw["NDX收盘"])
    if "美国10年期收益率" in raw: scores["收益率评分"]=clip((.5-raw["美国10年期收益率"].diff(20))*100)
    if "深南电路收盘" in raw:
        s=raw["深南电路收盘"]; r20=s.pct_change(20)*100; r5=s.pct_change(5)*100
        ma=s.rolling(50,min_periods=30).mean(); gap=(s/ma-1)*100
        scores["深南评分"]=.5*clip((r20+10)/20*100)+.3*clip((gap+5)/10*100)+.2*clip((r5+5)/10*100)
    try: ai=float(AI_FILE.read_text(encoding="utf-8").strip())
    except Exception: ai=0
    ai=max(-2,min(2,ai))
    dates=sorted(set().union(*[set(s.index) for s in raw.values()]))
    df=pd.DataFrame(index=pd.DatetimeIndex(dates))
    for k,s in raw.items(): df[k]=s
    for k,s in scores.items(): df[k]=s
    df["AI资本开支评分"]=(ai+2)/4*100
    cols=[c for c in WEIGHTS if c in df.columns]
    num=sum(df[c]*WEIGHTS[c] for c in cols)
    den=sum(df[c].notna()*WEIGHTS[c] for c in cols)
    df["综合评分"]=num/den.where(den!=0); df["数据覆盖率"]=den*100
    df.index.name="日期"; df=df.reset_index()
    df["日期"]=pd.to_datetime(df["日期"]).dt.strftime("%Y-%m-%d")
    return df,errors

def label(x):
    if pd.isna(x): return "数据不足"
    if x<40: return "偏弱｜风险较高"
    if x<60: return "中性｜观察"
    if x<75: return "改善｜等待确认"
    return "偏强｜环境较有利"

st.title("📈 深南电路每日跟踪评分")
st.caption("SOX + 纳斯达克100 + 美国10年期收益率 + 深南电路走势 + AI资本开支信号")
st.warning("规则模型环境评分，不是上涨概率或买卖指令。行情源可能延迟、缺失或下载失败。")
with st.sidebar:
    st.header("控制面板")
    refresh=st.button("🔄 刷新最新行情",type="primary",use_container_width=True)
    st.caption("权重：SOX 35% · NDX 15% · 美债收益率 20% · 深南电路 20% · AI资本开支 10%")
if refresh:
    with st.spinner("正在下载行情并计算评分……"):
        try:
            now,errs=live()
            if DATA.exists():
                old=pd.read_csv(DATA)
                combo=pd.concat([old,now],ignore_index=True)
                combo["日期"]=combo["日期"].astype(str)
                combo=combo.drop_duplicates("日期",keep="last").sort_values("日期")
            else: combo=now
            combo.to_csv(DATA,index=False,encoding="utf-8-sig")
            st.success(f"刷新完成，累计 {len(combo)} 个日期。")
            if errs: st.warning("部分指标抓取失败：\n\n"+"\n".join(errs))
        except Exception as e: st.error(f"刷新失败：{e}")
if DATA.exists():
    hist=pd.read_csv(DATA)
else: hist=pd.DataFrame()
if hist.empty:
    st.info("暂无历史评分。部署后点击左侧“刷新最新行情”进行首次更新；之后可由 GitHub Actions 工作日自动积累。")
else:
    hist["日期"]=pd.to_datetime(hist["日期"],errors="coerce")
    hist=hist.dropna(subset=["日期"]).sort_values("日期")
    last=hist.iloc[-1]
    score=pd.to_numeric(last.get("综合评分"),errors="coerce")
    cov=pd.to_numeric(last.get("数据覆盖率"),errors="coerce")
    a,b,c=st.columns(3)
    a.metric("最新综合评分",f"{score:.1f}/100" if pd.notna(score) else "暂无")
    b.metric("评分状态",label(score))
    c.metric("数据覆盖率",f"{cov:.0f}%" if pd.notna(cov) else "暂无")
    st.caption("数据日期："+last["日期"].strftime("%Y-%m-%d"))
    st.subheader("各项指标当日评分")
    metrics=[("费城半导体指数（SOX）","SOX评分","35%"),("纳斯达克100（NDX）","NDX评分","15%"),("美国10年期收益率环境","收益率评分","20%"),("深南电路自身走势","深南评分","20%"),("AI资本开支信号","AI资本开支评分","10%")]
    cs=st.columns(5)
    for i,(name,col,w) in enumerate(metrics):
        v=pd.to_numeric(last.get(col),errors="coerce")
        cs[i].metric(name,f"{v:.1f}" if pd.notna(v) else "暂无",help="权重 "+w)
    st.subheader("综合评分历史走势")
    ch=hist[["日期","综合评分"]].copy().set_index("日期")
    ch["综合评分"]=pd.to_numeric(ch["综合评分"],errors="coerce")
    st.line_chart(ch,y="综合评分",height=340)
    st.subheader("历史评分记录")
    wanted=["日期","SOX评分","NDX评分","收益率评分","深南评分","AI资本开支评分","综合评分","数据覆盖率"]
    show=hist[[x for x in wanted if x in hist.columns]].copy()
    for col in show.columns:
        if col!="日期": show[col]=pd.to_numeric(show[col],errors="coerce").round(1)
    st.dataframe(show.sort_values("日期",ascending=False),use_container_width=True,hide_index=True)
    st.download_button("下载历史评分 CSV",hist.to_csv(index=False).encode("utf-8-sig"),"深南电路历史评分.csv","text/csv")
