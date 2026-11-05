from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "quant_factor_history.csv"

WEIGHTS = {
    "价格动量因子": 20.0,
    "PCB相对强弱因子": 20.0,
    "成交量价格配合因子": 15.0,
    "资金流因子": 15.0,
    "波动风险因子": 15.0,
    "基本面因子": 15.0,
}

def _num(s):
    return pd.to_numeric(s, errors="coerce")

def _clip(s):
    return s.clip(0, 100)

def _linear_score(x, low, high):
    return _clip((x - low) / (high - low) * 100)

def _safe_pct(series, n):
    return _num(series).pct_change(n) * 100

def _fundamental_score():
    # 复用项目现有的人工基本面代理，不将其伪装成自动财报分析。
    try:
        import update_data
        score, note, available = update_data.read_fundamental_score()
        return float(score), str(note), bool(available)
    except Exception as exc:
        return 50.0, f"基本面代理不可用：{exc}", False

def build_factor_frame(stock, pcb, flow):
    stock_volume = getattr(stock, "attrs", {}).get("volume")
    stock = _num(stock).dropna().sort_index()
    pcb = _num(pcb).dropna().sort_index()
    if len(stock) < 65:
        raise RuntimeError(f"深南电路有效日线不足65个交易日（当前{len(stock)}）")
    if len(pcb) < 65:
        raise RuntimeError(f"PCB指数有效日线不足65个交易日（当前{len(pcb)}）")

    idx = stock.index.intersection(pcb.index)
    px = stock.reindex(idx).dropna()
    board = pcb.reindex(px.index)
    frame = pd.DataFrame(index=px.index)
    frame["深南收盘"] = px
    frame["PCB收盘"] = board

    # 1) 价格动量：5/20/60日收益率综合。
    r5 = _safe_pct(px, 5)
    r20 = _safe_pct(px, 20)
    r60 = _safe_pct(px, 60)
    frame["价格动量因子"] = (
        0.25 * _linear_score(r5, -15, 15)
        + 0.40 * _linear_score(r20, -25, 25)
        + 0.35 * _linear_score(r60, -40, 40)
    )

    # 2) PCB相对强弱：比较深南与PCB指数的同期收益差。
    rel20 = r20 - _safe_pct(board, 20)
    rel60 = r60 - _safe_pct(board, 60)
    frame["PCB相对强弱因子"] = (
        0.55 * _linear_score(rel20, -15, 15)
        + 0.45 * _linear_score(rel60, -25, 25)
    )

    # 3) 成交量与价格配合：上涨放量加分，下跌放量减分。
    vol = stock_volume
    if vol is not None:
        vol = _num(vol).reindex(frame.index)
        vol_ratio = vol / vol.rolling(20, min_periods=10).mean()
        volume_component = _linear_score(vol_ratio, 0.5, 2.0)
        direction_component = _linear_score(r5, -10, 10)
        # 量能只在方向明确时增强信号，避免单纯放量被视为利好。
        frame["成交量价格配合因子"] = (
            0.65 * direction_component
            + 0.35 * volume_component.where(r5 >= 0, 100 - volume_component)
        )
    else:
        frame["成交量价格配合因子"] = np.nan

    # 4) 资金流：使用可获得的历史主力净流入占比；缺失不填0。
    if flow is not None and not flow.empty:
        f = flow.copy()
        if "主力净流入占比（%）" in f.columns:
            flow_pct = _num(f["主力净流入占比（%）"])
        elif "主力净流入-净占比" in f.columns:
            flow_pct = _num(f["主力净流入-净占比"])
        else:
            flow_pct = pd.Series(dtype=float)
        flow_pct.index = pd.to_datetime(flow_pct.index, errors="coerce").normalize()
        flow_pct = flow_pct[~flow_pct.index.isna()]
        flow_pct = flow_pct.groupby(level=0).last()
        frame["资金流因子"] = _linear_score(flow_pct.reindex(frame.index), -5, 5)
    else:
        frame["资金流因子"] = np.nan

    # 5) 波动风险：波动越高、回撤越深，分数越低。
    daily_ret = px.pct_change() * 100
    vol20 = daily_ret.rolling(20, min_periods=15).std()
    peak60 = px.rolling(60, min_periods=30).max()
    drawdown = (px / peak60 - 1) * 100
    frame["波动风险因子"] = _clip(100 - vol20 * 5.0 - drawdown.abs() * 1.2)

    # 6) 基本面是当前人工代理值。只附加到最新日期，不倒灌到历史日期。
    fundamental, fundamental_note, fundamental_available = _fundamental_score()
    frame["基本面因子"] = np.nan
    if fundamental_available and len(frame):
        frame.loc[frame.index[-1], "基本面因子"] = fundamental
    frame["基本面说明"] = ""

    # 历史回测分只使用可逐日重建的前五项因子，避免把今天的人工基本面判断
    # 倒灌到过去；最新量化总分则在基本面信号可用时纳入第六项。
    historical_cols = list(WEIGHTS)[:5]
    hist_weight = sum(WEIGHTS[k] for k in historical_cols)
    hist_available = sum(_num(frame[k]).notna().astype(float) * WEIGHTS[k] for k in historical_cols)
    hist_sum = sum(_num(frame[k]).fillna(0) * WEIGHTS[k] for k in historical_cols)
    frame["历史可回测因子分"] = (hist_sum / hist_available.replace(0, np.nan)).clip(0, 100)
    frame["量化因子总分"] = frame["历史可回测因子分"]
    frame["数据覆盖率"] = hist_available / sum(WEIGHTS.values()) * 100

    if fundamental_available and len(frame):
        last_idx = frame.index[-1]
        base_weight = hist_available.loc[last_idx]
        denom = base_weight + WEIGHTS["基本面因子"]
        if denom > 0:
            numerator = hist_sum.loc[last_idx] + fundamental * WEIGHTS["基本面因子"]
            frame.loc[last_idx, "量化因子总分"] = float(numerator / denom)
            frame.loc[last_idx, "数据覆盖率"] = float(denom / sum(WEIGHTS.values()) * 100)

    # Forward returns are for historical evaluation only; they are never used in the same-day score.
    for horizon in (5, 20, 60):
        frame[f"未来{horizon}日收益率%"] = (px.shift(-horizon) / px - 1) * 100

    frame["未来5日上涨"] = frame["未来5日收益率%"] > 0
    frame["未来20日上涨"] = frame["未来20日收益率%"] > 0
    frame["未来60日上涨"] = frame["未来60日收益率%"] > 0
    return frame, fundamental_note

