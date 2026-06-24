from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def stochastic_k(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Estocástico %K (rápido). %D é a média móvel simples de 3 períodos do %K."""
    lowest = low.rolling(period).min()
    highest = high.rolling(period).max()
    rng = (highest - lowest).replace(0, np.nan)
    return 100 * (close - lowest) / rng


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    """OBV — acumula volume conforme a direção do fechamento."""
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).fillna(0.0).cumsum()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Suavização de Wilder (usada no ADX/DI)."""
    return series.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _adx_components(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Retorna (adx, +DI, -DI) com suavização de Wilder."""
    up = high.diff()
    down = -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = _wilder(tr, period).replace(0, np.nan)
    plus_di = 100 * _wilder(plus_dm, period) / atr
    minus_di = 100 * _wilder(minus_dm, period) / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_series = _wilder(dx, period)
    return adx_series, plus_di, minus_di


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _adx_components(high, low, close, period)[0]


def directional_index_plus(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _adx_components(high, low, close, period)[1]


def directional_index_minus(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return _adx_components(high, low, close, period)[2]


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """VWAP cumulativo (preço típico ponderado por volume)."""
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum().replace(0, np.nan)
    return (typical * volume).cumsum() / cum_vol


def add_indicators(df: pd.DataFrame, interval: str = "1d") -> pd.DataFrame:
    """Adiciona os indicadores técnicos. Mantém todos os da v4 e acrescenta os da v5.

    VWAP só é calculado para timeframes intraday (interval != "1d"), com reset diário.
    """
    out = df.copy()

    # Médias (v4 + novas v5)
    out["sma20"] = out["close"].rolling(20).mean()
    out["sma50"] = out["close"].rolling(50).mean()
    out["sma200"] = out["close"].rolling(200).mean()
    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema21"] = out["close"].ewm(span=21, adjust=False).mean()
    out["ema12"] = out["close"].ewm(span=12, adjust=False).mean()
    out["ema26"] = out["close"].ewm(span=26, adjust=False).mean()

    # MACD
    out["macd"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()

    # RSI
    out["rsi14"] = rsi(out["close"], 14)

    # Bollinger
    mid = out["close"].rolling(20).mean()
    std = out["close"].rolling(20).std()
    out["bb_mid"] = mid
    out["bb_upper"] = mid + 2 * std
    out["bb_lower"] = mid - 2 * std

    # ATR
    high_low = out["high"] - out["low"]
    high_close = (out["high"] - out["close"].shift()).abs()
    low_close = (out["low"] - out["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()

    out["volume_sma20"] = out["volume"].rolling(20).mean()

    # --- Novos indicadores v5 ---
    out["stoch_k"] = stochastic_k(out["high"], out["low"], out["close"], 14)
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()
    out["obv"] = on_balance_volume(out["close"], out["volume"])

    adx_series, di_plus, di_minus = _adx_components(out["high"], out["low"], out["close"], 14)
    out["adx"] = adx_series
    out["di_plus"] = di_plus
    out["di_minus"] = di_minus

    # VWAP somente para intraday, com reset por dia de pregão
    if interval != "1d":
        typical = (out["high"] + out["low"] + out["close"]) / 3
        day = out["datetime"].dt.normalize()
        cum_pv = (typical * out["volume"]).groupby(day).cumsum()
        cum_vol = out["volume"].groupby(day).cumsum().replace(0, np.nan)
        out["vwap"] = cum_pv / cum_vol

    return out
