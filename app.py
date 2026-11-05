from pathlib import Path
import subprocess
import sys

import pandas as pd
import streamlit as st

st.set_page_config(page_title="深南电路智能监控评分", page_icon="📈", layout="wide")
BASE = Path(__file__).parent
DATA = BASE / "data" / "history.csv"
HOURLY = BASE / "data" / "hourly_history.csv"
ALERT_HISTORY = BASE / "data" / "alert_history.csv"


def fmt_num(value, suffix="", digits=1):
    x = pd.to_numeric(value, errors="coerce")
    return f"{x:.{digits}f}{suffix}" if pd.notna(x) else "暂无"

st.title("📈 深南电路智能监控评分")
st.caption("近期/中期双趋势评分｜逐小时记录｜主力资金流｜阈值提醒｜历史趋势")
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
    st.caption("深南近期10% + 中期15% · PCB近期8% + 中期12% · 大资金流20% · 美股科技组合15% · 基本面代理20%")
    st.markdown("**评分参考区间**")
    st.caption("0–39 偏弱｜40–59 观察｜60–69 初步改善｜70–79 偏强｜80–89 强势｜90–100 很强")
    st.caption("GitHub Actions 每个工作日北京时间约09:05–15:05每小时运行一次；页面记录时间统一显示北京时间。深南日线/小时线优先东方财富，备用新浪/腾讯；PCB优先东方财富PCB概念指数，备用PCB成分股等权代理。")

hourly = pd.read_csv(HOURLY) if HOURLY.exists() else pd.DataFrame()
daily = pd.read_csv(DATA) if DATA.exists() else pd.DataFrame()

if hourly.empty:
    st.info("尚无升级后的逐小时记录。请点击左侧“立即更新数据”，或等待 GitHub Actions 首次运行。原有日线历史数据不会被删除。")
