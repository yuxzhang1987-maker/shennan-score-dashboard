from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import akshare as ak

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DAILY_FILE = DATA_DIR / "history.csv"
HOURLY_FILE = DATA_DIR / "hourly_history.csv"
ALERT_FILE = DATA_DIR / "alert_state.json"
AI_FILE = DATA_DIR / "ai_signal.txt"

# Five requested dimensions: 25 + 20 + 20 + 15 + 20 = 100.
WEIGHTS = {
    "深南走势评分": 0.25,
    "PCB板块评分": 0.20,
    "大资金流评分": 0.20,
    "美股科技评分": 0.15,
    "基本面评分": 0.20,
}
THRESHOLDS = (60, 70, 80, 90)


def clip(value):
    return value.clip(0, 100)


def as_num(series):
    return pd.to_numeric(series, errors="coerce")


def safe_call(fn, *args, **kwargs):
    last = None
    for attempt in range(2):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(2)
    raise last


def get_stock_daily():
    start = (datetime.now() - timedelta(days=500)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    df = safe_call(ak.stock_zh_a_hist, symbol="002916", period="daily", start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty or "收盘" not in df.columns:
        raise RuntimeError("东方财富未返回深南电路日线")
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.normalize()
    df["收盘"] = as_num(df["收盘"])
    df = df.dropna(subset=["日期", "收盘"]).sort_values("日期")
    return df.set_index("日期")["收盘"]


def get_stock_intraday():
    end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
    df = safe_call(ak.stock_zh_a_hist_min_em, symbol="002916", period="60", start_date=start, end_date=end, adjust="qfq")
    if df is None or df.empty or "收盘" not in df.columns or "时间" not in df.columns:
        raise RuntimeError("东方财富未返回深南电路60分钟K线")
    df = df.copy()
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    df["收盘"] = as_num(df["收盘"])
    return df.dropna(subset=["时间", "收盘"]).sort_values("时间")


def get_pcb_daily():
    start = (datetime.now() - timedelta(days=500)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    errors = []
    # Concept board first; component industry is a clearly labelled fallback proxy.
    for kind, symbol in [("PCB概念", "PCB概念"), ("元件行业代理", "元件")]:
        try:
            if kind == "PCB概念":
                df = safe_call(ak.stock_board_concept_hist_em, symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")
            else:
                df = safe_call(ak.stock_board_industry_hist_em, symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")
            if df is not None and not df.empty and "收盘" in df.columns:
                df = df.copy()
                date_col = "日期" if "日期" in df.columns else df.columns[0]
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
                df["收盘"] = as_num(df["收盘"])
                df = df.dropna(subset=[date_col, "收盘"]).sort_values(date_col)
                if len(df) >= 25:
                    s = df.set_index(date_col)["收盘"]
                    return s, kind
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
    raise RuntimeError("PCB/元件板块历史行情不可用；" + " | ".join(errors))


def get_fund_flow():
    df = safe_call(ak.stock_individual_fund_flow, stock="002916", market="sz")
    required = ["日期", "主力净流入-净额", "主力净流入-净占比"]
    if df is None or df.empty or any(c not in df.columns for c in required):
        raise RuntimeError("东方财富主力资金流接口数据为空或字段变化")
    df = df.copy()
    df["日期"] = pd.to_datetime(df["日期"], errors="coerce").dt.normalize()
    df["主力净流入（亿元）"] = as_num(df["主力净流入-净额"]) / 1e8
    df["主力净流入占比（%）"] = as_num(df["主力净流入-净占比"])
    df = df.dropna(subset=["日期", "主力净流入占比（%）"]).sort_values("日期")
    if df.empty:
        raise RuntimeError("主力资金流没有有效记录")
    df["大资金流评分"] = ((df["主力净流入占比（%）"] + 5) / 10 * 100).clip(0, 100)
    return df.set_index("日期")[["主力净流入（亿元）", "主力净流入占比（%）", "大资金流评分"]]


def get_us_tech():
    # Sina's China-facing US index endpoint; Nasdaq-100 is the primary proxy.
    df = safe_call(ak.index_us_stock_sina, symbol=".NDX")
    if df is None or df.empty or "close" not in df.columns:
        raise RuntimeError("新浪财经未返回纳斯达克100指数")
    df = df.copy()
    date_col = "date" if "date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    df["close"] = as_num(df["close"])
    s = df.dropna(subset=[date_col, "close"]).sort_values(date_col).set_index(date_col)["close"]
    if len(s) < 25:
        raise RuntimeError("纳斯达克100历史数据不足")
    return s


def trend_score(series):
    series = as_num(series).dropna().sort_index()
    ret20 = series.pct_change(20) * 100
    ma50 = series.rolling(50, min_periods=25).mean()
    gap = (series / ma50 - 1) * 100
    return 0.6 * clip((ret20 + 10) / 20 * 100) + 0.4 * clip((gap + 5) / 10 * 100)


def stock_score(daily, intraday):
    s = daily.copy()
    if intraday is not None and not intraday.empty:
        # Add the current intraday close to the daily history without rewriting prior bars.
        latest = intraday.iloc[-1]
        s.loc[pd.Timestamp(latest["时间"]).normalize()] = float(latest["收盘"])
        s = s.sort_index()
    r20 = s.pct_change(20) * 100
    r5 = s.pct_change(5) * 100
    ma50 = s.rolling(50, min_periods=25).mean()
    gap = (s / ma50 - 1) * 100
    return 0.5 * clip((r20 + 10) / 20 * 100) + 0.3 * clip((gap + 5) / 10 * 100) + 0.2 * clip((r5 + 5) / 10 * 100)


def read_fundamental_score():
    # Human-maintained proxy: ai_signal.txt remains -2..+2; default neutral=50.
    try:
        signal = float(AI_FILE.read_text(encoding="utf-8").strip())
        signal = max(-2.0, min(2.0, signal))
        return (signal + 2.0) / 4.0 * 100.0, "人工AI资本开支/基本面代理值"
    except Exception:
        return 50.0, "默认中性50分（未录入人工基本面信号）"


def latest_value(series):
    s = as_num(series).dropna()
    return float(s.iloc[-1]) if not s.empty else float("nan")


def merge_daily(old, row):
    new_row = pd.DataFrame([row])
    if old is None or old.empty:
        return new_row
    old = old.copy()
    old["日期"] = old["日期"].astype(str)
    # Replace today's snapshot rather than forward-filling failed fields from an older run.
    old = old[old["日期"] != str(row["日期"])]
    frame = pd.concat([old, new_row], ignore_index=True, sort=False)
    return frame.sort_values("日期")


def send_pushplus(message):
    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        print("INFO PUSHPLUS_TOKEN 未配置，跳过微信推送")
        return False
    response = requests.post(
        "https://www.pushplus.plus/send",
        json={"token": token, "title": "深南电路评分阈值提醒", "content": message, "template": "html"},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) not in ("200", "success"):
        raise RuntimeError(f"PushPlus返回异常：{result}")
    return True


def threshold_alert(score, stamp, details):
    state = {"alerted": [], "last_score": None}
    if ALERT_FILE.exists():
        try:
            state.update(json.loads(ALERT_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    alerted = set(int(x) for x in state.get("alerted", []))
    if pd.isna(score):
        return
    if score < 60:
        alerted.clear()
    crossed = [t for t in THRESHOLDS if score >= t and t not in alerted]
    if crossed:
        levels = "、".join(f"{x}分" for x in crossed)
        msg = (
            f"<b>深南电路（002916）综合评分达到阈值</b><br>"
            f"时间：{stamp}<br>综合评分：<b>{score:.1f}/100</b><br>"
            f"本次新触发：{levels}<br>{details}<br>"
            "提示：规则模型仅供参考，不构成买卖建议。"
        )
        try:
            sent = send_pushplus(msg)
            if sent:
                alerted.update(crossed)
        except Exception as exc:
            print("WARNING 微信推送失败：", exc)
    state = {"alerted": sorted(alerted), "last_score": round(float(score), 2), "updated_at": stamp}
    ALERT_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    errors, sources = [], []

    daily_stock = None
    intraday = None
    pcb = None
    flow = None
    us = None

    try:
        daily_stock = get_stock_daily(); sources.append("深南日线=东方财富")
    except Exception as exc: errors.append(f"深南日线：{exc}")
    try:
        intraday = get_stock_intraday(); sources.append("深南小时线=东方财富")
    except Exception as exc: errors.append(f"深南小时线：{exc}")
    try:
        pcb, pcb_label = get_pcb_daily(); sources.append(f"板块={pcb_label}")
    except Exception as exc: errors.append(f"PCB板块：{exc}")
    try:
        flow = get_fund_flow(); sources.append("资金流=东方财富")
    except Exception as exc: errors.append(f"资金流：{exc}")
    try:
        us = get_us_tech(); sources.append("美股科技=新浪纳斯达克100")
    except Exception as exc: errors.append(f"美股科技：{exc}")

    scores = {}
    if daily_stock is not None and len(daily_stock) >= 25:
        ss = stock_score(daily_stock, intraday)
        scores["深南走势评分"] = latest_value(ss)
        if intraday is not None and not intraday.empty:
            stock_price = float(intraday.iloc[-1]["收盘"])
            market_data_time = str(intraday.iloc[-1]["时间"])
        else:
            stock_price = latest_value(daily_stock)
            market_data_time = str(daily_stock.index[-1].date())
    else:
        stock_price, market_data_time = float("nan"), ""

    if pcb is not None:
        scores["PCB板块评分"] = latest_value(trend_score(pcb))
    if flow is not None:
        latest_flow = flow.iloc[-1]
        scores["大资金流评分"] = float(latest_flow["大资金流评分"])
    if us is not None:
        scores["美股科技评分"] = latest_value(trend_score(us))
    fundamental, fundamental_note = read_fundamental_score()
    scores["基本面评分"] = fundamental

    valid_weight = sum(WEIGHTS[k] for k, v in scores.items() if pd.notna(v))
    total = sum(scores[k] * WEIGHTS[k] for k in scores if pd.notna(scores[k])) / valid_weight if valid_weight else float("nan")
    coverage = valid_weight * 100

    flow_amt = float(flow.iloc[-1]["主力净流入（亿元）"]) if flow is not None else float("nan")
    flow_pct = float(flow.iloc[-1]["主力净流入占比（%）"]) if flow is not None else float("nan")
    flow_date = str(flow.index[-1].date()) if flow is not None else ""
    pcb_date = str(pcb.index[-1].date()) if pcb is not None else ""
    us_date = str(us.index[-1].date()) if us is not None else ""

    row = {
        "日期": now.strftime("%Y-%m-%d"), "更新时间": stamp, "行情数据时间": market_data_time,
        "深南电路收盘": stock_price, "深南走势评分": scores.get("深南走势评分"),
        "PCB板块评分": scores.get("PCB板块评分"), "PCB数据日期": pcb_date,
        "主力净流入（亿元）": flow_amt, "主力净流入占比（%）": flow_pct,
        "资金流数据日期": flow_date, "大资金流评分": scores.get("大资金流评分"),
        "美股科技评分": scores.get("美股科技评分"), "美股数据日期": us_date,
        "基本面评分": scores.get("基本面评分"), "基本面说明": fundamental_note,
        "综合评分_v2": total, "数据覆盖率_v2": coverage,
        "数据源": "；".join(sources), "数据异常": "；".join(errors),
    }

    # Every run creates an hourly observation. Never copy stale values into a new timestamp as if fresh.
    hourly = pd.read_csv(HOURLY_FILE) if HOURLY_FILE.exists() else pd.DataFrame()
    hourly = pd.concat([hourly, pd.DataFrame([row])], ignore_index=True)
    hourly = hourly.drop_duplicates(subset=["更新时间"], keep="last").sort_values("更新时间").tail(10000)
    hourly.to_csv(HOURLY_FILE, index=False, encoding="utf-8-sig")

    # Daily file keeps the latest snapshot for each calendar date while preserving old columns/history.
    old = pd.read_csv(DAILY_FILE) if DAILY_FILE.exists() else pd.DataFrame()
    daily_row = dict(row)
    daily = merge_daily(old, daily_row)
    daily.to_csv(DAILY_FILE, index=False, encoding="utf-8-sig")

    details = "；".join(f"{k}：{v:.1f}" for k, v in scores.items() if pd.notna(v))
    threshold_alert(total, stamp, details)

    print(f"Updated at {stamp}; score={total:.2f}; coverage={coverage:.0f}%")
    if errors:
        print("DATA_WARNINGS:", " | ".join(errors))
    if not scores or coverage < 80:
        print("WARNING 数据覆盖率低于80%，综合评分应谨慎参考。")


if __name__ == "__main__":
    main()