def calculate_and_save():
    import update_data

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stock, stock_source = update_data.get_stock_daily()
    pcb, pcb_source = update_data.get_pcb_daily()
    try:
        flow = update_data.get_fund_flow()
    except Exception:
        flow = None

    frame, fundamental_note = build_factor_frame(stock, pcb, flow)
    frame = frame.reset_index().rename(columns={"index": "日期", "时间": "日期"})
    if "日期" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "日期"})
    frame["日期"] = pd.to_datetime(frame["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    frame["深南数据源"] = stock_source
    frame["PCB数据源"] = pcb_source
    frame["基本面说明"] = fundamental_note
    frame.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    return frame

def backtest_summary(frame):
    rows = []
    bands = [
        ("0–44 偏弱", 0, 45),
        ("45–64 中性", 45, 65),
        ("65–79 偏强", 65, 80),
        ("80–100 强势", 80, 101),
    ]
    for label, low, high in bands:
        subset = frame[
            _num(frame["历史可回测因子分"]).ge(low)
            & _num(frame["历史可回测因子分"]).lt(high)
        ]
        for horizon in (5, 20, 60):
            col = f"未来{horizon}日收益率%"
            valid = _num(subset[col]).dropna()
            if len(valid):
                rows.append({
                    "评分区间": label,
                    "观察周期": f"{horizon}个交易日",
                    "有效样本数": int(len(valid)),
                    "平均收益率%": round(float(valid.mean()), 2),
                    "上涨比例%": round(float((valid > 0).mean() * 100), 1),
                    "中位数收益率%": round(float(valid.median()), 2),
                })
    return pd.DataFrame(rows)
