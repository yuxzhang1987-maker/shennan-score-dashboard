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


# ==================== 独立智能做T模块 ====================
st.divider()
st.title("🎯 深南电路智能做T系统")
st.caption("混合策略：震荡高抛低吸 + 趋势跟随｜稳健过滤｜T+1约束｜独立于长期底仓")
st.warning("本模块为研究与模拟辅助，不会自动下单。A股实行T+1：当天新买入的股份不能当天卖出；卖出信号只能针对已有可卖底仓。行情和回测样本不足时，系统会明确提示，不输出虚假的胜率。")

try:
    import intraday_t_model as tmodel
    tcol1, tcol2, tcol3 = st.columns([1, 1, 2])
    with tcol1:
        t_capital = st.number_input("做T机动资金（元）", min_value=10000, max_value=100000, value=50000, step=5000)
    with tcol2:
        t_cost_bps = st.number_input("双边费用+滑点假设（基点）", min_value=10, max_value=100, value=25, step=5)
    with tcol3:
        sellable_shares = st.number_input("当前可卖底仓股数（股）", min_value=0, max_value=1000000, value=0, step=100)
    if st.button("📥 更新5分钟行情并计算做T信号", type="primary", use_container_width=True):
        with st.spinner("正在获取深南电路5分钟K线、更新历史样本并计算信号……"):
            try:
                scored_t, signal_t, bt_t, source_t = tmodel.run_update()
                st.success(f"行情更新完成：{source_t}；共{len(scored_t)}根5分钟K线")
                st.rerun()
            except Exception as exc:
                st.error(f"做T行情更新失败：{exc}")
    if tmodel.BARS_FILE.exists():
        bars_t = pd.read_csv(tmodel.BARS_FILE)
        scored_t = tmodel.calculate(bars_t)
        signal_t = tmodel.current_signal(scored_t)
        bt_t = tmodel.backtest_next_session(scored_t, cost_bps=float(t_cost_bps))
        bt_sell_t = tmodel.backtest_sell_first_same_day(scored_t, target_pct=0.45, cost_bps=float(t_cost_bps))
        a,b,c,d = st.columns(4)
        a.metric("当前模型状态", signal_t.get("action","数据不足"))
        b.metric("最新行情价", fmt_num(signal_t.get("price"),"元",2))
        c.metric("RSI(14)", fmt_num(signal_t.get("rsi"),"",1))
        d.metric("趋势识别", signal_t.get("trend","未知"))
        st.caption(f"行情时间：{signal_t.get('time','未知')}｜策略：{signal_t.get('strategy','-')}｜VWAP：{fmt_num(signal_t.get('vwap'),'元',2)}｜ATR：{fmt_num(signal_t.get('atr'),'元',2)}")
        st.info(signal_t.get("reason","当前无有效信号"))
        if signal_t.get("action")=="关注卖出" and sellable_shares<=0:
            st.warning("你尚未填写可卖底仓股数。卖出信号仅作观察，不据此执行先卖后买。")
        if signal_t.get("price") and signal_t.get("atr"):
            px=float(signal_t["price"]); atr=float(signal_t["atr"])
            buy_low=max(0,px-0.8*atr); buy_high=max(0,px-0.3*atr)
            sell_low=px+0.3*atr; sell_high=px+0.8*atr
            p1,p2,p3=st.columns(3)
            p1.metric("ATR波动参考买入观察区",f"{buy_low:.2f}–{buy_high:.2f}元")
            p2.metric("ATR波动参考卖出观察区",f"{sell_low:.2f}–{sell_high:.2f}元")
            one_trade_cash=min(float(t_capital)*0.25,25000.0)
            one_trade_lots=int(one_trade_cash/px/100)*100 if px>0 else 0
            p3.metric("单次计划资金上限",f"{one_trade_cash:,.0f}元",help=f"按当前价约{one_trade_lots}股，按100股整数手估算；不是下单指令")
            st.caption("以上只是ATR波动观察区，不是已验证的买卖价位；必须同时满足策略信号、成本覆盖和T+1可卖数量约束。")
        st.subheader("历史信号检验：先卖后买回（同日T差价测试）")
        z1,z2,z3,z4 = st.columns(4)
        z1.metric("卖出信号数",str(bt_sell_t.get("signals",0)))
        z2.metric("完成回补次数",str(bt_sell_t.get("completed",0)))
        z3.metric("回补完成率",fmt_num(bt_sell_t.get("completion_rate"),"%",1))
        z4.metric("平均净差价",fmt_num(bt_sell_t.get("avg_net_spread"),"%",2))
        st.caption(bt_sell_t.get("note","暂无同日回补测试结果"))
        st.caption("该测试假设你有可卖的原有底仓，卖出后同日回落挂单买回；未回补样本的机会成本尚未计入，不能单独视为完整策略收益。")
        st.subheader("历史信号检验（T+1方向性测试）")
        x1,x2,x3,x4 = st.columns(4)
        x1.metric("有效样本数",str(bt_t.get("samples",0)))
        x2.metric("历史胜率",fmt_num(bt_t.get("win_rate"),"%",1))
        x3.metric("平均收益",fmt_num(bt_t.get("avg_return"),"%",2))
        x4.metric("盈利因子",fmt_num(bt_t.get("profit_factor"),"",2))
        st.caption(bt_t.get("note","暂无回测说明"))
        if bt_t.get("samples",0)<100:
            st.warning("目前有效样本不足100次，胜率和收益统计暂不具备稳定参考价值。系统不会把当前统计包装成已达到60%的准确率。建议继续积累历史5分钟数据，并进行滚动样本外验证。")
        elif bt_t.get("win_rate") is not None and bt_t["win_rate"]<60:
            st.warning("当前样本外/历史统计胜率尚未达到60%目标，不建议据此投入实盘资金。")
        else:
            st.success("当前统计达到初步观察门槛，但仍需检查样本外表现、盈亏比、最大回撤和实际成交成本。")
        st.subheader("最近5分钟行情与策略信号")
        cols_t=[c for c in ["时间","收盘","EMA9","EMA21","RSI14","日内VWAP","量比","趋势状态","策略类型","买入信号","卖出信号"] if c in scored_t.columns]
        st.dataframe(scored_t[cols_t].tail(100).sort_values("时间",ascending=False),use_container_width=True,hide_index=True)
        st.download_button("下载做T五分钟行情历史",bars_t.to_csv(index=False).encode("utf-8-sig"),"深南电路做T_5分钟行情.csv","text/csv")
    else:
        st.info("尚无独立5分钟行情历史。点击上方按钮首次获取；若数据源暂时不可用，原有综合评分系统不受影响。")
    st.subheader("交易纪律")
    st.markdown("- 稳健过滤：没有信号就观望，不设每日交易任务。\n- 单次计划资金建议先限制在机动资金的20%–30%，不一次性用满5–10万元。\n- 卖出信号只针对已有可卖底仓；新买入的股票遵守T+1，不能当日卖出。\n- 买卖价差必须覆盖佣金、印花税（卖出端）和滑点；未覆盖成本不做。\n- 先模拟交易至少20–30个交易日，再考虑小额实盘。")
except Exception as exc:
    st.error(f"智能做T模块暂时不可用：{exc}")
