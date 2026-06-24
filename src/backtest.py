from __future__ import annotations

import pandas as pd


def _compute_result(estrategia: str, data: pd.DataFrame, positions: pd.Series, fee: float) -> dict:
    """Calcula resultado padronizado (formato v5) a partir de um vetor de posições 0/1."""
    d = data.copy()
    d["_pos"] = positions.shift(1).fillna(0)
    d["_ret"] = d["close"].pct_change().fillna(0)
    d["_chg"] = d["_pos"].diff().abs().fillna(0)
    d["_strat"] = d["_pos"] * d["_ret"] - d["_chg"] * fee
    d["_equity"] = (1 + d["_strat"]).cumprod()
    d["_bh"] = (1 + d["_ret"]).cumprod()

    total_return_pct = round(float(d["_equity"].iloc[-1] - 1) * 100, 2)
    bh_return_pct = round(float(d["_bh"].iloc[-1] - 1) * 100, 2)
    max_dd_pct = round(float((d["_equity"] / d["_equity"].cummax() - 1).min()) * 100, 2)

    # Individual trade returns (entry/exit pairs)
    trade_returns: list[float] = []
    pos_arr = d["_pos"].values
    eq_arr = d["_equity"].values
    entry_eq = None
    for i in range(1, len(pos_arr)):
        if pos_arr[i] == 1 and pos_arr[i - 1] == 0:
            entry_eq = eq_arr[i]
        elif pos_arr[i] == 0 and pos_arr[i - 1] == 1 and entry_eq is not None:
            trade_returns.append((eq_arr[i] / entry_eq - 1) * 100)
            entry_eq = None
    if entry_eq is not None:  # posição aberta no final do período
        trade_returns.append((eq_arr[-1] / entry_eq - 1) * 100)

    n = len(trade_returns)
    n_win = sum(1 for r in trade_returns if r > 0)
    win_rate = round(n_win / n * 100, 1) if n > 0 else 0.0
    maior_ganho = round(max(trade_returns), 2) if trade_returns else 0.0
    maior_perda = round(min(trade_returns), 2) if trade_returns else 0.0

    # Sharpe simplificado (retornos não-nulos apenas)
    non_zero = d["_strat"][d["_strat"] != 0]
    if len(non_zero) > 20:
        mean_r = float(non_zero.mean()) * 252
        std_r = float(non_zero.std()) * (252 ** 0.5)
        sharpe = round(mean_r / std_r, 2) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    periodo = ""
    if "datetime" in d.columns:
        try:
            periodo = (
                pd.to_datetime(d["datetime"].iloc[0]).strftime("%Y-%m-%d")
                + " a "
                + pd.to_datetime(d["datetime"].iloc[-1]).strftime("%Y-%m-%d")
            )
        except Exception:
            pass

    ec = d[["datetime", "_equity", "_bh"]].rename(columns={"_equity": "equity", "_bh": "buy_hold"})

    return {
        "estrategia": estrategia,
        "periodo": periodo,
        "total_operacoes": n,
        "operacoes_ganhadoras": n_win,
        "win_rate_pct": win_rate,
        "retorno_total_pct": total_return_pct,
        "retorno_buy_hold_pct": bh_return_pct,
        "maior_ganho_pct": maior_ganho,
        "maior_perda_pct": maior_perda,
        "drawdown_maximo_pct": max_dd_pct,
        "sharpe_simplificado": sharpe,
        "equity_curve": ec,
    }


_EMA_CORE = ["ema12", "ema26", "close"]


def ema_crossover_backtest(df: pd.DataFrame, fee_bps: float = 5.0) -> dict:
    """Backtest: comprado quando EMA12 > EMA26; zerado quando EMA12 <= EMA26."""
    data = df.dropna(subset=_EMA_CORE).copy().reset_index(drop=True)
    if len(data) < 60:
        return {"error": "dados insuficientes"}
    fee = fee_bps / 10_000
    positions = (data["ema12"] > data["ema26"]).astype(int)
    result = _compute_result("EMA12 × EMA26 Crossover", data, positions, fee)
    # Aliases para callers v4 (analyze_one_ticker, aba Análise individual)
    result["total_return_pct"] = result["retorno_total_pct"]
    result["buy_hold_return_pct"] = result["retorno_buy_hold_pct"]
    result["max_drawdown_pct"] = result["drawdown_maximo_pct"]
    result["trades"] = int(positions.diff().abs().sum())
    return result


def macd_signal_backtest(df: pd.DataFrame, fee_bps: float = 5.0) -> dict:
    """Backtest: comprado quando MACD > linha de sinal; zerado no cruzamento para baixo."""
    data = df.dropna(subset=["macd", "macd_signal", "close"]).copy().reset_index(drop=True)
    if len(data) < 60:
        return {"error": "dados insuficientes"}
    fee = fee_bps / 10_000
    positions = (data["macd"] > data["macd_signal"]).astype(int)
    return _compute_result("MACD Signal Crossover", data, positions, fee)


def rsi_reversal_backtest(
    df: pd.DataFrame,
    buy_rsi: float = 35,
    sell_rsi: float = 65,
    fee_bps: float = 5.0,
) -> dict:
    """Backtest: compra quando RSI < buy_rsi; vende quando RSI > sell_rsi."""
    data = df.dropna(subset=["rsi14", "close"]).copy().reset_index(drop=True)
    if len(data) < 60:
        return {"error": "dados insuficientes"}
    fee = fee_bps / 10_000

    rsi = data["rsi14"].values
    pos = [0] * len(data)
    in_pos = False
    for i in range(len(data)):
        if not in_pos and rsi[i] <= buy_rsi:
            in_pos = True
        elif in_pos and rsi[i] >= sell_rsi:
            in_pos = False
        pos[i] = 1 if in_pos else 0

    return _compute_result(
        f"RSI Reversão (compra<{int(buy_rsi)} / venda>{int(sell_rsi)})",
        data,
        pd.Series(pos, index=data.index),
        fee,
    )


def bollinger_reversion_backtest(df: pd.DataFrame, fee_bps: float = 5.0) -> dict:
    """Backtest: compra quando preço toca banda inferior; vende na banda superior ou SMA20."""
    data = df.dropna(subset=["bb_lower", "bb_upper", "sma20", "close"]).copy().reset_index(drop=True)
    if len(data) < 60:
        return {"error": "dados insuficientes"}
    fee = fee_bps / 10_000

    close = data["close"].values
    bb_lower = data["bb_lower"].values
    bb_upper = data["bb_upper"].values
    sma20 = data["sma20"].values

    pos = [0] * len(data)
    in_pos = False
    for i in range(len(data)):
        if not in_pos and close[i] <= bb_lower[i]:
            in_pos = True
        elif in_pos and (close[i] >= bb_upper[i] or close[i] >= sma20[i]):
            in_pos = False
        pos[i] = 1 if in_pos else 0

    return _compute_result(
        "Bollinger Reversão à Média",
        data,
        pd.Series(pos, index=data.index),
        fee,
    )
