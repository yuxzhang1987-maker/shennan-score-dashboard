from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

try:
    import akshare as ak
except Exception:
    ak = None

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DAILY_FILE = DATA_DIR / "history.csv"
HOURLY_FILE = DATA_DIR / "hourly_history.csv"
ALERT_FILE = DATA_DIR / "alert_state.json"
ALERT_HISTORY_FILE = DATA_DIR / "alert_history.csv"
AI_FILE = DATA_DIR / "ai_signal.txt"

WEIGHTS = {
    "深南走势评分": 0.25,
    "PCB板块评分": 0.20,
    "大资金流评分": 0.20,
    "美股科技评分": 0.15,
    "基本面评分": 0.20,
}
THRESHOLDS = (60, 70, 80, 90)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clip(value):
    return value.clip(0, 100)


def as_num(series):
    return pd.to_numeric(series, errors="coerce")


def http_get(url, params=None, timeout=15):
    response = SESSION.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response


def parse_em_klines(secid: str, klt: int, beg: str, end: str, limit: int = 600) -> pd.DataFrame:
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": str(klt), "fqt": "1", "beg": beg, "end": end, "lmt": str(limit),
    }
    payload = http_get(url, params).json()
    rows = (payload.get("data") or {}).get("klines") or []
    if not rows:
        raise RuntimeError(f"东方财富K线返回空数据: {secid}, klt={klt}")
    parsed = [str(x).split(",") for x in rows]
    df = pd.DataFrame(parsed, columns=["时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"][:len(parsed[0])])
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额"]:
        if col in df.columns:
            df[col] = as_num(df[col])
    return df.dropna(subset=["时间", "收盘"]).sort_values("时间")


def parse_sina_kline(code: str, scale: int = 240, count: int = 600) -> pd.DataFrame:
    # Sina Finance is a mainland-China-facing public market-data endpoint.
    market_code = ("sh" if code.startswith(("6", "9")) else "sz") + code
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    payload = http_get(url, {"symbol": market_code, "scale": str(scale), "ma": "no", "datalen": str(count)}).json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"新浪K线返回空数据: {market_code}")
    df = pd.DataFrame(payload)
    time_col = "day" if "day" in df.columns else "date"
    if time_col not in df.columns or "close" not in df.columns:
        raise RuntimeError("新浪K线字段结构变化")
    df = df.rename(columns={time_col: "时间", "close": "收盘", "open": "开盘", "high": "最高", "low": "最低", "volume": "成交量"})
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    df["收盘"] = as_num(df["收盘"])
    return df.dropna(subset=["时间", "收盘"]).sort_values("时间")


def parse_tencent_kline(code: str, count: int = 600) -> pd.DataFrame:
    market_code = ("sh" if code.startswith(("6", "9")) else "sz") + code
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    payload = http_get(url, {"param": f"{market_code},day,,,{count},qfq"}).json()
    item = (payload.get("data") or {}).get(market_code) or {}
    rows = item.get("qfqday") or item.get("day") or []
    if not rows:
        raise RuntimeError(f"腾讯K线返回空数据: {market_code}")
    df = pd.DataFrame(rows)
    # Tencent row format: date, open, close, high, low, volume, amount ...
    df = df.iloc[:, :7].copy()
    df.columns = ["时间", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]
    df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
    for col in ["开盘", "收盘", "最高", "最低", "成交量", "成交额"]:
        df[col] = as_num(df[col])
    return df.dropna(subset=["时间", "收盘"]).sort_values("时间")


def check_fresh(series: pd.Series, label: str, max_age_days: int = 10):
    if series is None or len(series.dropna()) < 55:
        raise RuntimeError(f"{label}有效历史K线不足55个交易日")
    last_date = pd.Timestamp(series.dropna().index[-1]).date()
    if (datetime.now().date() - last_date).days > max_age_days:
        raise RuntimeError(f"{label}最新数据日期为{last_date}，已超过{max_age_days}天，拒绝作为最新行情")


