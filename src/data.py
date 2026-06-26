from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import pandas as pd
import yfinance as yf

from .data_brapi import BrapiError, fetch_brapi_history

_BINANCE_URLS = [
    "https://api.binance.com/api/v3/klines",
    "https://api1.binance.com/api/v3/klines",
    "https://api2.binance.com/api/v3/klines",
    "https://api3.binance.com/api/v3/klines",
]


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


def _is_fracionario(symbol: str) -> bool:
    """Ação fracionária: 4 letras + dígito + F (ex.: PETR4F, BBSE3F, VALE3F)."""
    t = symbol.upper().replace(".SA", "")
    return bool(re.fullmatch(r"[A-Z]{4}\dF", t))


def _lot_ticker(symbol: str) -> str:
    """Ticker de lote equivalente ao fracionário. Ex: BBSE3F.SA → BBSE3.SA"""
    t = symbol.upper().replace(".SA", "")
    if t.endswith("F"):
        t = t[:-1]
    return f"{t}.SA"


def binance_meta() -> DataMeta:
    return DataMeta(fonte="Binance Spot", atualizado_em=_now_hms(), tipo_dado="tempo real", confiavel=True)


def get_usd_brl_rate() -> float:
    """Taxa de câmbio USD/BRL em tempo real. Tenta Yahoo Finance e Awesomeapi como fallback."""
    try:
        df = yf.Ticker("USDBRL=X").history(period="2d")
        if not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    try:
        r = requests.get(
            "https://economia.awesomeapi.com.br/json/last/USD-BRL",
            timeout=10,
        )
        r.raise_for_status()
        return float(r.json()["USDBRL"]["bid"])
    except Exception:
        return 5.70


_BINANCE_INTERVAL_MAP = {"1wk": "1w", "1mo": "1M"}


