from __future__ import annotations

import os

import pandas as pd
import requests

BRAPI_BASE_URL = "https://brapi.dev/api/quote"

# Header de navegador para evitar bloqueio em chamadas anônimas.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 TraderAI/5.0"
    ),
    "Accept": "application/json",
}
_TIMEOUT = 20
_OHLCV = ["open", "high", "low", "close", "volume"]


class BrapiError(RuntimeError):
    """Erro ao consultar a API da Brapi (HTTP, conexão ou resposta inválida)."""


def _normalize_ticker(ticker: str) -> str:
    """Brapi usa o ticker puro da B3, sem o sufixo .SA (ex.: PETR4, VALE3, BOVA11)."""
    t = ticker.strip().upper()
    if t.endswith(".SA"):
        t = t[:-3]
    return t


def _token_params() -> dict:
    """BRAPI_TOKEN é opcional. Sem token, usa o plano gratuito com limite de requisições."""
    token = os.getenv("BRAPI_TOKEN", "").strip()
    return {"token": token} if token else {}


def _request(ticker: str, params: dict) -> dict:
    """Faz a chamada à Brapi e devolve o primeiro item de `results`, com erros claros."""
    symbol = _normalize_ticker(ticker)
    url = f"{BRAPI_BASE_URL}/{symbol}"
    query = {**params, **_token_params()}
    try:
        resp = requests.get(url, params=query, headers=_HEADERS, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise BrapiError(f"Falha de conexão com a Brapi para {symbol}: {exc}") from exc

    if resp.status_code in (401, 403):
        raise BrapiError(
            f"Brapi exigiu autenticação ({resp.status_code}) para {symbol}. "
            "Configure BRAPI_TOKEN no .env."
        )
    if resp.status_code == 404:
        raise BrapiError(f"Ativo {symbol} não encontrado na Brapi (404).")
    if resp.status_code == 429:
        raise BrapiError(
            "Limite de requisições da Brapi atingido (429). "
            "Aguarde alguns instantes ou configure BRAPI_TOKEN no .env."
        )
    if resp.status_code >= 400:
        raise BrapiError(f"Brapi retornou erro HTTP {resp.status_code} para {symbol}.")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise BrapiError(f"Resposta inválida (não-JSON) da Brapi para {symbol}.") from exc

    results = payload.get("results") or []
    if not results:
        detail = payload.get("message") or payload.get("error") or "sem resultados"
        raise BrapiError(f"Brapi não retornou dados para {symbol}: {detail}.")
    return results[0]


def _history_to_df(raw_history: list | None) -> pd.DataFrame:
    """Converte `historicalDataPrice` da Brapi no formato padrão da v4."""
    columns = ["datetime", *_OHLCV]
    if not raw_history:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(raw_history)
    if "date" not in df.columns:
        return pd.DataFrame(columns=columns)

    # A Brapi retorna o campo `date` em epoch (segundos, UTC).
    df["datetime"] = pd.to_datetime(df["date"], unit="s", utc=True).dt.tz_convert(None)
    for col in _OHLCV:
        df[col] = pd.to_numeric(df.get(col), errors="coerce")

    out = (
        df[columns]
        .dropna(subset=["open", "high", "low", "close"])
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    return out


def fetch_brapi_quote(ticker: str) -> dict:
    """Busca cotação + histórico + fundamentos básicos de um ativo da B3 via Brapi.

    Retorna dict com: preço atual, variação no dia, volume, OHLCV histórico
    (DataFrame em "history") e o payload bruto em "raw".
    """
    result = _request(
        ticker,
        {
            "range": "1y",
            "interval": "1d",
            "fundamental": "true",
            "dividends": "false",
        },
    )
    return {
        "symbol": result.get("symbol"),
        "short_name": result.get("shortName"),
        "long_name": result.get("longName"),
        "price": result.get("regularMarketPrice"),
        "change": result.get("regularMarketChange"),
        "change_pct": result.get("regularMarketChangePercent"),
        "volume": result.get("regularMarketVolume"),
        "previous_close": result.get("regularMarketPreviousClose"),
        "day_high": result.get("regularMarketDayHigh"),
        "day_low": result.get("regularMarketDayLow"),
        "market_time": result.get("regularMarketTime"),
        "currency": result.get("currency"),
        "history": _history_to_df(result.get("historicalDataPrice")),
        "raw": result,
    }


def fetch_brapi_history(ticker: str, range: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Histórico OHLCV via Brapi no mesmo formato da v4: datetime, open, high, low, close, volume.

    Compatível 100% com `add_indicators()` sem modificação.
    """
    result = _request(
        ticker,
        {
            "range": range,
            "interval": interval,
            "fundamental": "false",
            "dividends": "false",
        },
    )
    df = _history_to_df(result.get("historicalDataPrice"))
    if df.empty:
        raise BrapiError(f"Brapi não retornou histórico para {_normalize_ticker(ticker)}.")
    return df