def get_stock_daily():
    beg = (datetime.now() - timedelta(days=600)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    errors = []
    providers = [
        ("东方财富直连接口", lambda: parse_em_klines("0.002916", 101, beg, end, 600)),
        ("新浪财经", lambda: parse_sina_kline("002916", 240, 600)),
        ("腾讯财经", lambda: parse_tencent_kline("002916", 600)),
    ]
    # AKShare is retained as a fourth fallback, not the only dependency.
    if ak is not None:
        providers.append(("AKShare/东方财富", lambda: ak.stock_zh_a_hist(symbol="002916", period="daily", start_date=beg, end_date=end, adjust="qfq").rename(columns={"日期": "时间"})))
    for name, fn in providers:
        try:
            df = fn()
            if df is None or df.empty or "收盘" not in df.columns:
                raise RuntimeError("返回空数据或缺少收盘字段")
            df = df.copy()
            df["时间"] = pd.to_datetime(df["时间"], errors="coerce")
            df["收盘"] = as_num(df["收盘"])
            df = df.dropna(subset=["时间", "收盘"]).sort_values("时间")
            s = df.set_index("时间")["收盘"]
            s.index = s.index.normalize()
            check_fresh(s, "深南电路日线")
            return s, name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("深南电路日线所有数据源均失败；" + " | ".join(errors))


def get_stock_intraday():
    beg = (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    errors = []
    providers = [
        ("东方财富60分钟K线", lambda: parse_em_klines("0.002916", 60, beg, end, 500)),
        ("新浪财经60分钟K线", lambda: parse_sina_kline("002916", 60, 500)),
    ]
    for name, fn in providers:
        try:
            df = fn()
            if df is not None and not df.empty:
                # Keep bars from the recent period only.
                df = df[df["时间"] >= pd.Timestamp(datetime.now() - timedelta(days=15))]
                if not df.empty:
                    df.attrs["source"] = name
                    return df
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    print("WARN 分钟行情不可用，评分将使用最近日线收盘：", " | ".join(errors))
    return pd.DataFrame()


# A stable fallback basket of listed PCB makers / PCB-related manufacturers.
# This is explicitly labelled as a constituent proxy, not the official sector index.
PCB_BASKET = ["002916", "002463", "002938", "300476", "603920", "688183", "002579", "603936", "605058", "688519", "002384", "600183"]


def get_eastmoney_pcb_board():
    # Resolve the current PCB concept-board code dynamically, then read its own K-line.
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": "1", "pz": "500", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f3", "fs": "m:90+t:3", "fields": "f12,f14"}
    payload = http_get(url, params).json()
    diff = (payload.get("data") or {}).get("diff") or []
    if isinstance(diff, dict):
        diff = list(diff.values())
    matches = [x for x in diff if "PCB" in str(x.get("f14", "")).upper()]
    if not matches:
        raise RuntimeError("东方财富概念板块列表中未找到PCB概念板块")
    # Prefer the exact PCB概念 name where available.
    matches.sort(key=lambda x: ("PCB概念" not in str(x.get("f14", "")), len(str(x.get("f14", "")))))
    code = str(matches[0].get("f12", ""))
    if not code:
        raise RuntimeError("PCB概念板块代码为空")
    beg = (datetime.now() - timedelta(days=600)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    df = parse_em_klines(f"90.{code}", 101, beg, end, 600)
    s = df.set_index("时间")["收盘"]
    s.index = s.index.normalize()
    check_fresh(s, "PCB概念指数")
    return s, f"东方财富PCB概念指数({code})"


def get_pcb_basket_proxy():
    errors = []
    series = {}
    def fetch(code):
        try:
            try:
                df = parse_tencent_kline(code, 600)
                source = "腾讯财经"
            except Exception:
                df = parse_sina_kline(code, 240, 600)
                source = "新浪财经"
            s = df.set_index("时间")["收盘"]
            s.index = s.index.normalize()
            s = s[~s.index.duplicated(keep="last")].dropna()
            if len(s) >= 55:
                return code, s, source
            raise RuntimeError("有效历史不足55日")
        except Exception as exc:
            return code, None, str(exc)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch, code) for code in PCB_BASKET]
        for future in as_completed(futures):
            code, s, info = future.result()
            if s is not None:
                series[code] = s
            else:
                errors.append(f"{code}:{info}")
    if len(series) < 5:
        raise RuntimeError(f"PCB成分股代理可用股票不足5只（{len(series)}只）；" + " | ".join(errors))
    frame = pd.concat(series, axis=1).sort_index().ffill()
    # Use common dates and normalize each constituent to 100, then equal-weight average.
    frame = frame.dropna(how="any")
    if len(frame) < 55:
        raise RuntimeError("PCB成分股共同历史区间不足55日")
    normalized = frame.div(frame.iloc[0]).mul(100)
    basket = normalized.mean(axis=1)
    check_fresh(basket, "PCB成分股等权代理")
    return basket, f"PCB成分股等权代理({len(series)}/{len(PCB_BASKET)}只；腾讯/新浪)"