else:
    # 旧版本时间戳由GitHub服务器按UTC写入；新版本明确写入北京时间。
    hourly["更新时间"] = pd.to_datetime(hourly["更新时间"], errors="coerce")
    if "更新时间时区" not in hourly.columns:
        hourly["更新时间时区"] = ""
    legacy = hourly["更新时间时区"].fillna("").astype(str).eq("")
    hourly.loc[legacy, "更新时间"] = hourly.loc[legacy, "更新时间"] + pd.Timedelta(hours=8)
    hourly = hourly.dropna(subset=["更新时间"]).sort_values("更新时间")
    latest = hourly.iloc[-1]
    score = pd.to_numeric(latest.get("综合评分_v2"), errors="coerce")
    coverage = pd.to_numeric(latest.get("数据覆盖率_v2"), errors="coerce")

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
    st.caption(f"本次记录时间：{latest['更新时间']}｜深南行情时间：{latest.get('行情数据时间', '未知')}")
    src1, src2 = st.columns(2)
    src1.caption(f"深南电路数据源：{latest.get('深南数据源', '旧记录/未知')}")
    src2.caption(f"PCB板块数据源：{latest.get('PCB数据源', '旧记录/未知')}｜数据日期：{latest.get('PCB数据日期', '未知')}")
    st.caption(f"其他数据源：{latest.get('数据源', '未知')}")

    error_text = str(latest.get("数据异常", ""))
    if error_text and error_text.lower() != "nan":
        st.warning("部分数据源本次不可用，相关指标可能缺失：" + error_text)
    if pd.notna(coverage) and coverage < 80:
        st.error("本次有效指标权重覆盖率低于80%，请不要单独依据综合评分作决策。")

    st.subheader("走势双评分")
    st.caption("近期分数观察1—5个交易日的强弱；中期分数观察20—60个交易日的趋势。两者独立计算，避免单日涨停被中期弱势完全掩盖。")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("深南近期走势", fmt_num(latest.get("深南近期走势评分")), help="当日25%、近3日20%、近5日20%、量能20%、5日均线位置15%")
    c2.metric("深南中期走势", fmt_num(latest.get("深南中期走势评分")), help="近20/60日收益、20/50日均线位置及均线方向")
    c3.metric("PCB近期走势", fmt_num(latest.get("PCB近期走势评分")))
    c4.metric("PCB中期走势", fmt_num(latest.get("PCB中期走势评分")))

    st.subheader("其他三项指标评分")
    metrics = [
        ("大资金流向", "大资金流评分", "20%"),
        ("美股科技趋势（SOX/纳指/龙头组合）", "美股科技评分", "15%"),
        ("基本面/人工代理", "基本面评分", "20%"),
    ]
    cols = st.columns(3)
    for i, (label, key, weight) in enumerate(metrics):
        cols[i].metric(label, fmt_num(latest.get(key)), help=f"权重 {weight}")

    st.subheader("🇺🇸 美国半导体与科技环境评分")
    st.caption("美股环境项总权重仍为原综合评分的15%，内部按 SOX 40%、纳指100 25%、英伟达15%、AMD 10%、博通10% 加权；缺失标的不以0分代替。")
    us_cols = st.columns(5)
    us_items = [
        ("费城半导体 SOX", "SOX评分"),
        ("纳斯达克100", "NDX评分"),
        ("英伟达 NVDA", "NVDA评分"),
        ("AMD", "AMD评分"),
        ("博通 AVGO", "AVGO评分"),
    ]
    for i, (label, key) in enumerate(us_items):
        us_cols[i].metric(label, fmt_num(latest.get(key)))
    st.caption(
        f"美股数据日期：{latest.get('美股数据日期', '未知')}｜"
        f"美股组合数据覆盖率：{fmt_num(latest.get('美股数据覆盖率'), '%', 0)}"
    )

    st.subheader("💰 深南电路主力资金流")
    f1, f2, f3 = st.columns(3)
    f1.metric("主力净流入/出", fmt_num(latest.get("主力净流入（亿元）"), "亿元", 2))
    f2.metric("主力净流入占比", fmt_num(latest.get("主力净流入占比（%）"), "%", 2))
    f3.metric("资金流数据日期", str(latest.get("资金流数据日期", "暂无")))
    st.caption("正数表示该平台统计口径下净流入，负数表示净流出。不同平台的主力资金定义可能不同。")

    st.subheader("逐小时综合评分曲线")
    chart_cols = [c for c in ["综合评分_v2", "深南近期走势评分", "深南中期走势评分", "PCB近期走势评分", "PCB中期走势评分"] if c in hourly.columns]
    chart = hourly[["更新时间"] + chart_cols].copy().set_index("更新时间")
    for col in chart_cols:
        chart[col] = pd.to_numeric(chart[col], errors="coerce")
    st.line_chart(chart, height=320)

    st.subheader("逐小时大资金流记录")
    flow_chart = hourly[["更新时间", "主力净流入（亿元）"]].copy().set_index("更新时间")
    flow_chart["主力净流入（亿元）"] = pd.to_numeric(flow_chart["主力净流入（亿元）"], errors="coerce")
    st.bar_chart(flow_chart, height=240)

    st.subheader("最近逐小时评分记录")
    wanted = ["更新时间", "综合评分_v2", "数据覆盖率_v2", "深南近期走势评分", "深南中期走势评分", "PCB近期走势评分", "PCB中期走势评分", "大资金流评分", "美股科技评分", "基本面评分", "主力净流入（亿元）", "主力净流入占比（%）", "数据异常"]
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
        dcols = [c for c in ["综合评分_v2", "深南近期走势评分", "深南中期走势评分", "PCB近期走势评分", "PCB中期走势评分"] if c in daily.columns]
        dchart = daily.dropna(subset=["日期"])[["日期"] + dcols].copy().set_index("日期")
        for col in dcols:
            dchart[col] = pd.to_numeric(dchart[col], errors="coerce")
        if not dchart.empty:
            st.line_chart(dchart, height=260)
    st.download_button("下载全部日度历史 CSV", daily.to_csv(index=False).encode("utf-8-sig"), "深南电路日度历史.csv", "text/csv")

st.divider()
st.subheader("🔔 评分阈值提醒记录")
st.caption("无需申请 Token，也无需配置 GitHub Secrets。综合评分首次达到60、70、80或90分时，会自动写入下面的提醒记录；评分回落到60分以下后，下一轮突破会重新记录。提醒显示在本网站内，不会主动发送微信消息。")
if ALERT_HISTORY.exists():
    try:
        alerts = pd.read_csv(ALERT_HISTORY)
        if not alerts.empty:
            st.dataframe(alerts.tail(50).sort_values("触发时间", ascending=False), use_container_width=True, hide_index=True)
            st.download_button("下载阈值提醒记录 CSV", alerts.to_csv(index=False).encode("utf-8-sig"), "深南电路阈值提醒.csv", "text/csv")
        else:
            st.info("目前还没有触发过阈值提醒。")
    except Exception as exc:
        st.warning(f"提醒记录暂时无法读取：{exc}")
