from pathlib import Path
import subprocess
import sys

import pandas as pd
import streamlit as st

st.set_page_config(page_title="深南电路智能监控评分", page_icon="📈", layout="wide")
BASE = Path(__file__).parent
DATA = BASE / "data" / "history.csv"
HOURLY = BASE / "data" / "hourly_history.csv"

st.title("📈 深南电路智能监控评分")
st.caption("五维评分｜逐小时记录｜主力资金流｜阈值提醒｜历史趋势")
st.warning(
    "本工具是规则化市场环境监测，不代表上涨概率，也不是自动买卖指令。"
    "资金流是平台按成交单分类估算，不等于真实机构账户。数据源可能延迟或临时不可用。"
)

with st.sidebar:
    st.header("控制面板")
    if st.button("🔄 立即更新数据", type="primary", use_container_width=True):
        with st.spinner("正在从东方财富/新浪等数据源更新……"):
            try:
                import update_data
                update_data.main()
                st.success("更新任务已执行，请查看下方最新数据时间和异常信息。")
                st.rerun()
            except Exception as exc:
                st.error(f"更新失败：{exc}")
    st.markdown("**评分权重**")
    st.caption("深南走势 25% · PCB板块 20% · 大资金流 20% · 美股科技 15% · 基本面代理 20%")
    st.markdown("**评分参考区间**")
    st.caption("0–39 偏弱｜40–59 观察｜60–69 初步改善｜70–79 偏强｜80–89 强势｜90–100 很强")
    st.caption("GitHub Actions 每个工作日北京时间约09:05–15:05每小时运行一次；定时任务可能延迟。")

hourly = pd.read_csv(HOURLY) if HOURLY.exists() else pd.DataFrame()
daily = pd.read_csv(DATA) if DATA.exists() else pd.DataFrame()

if hourly.empty:
    st.info("尚无升级后的逐小时记录。请点击左侧“立即更新数据”，或等待 GitHub Actions 首次运行。原有日线历史数据不会被删除。")
