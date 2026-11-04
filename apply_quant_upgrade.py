from pathlib import Path

app_path = Path(__file__).parent / "app.py"
source = app_path.read_text(encoding="utf-8")

marker = "# === SHENNAN_QUANT_FACTOR_V1 ==="
if marker in source:
    print("量化因子页面已经安装，无需重复修改 app.py。")
else:
    addition = r"""

# === SHENNAN_QUANT_FACTOR_V1 ===
st.divider()
st.title("🧮 深南电路量化因子模型 V1.0")
st.caption("独立实验模型｜六大因子｜历史样本检验｜不改变原有综合评分")
st.warning(
    "本页面是研究工具，不是上涨概率或买卖指令。历史回测不代表未来表现。"
    "当前基本面因子沿用人工代理信号；没有人工输入时显示缺失，不伪造自动财报评分。"
)

try:
    import quant_factor_model as qfm
    qpath = qfm.HISTORY_FILE
    if st.button("🔄 计算/刷新量化因子与历史回测", type="primary"):
        with st.spinner("正在获取深南电路、PCB和资金流历史数据并计算因子……"):
            qfm.calculate_and_save()
        st.success("量化因子数据已更新。")
        st.rerun()

    if qpath.exists():
        qdf = pd.read_csv(qpath)
        if not qdf.empty:
            qdf["日期"] = pd.to_datetime(qdf["日期"], errors="coerce")
            qdf = qdf.dropna(subset=["日期"]).sort_values("日期")
            latest_q = qdf.iloc[-1]
            q1, q2, q3 = st.columns(3)
            q1.metric("最新量化因子总分", f"{latest_q.get('量化因子总分', float('nan')):.1f}/100" if pd.notna(latest_q.get("量化因子总分")) else "暂无")
            q2.metric("数据覆盖率", f"{latest_q.get('数据覆盖率', float('nan')):.0f}%" if pd.notna(latest_q.get("数据覆盖率")) else "暂无")
            q3.metric("数据日期", latest_q["日期"].strftime("%Y-%m-%d"))

            st.subheader("六大因子得分")
            factor_names = list(qfm.WEIGHTS)
            factor_rows = []
            for name in factor_names:
                val = pd.to_numeric(latest_q.get(name), errors="coerce")
                factor_rows.append({
                    "因子": name,
                    "得分": round(float(val), 1) if pd.notna(val) else "暂无",
                    "权重": f"{qfm.WEIGHTS[name]:.0f}%",
                })
            st.dataframe(pd.DataFrame(factor_rows), use_container_width=True, hide_index=True)

            st.subheader("量化因子历史曲线")
            qchart = qdf[["日期", "历史可回测因子分"]].copy().set_index("日期")
            qchart["历史可回测因子分"] = pd.to_numeric(qchart["历史可回测因子分"], errors="coerce")
            st.line_chart(qchart, height=260)
            st.caption("历史曲线使用可逐日重建的五项市场因子；最新总分若有人工基本面信号，会额外纳入基本面15%。")

            st.subheader("历史回测：不同分数区间的后续表现")
            summary = qfm.backtest_summary(qdf)
            if summary.empty:
                st.info("目前成熟的未来收益样本不足。随着后续交易日数据增加，5/20/60日统计会逐步形成。")
            else:
                st.dataframe(summary, use_container_width=True, hide_index=True)
            st.caption(
                f"回测样本覆盖约{len(qdf)}个交易日，受数据源可用历史长度限制；"
                "历史分组使用五项可逐日重建的市场因子，不纳入当前人工基本面信号，避免把当前信息倒灌到过去。"
                "未来收益只用于事后统计，不参与当日因子评分。"
            )
            st.subheader("最近量化因子记录")
            display_cols = ["日期", "量化因子总分", "数据覆盖率"] + factor_names
            st.dataframe(qdf[[c for c in display_cols if c in qdf.columns]].tail(120).sort_values("日期", ascending=False),
                         use_container_width=True, hide_index=True)
            st.download_button("下载量化因子历史 CSV", qdf.to_csv(index=False).encode("utf-8-sig"),
                               "深南电路量化因子历史.csv", "text/csv")
    else:
        st.info("尚未生成量化因子历史。点击上方按钮，首次计算并建立独立历史文件。")
except Exception as exc:
    st.error(f"量化因子模块暂时不可用：{exc}")
"""
    app_path.write_text(source.rstrip() + "\n\n" + marker + addition + "\n", encoding="utf-8")
    print("已在 app.py 末尾增加独立量化因子页面。")
print("注意：本补丁不修改 data/ 中任何现有文件。")