else:
    st.info("目前还没有触发过阈值提醒。达到60/70/80/90分时，这里会自动留下记录。")

st.subheader("走势双评分说明")
st.markdown("近期走势侧重1—5个交易日，包含成交量配合；中期走势侧重20—60个交易日及均线方向。PCB的近期/中期分数基于当前可用PCB指数或成分股等权代理价格序列计算；当前版本尚未将涨停家数、上涨家数作为独立评分项。旧历史记录没有双评分字段时留空，不追溯伪造。")

st.subheader("基本面指标说明")
st.markdown(
    "当前基本面项使用 `data/ai_signal.txt` 的人工信号作为代理：-2对应0分、0对应50分、+2对应100分。"
    "它不是自动读取财报得出的基本面评分。若没有人工输入，则按中性50分处理，并在记录中注明。"
)



st.divider()
st.title("🧮 深南电路独立量化因子模型")
st.caption("六因子独立评分与历史检验；不改变上方原综合评分及其权重。")
st.warning(
    "量化模型是研究参考，不是买卖指令或收益保证。基本面因子沿用人工信号；"
    "没有录入时不计入有效覆盖率。未来收益只用于事后统计，不参与同日评分。"
)

try:
    import quant_factor_model as qfm
    qfile = qfm.HISTORY_FILE
    qleft, qright = st.columns([1, 3])
    with qleft:
        if st.button("🔄 更新量化因子历史", type="primary", use_container_width=True):
            with st.spinner("正在计算量化因子历史……"):
                qfm.calculate_and_save()
            st.success("量化因子历史已更新。")
            st.rerun()
    if qfile.exists():
        qdf = pd.read_csv(qfile)
        if not qdf.empty:
            qdf["日期"] = pd.to_datetime(qdf["日期"], errors="coerce")
            qdf = qdf.dropna(subset=["日期"]).sort_values("日期")
            latest_q = qdf.iloc[-1]
            a, b, c = st.columns(3)
            a.metric("最新量化因子总分", fmt_num(latest_q.get("量化因子总分")))
            b.metric("数据覆盖率", fmt_num(latest_q.get("数据覆盖率"), "%", 0))
            c.metric("数据日期", latest_q["日期"].strftime("%Y-%m-%d"))
            factor_rows = []
            for factor, weight in qfm.WEIGHTS.items():
                value = pd.to_numeric(latest_q.get(factor), errors="coerce")
                factor_rows.append({
                    "因子": factor,
                    "得分": round(float(value), 1) if pd.notna(value) else "暂无",
                    "权重": f"{weight:.0f}%",
                })
            st.subheader("六大因子得分")
            st.dataframe(pd.DataFrame(factor_rows), use_container_width=True, hide_index=True)
            st.subheader("量化因子历史曲线")
            qchart = qdf[["日期", "历史可回测因子分"]].copy().set_index("日期")
            qchart["历史可回测因子分"] = pd.to_numeric(qchart["历史可回测因子分"], errors="coerce")
            st.line_chart(qchart, height=280)
            st.caption("历史曲线基于前五项可逐日重建的市场因子；当前人工基本面只用于最新日，不倒灌到过去。")
            st.subheader("不同分数区间的未来表现（事后统计）")
            summary = qfm.backtest_summary(qdf)
            if summary.empty:
                st.info("成熟的未来5/20/60交易日样本暂时不足，随着数据积累会逐步形成。")
            else:
                st.dataframe(summary, use_container_width=True, hide_index=True)
            st.caption(
                f"当前记录 {len(qdf)} 个交易日。样本长度受数据接口限制；"
                "样本数较少时统计结果不稳定，不能直接理解为未来上涨概率。"
            )
            display_cols = ["日期", "量化因子总分", "数据覆盖率"] + list(qfm.WEIGHTS)
            st.subheader("最近量化因子记录")
            st.dataframe(
                qdf[[c for c in display_cols if c in qdf.columns]].tail(100).sort_values("日期", ascending=False),
                use_container_width=True, hide_index=True
            )
            st.download_button(
                "下载量化因子历史 CSV",
                qdf.to_csv(index=False).encode("utf-8-sig"),
                "深南电路量化因子历史.csv",
                "text/csv",
            )
    else:
        st.info("尚未生成量化因子历史。下一次自动更新会尝试创建；也可以点击左侧按钮生成。")
except Exception as exc:
    st.error(f"量化因子模块暂时不可用：{exc}")

