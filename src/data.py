from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import requests
import pandas as pd
import yfinance as yf

from .data_brapi import BrapiError, fetch_brapi_history

BINANCE_SPOT_URL = "https://api.binance.com/api/v3/klines"


class DataError(RuntimeError):
    """Nenhuma fonte de dados conseguiu atender à requisição."""


@dataclass
class DataMeta:
    """Metadados de confiabilidade do dado retornado, exibidos como badge na interface."""

    fonte: str           # "Brapi" | "Yahoo Finance" | "Binance Spot"
    atualizado_em: str   # "14:37:10" (HH:MM:SS)
    tipo_dado: str       # "15 min delay" | "tempo real" | "delay"
    confiavel: bool      # False para Yahoo Finance; True para Brapi e Binance Spot

    @property
    def label(self) -> str:
        """Rótulo completo para exibição, ex.: 'Brapi (15 min delay)'."""
        return f"{self.fonte} ({self.tipo_dado})"


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def binance_meta() -> DataMeta:
    return DataMeta(fonte="Binance Spot", atualizado_em=_now_hms(), tipo_dado="tempo real", confiavel=True)


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Busca candles spot públicos da Binance. Não exige API key."""
    symbol = symbol.upper().replace("/", "")
    limit = max(50, min(int(limit), 1000))
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    response = requests.get(BINANCE_SPOT_URL, params=params, timeout=15)
    response.raise_for_status()
    raw = response.json()
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)
    numeric_cols = ["open", "high", "low", "close", "volume", "quote_asset_volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    return df[["datetime", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)


def fetch_yfinance_history(symbol: str = "PETR4.SA", period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Busca histórico via yfinance. Para B3, normalmente use sufixo .SA: PETR4.SA, VALE3.SA, BOVA11.SA."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)
    if df.empty:
        raise ValueError(f"Nenhum dado retornado para {symbol}. Confira ticker, período e intervalo.")
    df = df.reset_index()
    date_col = "Datetime" if "Datetime" in df.columns else "Date"
    df = df.rename(columns={date_col: "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)
    return df[["datetime", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)


def fetch_b3_history(ticker: str, period: str = "1y", interval: str = "1d") -> tuple[pd.DataFrame, DataMeta]:
    """Histórico de ativo da B3 com estratégia de fallback.

    Tenta: 1. Brapi -> 2. yfinance. Se ambas falharem, levanta DataError.
    Retorna (dataframe, DataMeta). Tickers sem sufixo recebem .SA automaticamente.
    """
    symbol = ticker.strip().upper()
    if not symbol.endswith(".SA"):
        symbol = f"{symbol}.SA"

    # 1. Brapi (preferencial: dado confiável, ~15 min de atraso).
    try:
        df = fetch_brapi_history(symbol, range=period, interval=interval)
        if not df.empty:
            return df, DataMeta(fonte="Brapi", atualizado_em=_now_hms(), tipo_dado="15 min delay", confiavel=True)
    except BrapiError:
        pass  # fallback silencioso para yfinance
    except Exception:
        pass  # qualquer falha inesperada na Brapi não pode travar a ferramenta

    # 2. yfinance (fallback: menos confiável, marcado em amarelo na interface).
    try:
        df = fetch_yfinance_history(symbol, period=period, interval=interval)
        if not df.empty:
            return df, DataMeta(fonte="Yahoo Finance", atualizado_em=_now_hms(), tipo_dado="delay", confiavel=False)
    except Exception as exc:
        raise DataError(
            f"Não foi possível obter dados de {symbol} via Brapi nem yfinance: {exc}"
        ) from exc

    raise DataError(f"Nenhuma fonte retornou dados para {symbol}.")


def fetch_history(symbol: str, period: str = "1y", interval: str = "1d", limit: int = 500) -> tuple[pd.DataFrame, DataMeta]:
    """Dispatcher por tipo de ativo.

    - Tickers terminando em USDT  -> Binance Spot (tempo real)
    - Demais (com/sem .SA)        -> fetch_b3_history (Brapi -> yfinance)
    """
    s = symbol.strip().upper()
    if s.endswith("USDT"):
        df = fetch_binance_klines(symbol=s, interval=interval, limit=limit)
        return df, binance_meta()
    return fetch_b3_history(s, period=period, interval=interval)
