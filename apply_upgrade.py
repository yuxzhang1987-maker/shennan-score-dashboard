from pathlib import Path
import ast
import shutil
import sys

ROOT = Path(__file__).resolve().parent
UPDATE = ROOT / "update_data.py"
APP = ROOT / "app.py"
HELPER = ROOT / "us_market_functions.txt"

def replace_between(source, start_marker, end_marker, replacement, label):
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError(f"找不到代码位置：{label}（起始标记）")
    end = source.find(end_marker, start + len(start_marker))
    if end < 0:
        raise RuntimeError(f"找不到代码位置：{label}（结束标记）")
    return source[:start] + replacement + "\n\n" + source[end:]

def replace_once(source, old, new, label):
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}：预期找到1处，实际找到{count}处。为避免误改，已停止。")
    return source.replace(old, new, 1)

def main():
    if not UPDATE.exists() or not APP.exists() or not HELPER.exists():
        print("错误：请把本包全部文件放在项目根目录，且同一目录下有 app.py 和 update_data.py。")
        sys.exit(1)

    u = UPDATE.read_text(encoding="utf-8")
    a = APP.read_text(encoding="utf-8")
    helper = HELPER.read_text(encoding="utf-8").strip()

    u = replace_between(u, "def get_us_tech():", "def _score_pct(", helper, "美国市场行情获取函数")

    u = replace_once(u,
        "daily_stock = intraday = pcb = flow = us = None",
        "daily_stock = intraday = pcb = flow = None\n    us_data, us_sources, us_errors = {}, {}, []",
        "主流程美股变量")

    old_fetch = (
        '    try:\n'
        '        us = get_us_tech(); sources.append("美股科技=新浪财经纳斯达克100")\n'
        '    except Exception as exc:\n'
        '        errors.append(f"美股科技：{exc}")\n'
    )
    new_fetch = (
        '    try:\n'
        '        us_data, us_sources, us_errors = get_us_tech()\n'
        '        if us_data:\n'
        '            sources.append("美股科技=" + "、".join(us_sources.values()))\n'
        '        errors.extend(us_errors)\n'
        '        if not us_data:\n'
        '            errors.append("美股科技：SOX/NDX/美股龙头均未取得有效行情")\n'
        '    except Exception as exc:\n'
        '        errors.append(f"美股科技：{exc}")\n'
    )
    u = replace_once(u, old_fetch, new_fetch, "美股数据获取主流程")

    old_score = (
        '    if us is not None:\n'
        '        scores["美股科技评分"] = latest_value(trend_score(us))\n'
    )
    new_score = (
        '    us_component_scores = {}\n'
        '    us_available_weight = 0.0\n'
        '    us_latest_dates = []\n'
        '    for metric_name, (symbol, component_weight) in US_MARKET_ITEMS.items():\n'
        '        series = us_data.get(metric_name)\n'
        '        if series is not None and not series.empty:\n'
        '            component_score = latest_value(trend_score(series))\n'
        '            if pd.notna(component_score):\n'
        '                us_component_scores[metric_name] = component_score\n'
        '                us_available_weight += component_weight\n'
        '                us_latest_dates.append(pd.Timestamp(series.dropna().index[-1]).date())\n'
        '    if us_available_weight > 0:\n'
        '        scores["美股科技评分"] = sum(us_component_scores[name] * US_MARKET_ITEMS[name][1] for name in us_component_scores) / us_available_weight\n'
        '    else:\n'
        '        scores["美股科技评分"] = float("nan")\n'
    )
    u = replace_once(u, old_score, new_score, "美股综合评分计算")

    old_weights = (
        '    valid_keys = [k for k, v in scores.items() if pd.notna(v) and (k != "基本面评分" or fundamental_available)]\n'
        '    valid_weight = sum(WEIGHTS[k] for k in valid_keys)\n'
        '    total = sum(scores[k] * WEIGHTS[k] for k in valid_keys) / valid_weight if valid_weight else float("nan")\n'
    )
    new_weights = (
        '    valid_keys = [k for k, v in scores.items() if pd.notna(v) and (k != "基本面评分" or fundamental_available)]\n'
        '    effective_weights = dict(WEIGHTS)\n'
        '    effective_weights["美股科技评分"] = WEIGHTS["美股科技评分"] * us_available_weight\n'
        '    valid_weight = sum(effective_weights[k] for k in valid_keys)\n'
        '    total = sum(scores[k] * effective_weights[k] for k in valid_keys) / valid_weight if valid_weight else float("nan")\n'
    )
    u = replace_once(u, old_weights, new_weights, "综合评分权重覆盖率")

    u = replace_once(u,
        '    us_date = str(us.index[-1].date()) if us is not None else ""',
        '    us_date = max(us_latest_dates).isoformat() if us_latest_dates else ""\n    us_coverage = round(us_available_weight * 100, 1)',
        "美股数据日期")

    u = replace_once(u,
        '        "美股科技评分": scores.get("美股科技评分"), "美股数据日期": us_date,',
        '        "美股科技评分": scores.get("美股科技评分"), "美股数据日期": us_date,\n'
        '        "美股科技有效覆盖率": us_coverage,\n'
        '        "费城半导体指数SOX评分": us_component_scores.get("费城半导体指数SOX评分"),\n'
        '        "纳斯达克100指数评分": us_component_scores.get("纳斯达克100指数评分"),\n'
        '        "英伟达NVDA评分": us_component_scores.get("英伟达NVDA评分"),\n'
        '        "AMD评分": us_component_scores.get("AMD评分"),\n'
        '        "博通AVGO评分": us_component_scores.get("博通AVGO评分"),\n'
        '        "美股数据源": "；".join(us_sources.values()),',
        "美股历史字段")

    old_metrics = (
        '    metrics = [\n'
        '        ("大资金流向", "大资金流评分", "20%"),\n'
        '        ("美股科技趋势（纳斯达克100）", "美股科技评分", "15%"),\n'
        '        ("基本面/人工代理", "基本面评分", "20%"),\n'
        '    ]\n'
        '    cols = st.columns(3)\n'
        '    for i, (label, key, weight) in enumerate(metrics):\n'
        '        cols[i].metric(label, fmt_num(latest.get(key)), help=f"权重 {weight}")\n'
    )
    new_metrics = (
        '    metrics = [("大资金流向", "大资金流评分", "20%"), ("美股科技综合评分", "美股科技评分", "15%"), ("基本面/人工代理", "基本面评分", "20%")]\n'
        '    cols = st.columns(3)\n'
        '    for i, (label, key, weight) in enumerate(metrics):\n'
        '        cols[i].metric(label, fmt_num(latest.get(key)), help=f"权重 {weight}")\n'
        '\n'
        '    st.subheader("🇺🇸 美国科技市场综合评分")\n'
        '    st.caption("美股科技总权重仍为15%；内部按SOX 40%、纳斯达克100 25%、英伟达15%、AMD 10%、博通10%计算。某项数据不可用时，其内部权重不强行补分，并按有效权重同步降低总评分覆盖率。")\n'
        '    us_items = [("费城半导体指数 SOX", "费城半导体指数SOX评分", "内部40%"), ("纳斯达克100", "纳斯达克100指数评分", "内部25%"), ("英伟达 NVIDIA", "英伟达NVDA评分", "内部15%"), ("AMD", "AMD评分", "内部10%"), ("博通 Broadcom", "博通AVGO评分", "内部10%")]\n'
        '    us_cols = st.columns(5)\n'
        '    for i, (label, key, weight) in enumerate(us_items):\n'
        '        us_cols[i].metric(label, fmt_num(latest.get(key)), help=weight)\n'
        '    st.caption(f"美股数据日期：{latest.get(\'美股数据日期\', \'暂无\')}｜美国科技内部有效覆盖：{fmt_num(latest.get(\'美股科技有效覆盖率\'), \'%\', 0)}｜数据源：{latest.get(\'美股数据源\', \'暂无\')}")\n'
    )
    a = replace_once(a, old_metrics, new_metrics, "网页美股指标展示")

    old_chart = '["综合评分_v2", "深南近期走势评分", "深南中期走势评分", "PCB近期走势评分", "PCB中期走势评分"]'
    new_chart = '["综合评分_v2", "深南近期走势评分", "深南中期走势评分", "PCB近期走势评分", "PCB中期走势评分", "美股科技评分", "费城半导体指数SOX评分", "纳斯达克100指数评分", "英伟达NVDA评分", "AMD评分", "博通AVGO评分"]'
    if a.count(old_chart) != 2:
        raise RuntimeError(f"图表指标列表：预期找到2处，实际找到{a.count(old_chart)}处。为避免误改，已停止。")
    a = a.replace(old_chart, new_chart)
    a = replace_once(a,
        '"大资金流评分", "美股科技评分", "基本面评分", "主力净流入（亿元）"',
        '"大资金流评分", "美股科技评分", "美股科技有效覆盖率", "费城半导体指数SOX评分", "纳斯达克100指数评分", "英伟达NVDA评分", "AMD评分", "博通AVGO评分", "美股数据日期", "美股数据源", "基本面评分", "主力净流入（亿元）"',
        "逐小时表格字段")

    ast.parse(u)
    ast.parse(a)
    shutil.copy2(UPDATE, UPDATE.with_suffix(".py.before_us_upgrade.bak"))
    shutil.copy2(APP, APP.with_suffix(".py.before_us_upgrade.bak"))
    UPDATE.write_text(u, encoding="utf-8")
    APP.write_text(a, encoding="utf-8")
    print("升级完成：已修改 update_data.py 和 app.py。")
    print("原文件备份已保留；data/ 目录和所有历史CSV均未修改。")
    print("请运行：python -m py_compile update_data.py app.py")
    print("语法检查通过后，只需把这两个修改后的文件上传覆盖GitHub仓库根目录同名文件。")

if __name__ == "__main__":
    main()