def _symbol_binance_to_yf(symbol: str) -> str:
    """Converte símbolo Binance para Yahoo Finance. Ex: BTCUSDT → BTC-USD"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "-USD"
    return symbol


def _parse_binance_raw(raw: list) -> pd.DataFrame:
    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)
    for col in ["open", "high", "low", "close", "volume", "quote_asset_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(None)
    return df[["datetime", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)


def fetch_binance_klines(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 500) -> pd.DataFrame:
    """Busca candles spot da Binance. Tenta múltiplos endpoints; levanta na primeira falha não-geo."""
    symbol = symbol.upper().replace("/", "")
    limit = max(50, min(int(limit), 1000))
    binance_interval = _BINANCE_INTERVAL_MAP.get(interval, interval)
    params = {"symbol": symbol, "interval": binance_interval, "limit": limit}
    last_exc: Exception | None = None
    for url in _BINANCE_URLS:
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return _parse_binance_raw(r.json())
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (403, 451, 503):
                raise  # 400 = símbolo/intervalo inválido — não adianta tentar outros mirrors
            last_exc = exc
        except requests.RequestException as exc:
            last_exc = exc
    raise last_exc  # type: ignore[misc]


_COINBASE_KEY_FILE = Path(__file__).resolve().parents[1] / "cdp_api_key.json"
_COINBASE_GRANULARITY = {"1d": "ONE_DAY", "1wk": "ONE_DAY", "1w": "ONE_DAY", "1mo": "ONE_DAY", "1M": "ONE_DAY"}


def _coinbase_client():
    """Cria cliente Coinbase REST a partir do cdp_api_key.json."""
    from coinbase.rest import RESTClient  # importação lazy — não quebra se não instalado
    if not _COINBASE_KEY_FILE.exists():
        raise DataError("cdp_api_key.json não encontrado na raiz do projeto.")
    with open(_COINBASE_KEY_FILE) as f:
        creds = json.load(f)
    return RESTClient(api_key=creds["name"], api_secret=creds["privateKey"])


def _symbol_binance_to_coinbase(symbol: str) -> str:
    """BTCUSDT → BTC-USD"""
    if symbol.endswith("USDT"):
        return symbol[:-4] + "-USD"
    return symbol


def fetch_coinbase_klines(symbol: str, interval: str = "1d", limit: int = 500) -> pd.DataFrame:
    """Busca candles da Coinbase Advanced Trade API com paginação automática."""
    client = _coinbase_client()
    product_id = _symbol_binance_to_coinbase(symbol.upper())
    granularity = _COINBASE_GRANULARITY.get(interval, "ONE_DAY")
    seconds_per_candle = 86400  # Coinbase granularidade máxima = ONE_DAY
    max_per_req = 300

    # Para semanal/mensal o resample reduz muito o nº de candles;
    # busca diários suficientes para garantir pelo menos 70 períodos após indicadores (SMA50 + buffer).
    if interval in ("1wk", "1w"):
        daily_limit = min((limit + 70) * 7, 3000)
    elif interval in ("1mo", "1M"):
        daily_limit = min((limit + 70) * 31, 3000)
    else:
        daily_limit = limit

    now = datetime.now(timezone.utc)
    all_rows: list[dict] = []
    current_end = now
    remaining = daily_limit

    while remaining > 0:
        batch = min(remaining, max_per_req)
        batch_start = current_end - timedelta(seconds=seconds_per_candle * batch)
        r = client.get_candles(
            product_id,
            start=str(int(batch_start.timestamp())),
            end=str(int(current_end.timestamp())),
            granularity=granularity,
        )
        candles = r.candles or []
        if not candles:
            break
        for c in candles:
            all_rows.append({
                "datetime": datetime.fromtimestamp(int(c.start), tz=timezone.utc).replace(tzinfo=None),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            })
        remaining -= len(candles)
        current_end = batch_start

    if not all_rows:
        raise DataError(f"Nenhum candle retornado pela Coinbase para {product_id}.")

    df = (
        pd.DataFrame(all_rows)
        .drop_duplicates("datetime")
        .sort_values("datetime")
        .reset_index(drop=True)
    )

    # Coinbase só tem granularidade diária — resample para semanal/mensal
    if interval in ("1wk", "1w"):
        df = df.set_index("datetime").resample("W").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna().reset_index()
    elif interval in ("1mo", "1M"):
        df = df.set_index("datetime").resample("ME").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna().reset_index()

    return df.tail(limit).reset_index(drop=True)


def fetch_crypto_history(symbol: str, interval: str = "1d", limit: int = 500) -> tuple[pd.DataFrame, DataMeta]:
    """Histórico de cripto em BRL com fallback em cascata: Binance → Coinbase → Yahoo Finance.

    Preços retornados já convertidos de USD para BRL usando taxa em tempo real.
    """
    sym = symbol.upper().replace("/", "")

    # 1. Binance
    try:
        df = fetch_binance_klines(sym, interval, limit)
        meta = binance_meta()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 400:
            raise DataError(f"Símbolo '{sym}' não reconhecido na Binance.") from exc
        df, meta = None, None
    except Exception:
        df, meta = None, None

    # 2. Coinbase (tempo real, sem geobloqueio)
    if df is None:
        try:
            df = fetch_coinbase_klines(sym, interval, limit)
            if len(df) >= 70:
                meta = DataMeta(fonte="Coinbase", atualizado_em=_now_hms(), tipo_dado="tempo real", confiavel=True)
            else:
                df = None
        except Exception:
            df = None

    # 3. Yahoo Finance (fallback final)
    if df is None:
        yf_sym = _symbol_binance_to_yf(sym)
        period = {"1d": "2y", "1wk": "5y", "1w": "5y", "1mo": "max", "1M": "max"}.get(interval, "2y")
        df = fetch_yfinance_history(yf_sym, period=period, interval=interval)
        meta = DataMeta(fonte="Yahoo Finance (crypto)", atualizado_em=_now_hms(), tipo_dado="delay", confiavel=False)

    # Converte USD → BRL em todas as colunas de preço
    usd_brl = get_usd_brl_rate()
    for col in ("open", "high", "low", "close"):
        df[col] = (df[col] * usd_brl).round(2)

    meta = DataMeta(
        fonte=meta.fonte,
        atualizado_em=meta.atualizado_em,
        tipo_dado=f"{meta.tipo_dado} · USD->BRL {usd_brl:.4f}",
        confiavel=meta.confiavel,
    )
    return df, meta


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

    Tenta: 1. Brapi -> 2. yfinance -> 3. lote-proxy (só para fracionários).
    Retorna (dataframe, DataMeta). Tickers sem sufixo recebem .SA automaticamente.
    Fracionários (PETR4F, BBSE3F…) pulam a Brapi, que não os indexa.
    """
    symbol = ticker.strip().upper()
    if not symbol.endswith(".SA"):
        symbol = f"{symbol}.SA"

    fracionario = _is_fracionario(symbol)

    # 1. Brapi (preferencial para lotes: dado confiável, ~15 min de atraso).
    #    Fracionários não constam na Brapi — pula direto para yfinance.
    if not fracionario:
        try:
            df = fetch_brapi_history(symbol, range=period, interval=interval)
            if not df.empty:
                return df, DataMeta(fonte="Brapi", atualizado_em=_now_hms(), tipo_dado="15 min delay", confiavel=True)
        except BrapiError:
            pass
        except Exception:
            pass

    # 2. yfinance (fallback geral ou primário para fracionários).
    yf_error: Exception | None = None
    try:
        df = fetch_yfinance_history(symbol, period=period, interval=interval)
        if not df.empty:
            tipo = "delay (fracionário)" if fracionario else "delay"
            return df, DataMeta(fonte="Yahoo Finance", atualizado_em=_now_hms(), tipo_dado=tipo, confiavel=False)
    except Exception as exc:
        yf_error = exc

    # 3. Para fracionários: usa o lote equivalente como proxy se yfinance falhar.
    if fracionario:
        lot = _lot_ticker(symbol)
        lot_code = lot.replace(".SA", "")
        try:
            df = fetch_yfinance_history(lot, period=period, interval=interval)
            if not df.empty:
                return df, DataMeta(
                    fonte=f"Yahoo Finance ({lot_code} proxy)",
                    atualizado_em=_now_hms(),
                    tipo_dado="delay (proxy lote)",
                    confiavel=False,
                )
        except Exception as exc:
            raise DataError(
                f"Não foi possível obter dados de {symbol} nem do lote {lot}: {exc}"
            ) from exc
        raise DataError(f"Nenhuma fonte retornou dados para {symbol}.")

    raise DataError(
        f"Não foi possível obter dados de {symbol} via Brapi nem yfinance: {yf_error}"
    ) from yf_error


def fetch_history(symbol: str, period: str = "1y", interval: str = "1d", limit: int = 500) -> tuple[pd.DataFrame, DataMeta]:
    """Dispatcher por tipo de ativo.

    - Tickers terminando em USDT  -> fetch_crypto_history (Binance → yfinance)
    - Demais (com/sem .SA)        -> fetch_b3_history (Brapi -> yfinance)
    """
    s = symbol.strip().upper()
    if s.endswith("USDT"):
        return fetch_crypto_history(symbol=s, interval=interval, limit=limit)
    return fetch_b3_history(s, period=period, interval=interval)