def get_pcb_daily():
    errors = []
    for name, fn in [("东方财富PCB概念指数", get_eastmoney_pcb_board), ("PCB成分股代理", get_pcb_basket_proxy)]:
        try:
            return fn()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    # Last-resort proxy: 元件行业板块. Keep its identity explicit.
    if ak is not None:
        try:
            start = (datetime.now() - timedelta(days=600)).strftime("%Y%m%d")
            end = datetime.now().strftime("%Y%m%d")
            df = ak.stock_board_industry_hist_em(symbol="元件", period="daily", start_date=start, end_date=end, adjust="")
            if df is not None and not df.empty:
                date_col = "日期" if "日期" in df.columns else df.columns[0]
                df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
                df["收盘"] = as_num(df["收盘"])
                s = df.dropna(subset=[date_col, "收盘"]).set_index(date_col)["收盘"].sort_index()
                check_fresh(s, "元件行业代理")
                return s, "东方财富元件行业代理（非纯PCB）"
        except Exception as exc:
            errors.append(f"元件行业代理: {exc}")
    raise RuntimeError("PCB数据源均不可用；" + " | ".join(errors))


def get_fund_flow():
    if ak is None:
        raise RuntimeError("资金流接口需要AKShare；本次运行环境未安装")
    df = safe_ak_call(ak.stock_individual_fund_flow, stock="002916", market="sz")
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


def safe_ak_call(fn, *args, **kwargs):
    last = None
    for attempt in range(2):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last = exc
            if attempt == 0:
                time.sleep(2)
    raise last


def get_us_tech():
    # Sina Finance is a China-facing data source; Nasdaq-100 is used as the US tech proxy.
    if ak is None:
        raise RuntimeError("美股指数接口需要AKShare，本次运行环境未安装")
    df = safe_ak_call(ak.index_us_stock_sina, symbol=".NDX")
    if df is None or df.empty or "close" not in df.columns:
        raise RuntimeError("新浪财经未返回纳斯达克100指数")
    df = df.copy()
    date_col = "date" if "date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.normalize()
    df["close"] = as_num(df["close"])
    s = df.dropna(subset=[date_col, "close"]).sort_values(date_col).set_index(date_col)["close"]
    if len(s) < 55:
        raise RuntimeError("纳斯达克100历史数据不足55日")
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
        latest = intraday.iloc[-1]
        s.loc[pd.Timestamp(latest["时间"]).normalize()] = float(latest["收盘"])
        s = s.sort_index()
    r20 = s.pct_change(20) * 100
    r5 = s.pct_change(5) * 100
    ma50 = s.rolling(50, min_periods=25).mean()
    gap = (s / ma50 - 1) * 100
    return 0.5 * clip((r20 + 10) / 20 * 100) + 0.3 * clip((gap + 5) / 10 * 100) + 0.2 * clip((r5 + 5) / 10 * 100)


def read_fundamental_score():
    try:
        signal = float(AI_FILE.read_text(encoding="utf-8").strip())
        signal = max(-2.0, min(2.0, signal))
        return (signal + 2.0) / 4.0 * 100.0, "人工基本面代理值", True
    except Exception:
        # Display a neutral placeholder, but do not count it as a real data source
        # in the coverage ratio or weighted total.
        return 50.0, "默认中性50分（未录入人工基本面信号；不计入覆盖率）", False


def latest_value(series):
    s = as_num(series).dropna()
    return float(s.iloc[-1]) if not s.empty else float("nan")