else:
    hourly["更新时间"] = pd.to_datetime(hourly["更新时间"], errors="coerce")
    hourly = hourly.dropna(subset=["更新时间"]).sort_values("更新时间")
    latest = hourly.iloc[-1]
    score = pd.to_numeric(latest.get("综合评分_v2"), errors="coerce")
    coverage = pd.to_numeric(latest.get("数据覆盖率_v2"), errors="coerce")

    def fmt_num(value, suffix="", digits=1):
        x = pd.to_numeric(value, errors="coerce")
        return f"{x:.{digits}f}{suffix}" if pd.notna(x) else "暂无"

    if pd.isna(score):
        status = "数据不足"
    elif score < 40:
        status = "偏弱｜风险较高"
    elif score < 60:
        status = "中性｜观察"
    elif score < 70:
        status = "初步改善｜等待确认"
    elif score < 80:
        status = "偏强｜趋势改善"
    elif score < 90:
        status = "强势｜关注持续性"
    else:
        status = "很强｜仍需防范回撤"

    c1, c2, c3 = st.columns(3)
    c1.metric("最新综合评分", fmt_num(score, "/100"))
    c2.metric("评分状态", status)
    c3.metric("数据覆盖率", fmt_num(coverage, "%", 0))
    st.caption(f"本次记录时间：{latest['更新时间']}｜行情时间：{latest.get('行情数据时间', '未知')}｜数据源：{latest.get('数据源', '未知')}")

    error_text = str(latest.get("数据异常", ""))
    if error_text and error_text.lower() != "nan":
        st.warning("部分数据源本次不可用，相关指标可能缺失：" + error_text)
    if pd.notna(coverage) and coverage < 80:
        st.error("本次有效指标权重覆盖率低于80%，请不要单独依据综合评分作决策。")

    st.subheader("五项指标评分")
    metrics = [
        ("深南电路自身走势", "深南走势评分", "25%"),
        ("PCB板块走势", "PCB板块评分", "20%"),
        ("大资金流向", "大资金流评分", "20%"),
        ("美股科技趋势（纳斯达克100）", "美股科技评分", "15%"),
        ("基本面/AI资本开支人工代理", "基本面评分", "20%"),
    ]
    cols = st.columns(5)
    for i, (label, key, weight) in enumerate(metrics):
        cols[i].metric(label, fmt_num(latest.get(key)), help=f"权重 {weight}")

    st.subheader("💰 深南电路主力资金流")
    f1, f2, f3 = st.columns(3)
    f1.metric("主力净流入/出", fmt_num(latest.get("主力净流入（亿元）"), "亿元", 2))
    f2.metric("主力净流入占比", fmt_num(latest.get("主力净流入占比（%）"), "%", 2))
    f3.metric("资金流数据日期", str(latest.get("资金流数据日期", "暂无")))
    st.caption("正数表示该平台统计口径下净流入，负数表示净流出。不同平台的主力资金定义可能不同。")

    st.subheader("逐小时综合评分曲线")
    chart = hourly[["更新时间", "综合评分_v2"]].copy().set_index("更新时间")
    chart["综合评分_v2"] = pd.to_numeric(chart["综合评分_v2"], errors="coerce")
    st.line_chart(chart, height=300)

    st.subheader("逐小时大资金流记录")
    flow_chart = hourly[["更新时间", "主力净流入（亿元）"]].copy().set_index("更新时间")
    flow_chart["主力净流入（亿元）"] = pd.to_numeric(flow_chart["主力净流入（亿元）"], errors="coerce")
    st.bar_chart(flow_chart, height=240)

    st.subheader("最近逐小时评分记录")
    wanted = ["更新时间", "综合评分_v2", "数据覆盖率_v2", "深南走势评分", "PCB板块评分", "大资金流评分", "美股科技评分", "基本面评分", "主力净流入（亿元）", "主力净流入占比（%）", "数据异常"]
    show = hourly[[c for c in wanted if c in hourly.columns]].tail(200).copy().sort_values("更新时间", ascending=False)
    for col in show.columns:
        if col not in ("更新时间", "数据异常"):
            show[col] = pd.to_numeric(show[col], errors="coerce").round(2)
    st.dataframe(show, use_container_width=True, hide_index=True)
    st.download_button("下载逐小时历史 CSV", hourly.to_csv(index=False).encode("utf-8-sig"), "深南电路逐小时评分.csv", "text/csv")

if not daily.empty:
    st.subheader("日度历史数据")
    if "综合评分_v2" in daily.columns:
        daily["日期"] = pd.to_datetime(daily["日期"], errors="coerce")
        dchart = daily.dropna(subset=["日期"])[["日期", "综合评分_v2"]].copy().set_index("日期")
        dchart["综合评分_v2"] = pd.to_numeric(dchart["综合评分_v2"], errors="coerce")
        if not dchart.empty:
            st.line_chart(dchart, height=260)
    st.download_button("下载全部日度历史 CSV", daily.to_csv(index=False).encode("utf-8-sig"), "深南电路日度历史.csv", "text/csv")

st.divider()
st.subheader("微信提醒配置")
st.markdown(
    "在 GitHub 仓库 **Settings → Secrets and variables → Actions → New repository secret** 中，"
    "新增名称 `PUSHPLUS_TOKEN`，值填写你自己的 PushPlus Token。不要把 Token 写入代码或公开提交。"
)
st.caption("达到60/70/80/90分时按阈值首次触发推送；评分回落到60分以下后重新解锁下一轮提醒。未配置 Token 时网站照常评分，但不会发送微信提醒。")
st.subheader("基本面指标说明")
st.markdown(
    "当前基本面项使用 `data/ai_signal.txt` 的人工信号作为代理：-2对应0分、0对应50分、+2对应100分。"
    "它不是自动读取财报得出的基本面评分。若没有人工输入，则按中性50分处理，并在记录中注明。"
)