def merge_daily(old, row):
    new_row = pd.DataFrame([row])
    if old is None or old.empty:
        return new_row
    old = old.copy()
    old["日期"] = old["日期"].astype(str)
    old = old[old["日期"] != str(row["日期"])]
    return pd.concat([old, new_row], ignore_index=True, sort=False).sort_values("日期")


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
    crossed = [threshold for threshold in THRESHOLDS if score >= threshold and threshold not in alerted]
    if crossed:
        rows = [{"触发时间": stamp, "触发阈值": t, "当时综合评分": round(float(score), 2), "说明": details, "提醒方式": "网站内历史提醒（无需Token）"} for t in crossed]
        new_alerts = pd.DataFrame(rows)
        if ALERT_HISTORY_FILE.exists():
            try:
                new_alerts = pd.concat([pd.read_csv(ALERT_HISTORY_FILE), new_alerts], ignore_index=True)
            except Exception:
                pass
        new_alerts.to_csv(ALERT_HISTORY_FILE, index=False, encoding="utf-8-sig")
        alerted.update(crossed)
    ALERT_FILE.write_text(json.dumps({"alerted": sorted(alerted), "last_score": round(float(score), 2), "updated_at": stamp}, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    errors, sources = [], []
    daily_stock = intraday = pcb = flow = us = None
    stock_source = intraday_source = pcb_source = ""

    try:
        daily_stock, stock_source = get_stock_daily()
        sources.append(f"深南日线={stock_source}")
    except Exception as exc:
        errors.append(f"深南日线：{exc}")
    try:
        intraday = get_stock_intraday()
        if intraday is not None and not intraday.empty:
            intraday_source = intraday.attrs.get("source", "分钟线")
            sources.append(f"深南小时线={intraday_source}")
    except Exception as exc:
        errors.append(f"深南小时线：{exc}")
    try:
        pcb, pcb_source = get_pcb_daily()
        sources.append(f"PCB={pcb_source}")
    except Exception as exc:
        errors.append(f"PCB板块：{exc}")
    try:
        flow = get_fund_flow(); sources.append("资金流=东方财富（AKShare接口）")
    except Exception as exc:
        errors.append(f"资金流：{exc}")
    try:
        us = get_us_tech(); sources.append("美股科技=新浪财经纳斯达克100")
    except Exception as exc:
        errors.append(f"美股科技：{exc}")

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
        scores["大资金流评分"] = float(flow.iloc[-1]["大资金流评分"])
    if us is not None:
        scores["美股科技评分"] = latest_value(trend_score(us))
    fundamental, fundamental_note, fundamental_available = read_fundamental_score()
    scores["基本面评分"] = fundamental

    valid_keys = [k for k, v in scores.items() if pd.notna(v) and (k != "基本面评分" or fundamental_available)]
    valid_weight = sum(WEIGHTS[k] for k in valid_keys)
    total = sum(scores[k] * WEIGHTS[k] for k in valid_keys) / valid_weight if valid_weight else float("nan")
    coverage = valid_weight * 100
    flow_amt = float(flow.iloc[-1]["主力净流入（亿元）"]) if flow is not None else float("nan")
    flow_pct = float(flow.iloc[-1]["主力净流入占比（%）"]) if flow is not None else float("nan")
    flow_date = str(flow.index[-1].date()) if flow is not None else ""
    pcb_date = str(pcb.index[-1].date()) if pcb is not None else ""
    us_date = str(us.index[-1].date()) if us is not None else ""

    row = {
        "日期": now.strftime("%Y-%m-%d"), "更新时间": stamp, "行情数据时间": market_data_time,
        "深南电路收盘": stock_price, "深南走势评分": scores.get("深南走势评分"), "深南数据源": stock_source,
        "PCB板块评分": scores.get("PCB板块评分"), "PCB数据日期": pcb_date, "PCB数据源": pcb_source,
        "主力净流入（亿元）": flow_amt, "主力净流入占比（%）": flow_pct,
        "资金流数据日期": flow_date, "大资金流评分": scores.get("大资金流评分"),
        "美股科技评分": scores.get("美股科技评分"), "美股数据日期": us_date,
        "基本面评分": scores.get("基本面评分"), "基本面说明": fundamental_note,
        "综合评分_v2": total, "数据覆盖率_v2": coverage,
        "数据源": "；".join(sources), "数据异常": "；".join(errors),
    }

    hourly = pd.read_csv(HOURLY_FILE) if HOURLY_FILE.exists() else pd.DataFrame()
    hourly = pd.concat([hourly, pd.DataFrame([row])], ignore_index=True)
    hourly = hourly.drop_duplicates(subset=["更新时间"], keep="last").sort_values("更新时间").tail(10000)
    hourly.to_csv(HOURLY_FILE, index=False, encoding="utf-8-sig")
    old = pd.read_csv(DAILY_FILE) if DAILY_FILE.exists() else pd.DataFrame()
    merge_daily(old, row).to_csv(DAILY_FILE, index=False, encoding="utf-8-sig")
    details = "；".join(f"{k}：{v:.1f}" for k, v in scores.items() if pd.notna(v))
    threshold_alert(total, stamp, details)
    print(f"Updated at {stamp}; score={total:.2f}; coverage={coverage:.0f}%")
    print("DATA_SOURCES:", " | ".join(sources))
    if errors:
        print("DATA_WARNINGS:", " | ".join(errors))
    if not scores or coverage < 80:
        print("WARNING 数据覆盖率低于80%，综合评分应谨慎参考。")


if __name__ == "__main__":
    main()
