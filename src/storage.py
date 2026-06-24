from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
WATCHLIST_FILE = DATA_DIR / "watchlists.json"
RADAR_HISTORY_FILE = DATA_DIR / "radar_history.csv"
POSITIONS_FILE = DATA_DIR / "positions.csv"
POSITIONS_V5_FILE = DATA_DIR / "positions_v5.csv"
DECISIONS_LOG_FILE = DATA_DIR / "decisions_log.json"

_DECISIONS_MAX = 200

POSITION_COLUMNS = [
    "ticker", "quantity", "avg_buy_price", "total_invested",
    "buy_date", "stop_price", "target_price", "notes",
]


@dataclass
class Position:
    """Posição ativa do operador (v5). Uma posição ativa por ticker."""

    ticker: str
    quantity: int
    avg_buy_price: float
    total_invested: float
    buy_date: str = ""
    stop_price: float | None = None
    target_price: float | None = None
    notes: str = ""

DEFAULT_WATCHLISTS: dict[str, list[str]] = {
    "Foco principal": ["PETR4.SA", "VALE3.SA", "ITUB4.SA", "BBAS3.SA", "BOVA11.SA"],
    "Radar amplo B3": [
        "PETR4.SA", "VALE3.SA", "ITUB4.SA", "BBAS3.SA", "BBDC4.SA", "WEGE3.SA",
        "ABEV3.SA", "BOVA11.SA", "SMAL11.SA", "RENT3.SA", "PRIO3.SA", "SUZB3.SA",
    ],
    "Cripto": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
}


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def normalize_ticker(ticker: str, b3: bool = True) -> str:
    t = ticker.strip().upper()
    if not t:
        return ""
    if b3 and "." not in t and not t.endswith("USDT"):
        t += ".SA"
    return t


def normalize_tickers(raw: str | list[str], b3: bool = True) -> list[str]:
    if isinstance(raw, list):
        pieces = raw
    else:
        pieces = raw.replace("\n", ",").replace(";", ",").split(",")
    out: list[str] = []
    for item in pieces:
        ticker = normalize_ticker(str(item), b3=b3)
        if ticker and ticker not in out:
            out.append(ticker)
    return out


def load_watchlists() -> dict[str, list[str]]:
    ensure_data_dir()
    if not WATCHLIST_FILE.exists():
        save_watchlists(DEFAULT_WATCHLISTS)
        return {k: v[:] for k, v in DEFAULT_WATCHLISTS.items()}
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("watchlists.json inválido")
        normalized: dict[str, list[str]] = {}
        for name, tickers in data.items():
            if isinstance(name, str) and isinstance(tickers, list):
                normalized[name] = normalize_tickers([str(t) for t in tickers], b3=False)
        return normalized or {k: v[:] for k, v in DEFAULT_WATCHLISTS.items()}
    except Exception:
        backup = WATCHLIST_FILE.with_suffix(".json.bak")
        WATCHLIST_FILE.replace(backup)
        save_watchlists(DEFAULT_WATCHLISTS)
        return {k: v[:] for k, v in DEFAULT_WATCHLISTS.items()}


def save_watchlists(watchlists: dict[str, list[str]]) -> None:
    ensure_data_dir()
    clean = {str(name).strip(): normalize_tickers(tickers, b3=False) for name, tickers in watchlists.items() if str(name).strip()}
    WATCHLIST_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_watchlist(name: str, tickers: list[str]) -> dict[str, list[str]]:
    watchlists = load_watchlists()
    clean_name = name.strip() or "Foco principal"
    watchlists[clean_name] = normalize_tickers(tickers, b3=False)
    save_watchlists(watchlists)
    return watchlists


def delete_watchlist(name: str) -> dict[str, list[str]]:
    watchlists = load_watchlists()
    watchlists.pop(name, None)
    if not watchlists:
        watchlists = {"Foco principal": []}
    save_watchlists(watchlists)
    return watchlists


def add_tickers_to_watchlist(name: str, tickers: list[str]) -> dict[str, list[str]]:
    watchlists = load_watchlists()
    existing = watchlists.get(name, [])
    merged = normalize_tickers(existing + tickers, b3=False)
    watchlists[name] = merged
    save_watchlists(watchlists)
    return watchlists


def save_radar_snapshot(radar: pd.DataFrame, profile: str, interval: str, period: str, source_list: str, top_n: int = 15) -> Path:
    ensure_data_dir()
    if radar.empty:
        raise ValueError("Radar vazio; nada para salvar.")
    snap = radar.copy().head(top_n)
    snap.insert(0, "Salvo em", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    snap.insert(1, "Perfil", profile)
    snap.insert(2, "Intervalo", interval)
    snap.insert(3, "Período", period)
    snap.insert(4, "Lista origem", source_list)
    write_header = not RADAR_HISTORY_FILE.exists()
    snap.to_csv(RADAR_HISTORY_FILE, mode="a", index=False, header=write_header, encoding="utf-8-sig")
    return RADAR_HISTORY_FILE


def load_radar_history() -> pd.DataFrame:
    ensure_data_dir()
    if not RADAR_HISTORY_FILE.exists():
        return pd.DataFrame()
    return pd.read_csv(RADAR_HISTORY_FILE, encoding="utf-8-sig")



def load_positions() -> pd.DataFrame:
    ensure_data_dir()
    if not POSITIONS_FILE.exists():
        return pd.DataFrame()
    return pd.read_csv(POSITIONS_FILE, encoding="utf-8-sig")


def save_position(row: dict[str, Any]) -> Path:
    ensure_data_dir()
    record = row.copy()
    record.setdefault("Criado em", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    df = pd.DataFrame([record])
    write_header = not POSITIONS_FILE.exists()
    df.to_csv(POSITIONS_FILE, mode="a", index=False, header=write_header, encoding="utf-8-sig")
    return POSITIONS_FILE


# ----------------------------------------------------------------------
# Posições ativas v5 (uma por ticker, em positions_v5.csv)
# ----------------------------------------------------------------------

def _coerce_float(value) -> float | None:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def load_positions_v5() -> pd.DataFrame:
    ensure_data_dir()
    if not POSITIONS_V5_FILE.exists():
        return pd.DataFrame(columns=POSITION_COLUMNS)
    df = pd.read_csv(POSITIONS_V5_FILE, encoding="utf-8-sig")
    for col in POSITION_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[POSITION_COLUMNS]


def _save_positions_v5(df: pd.DataFrame) -> Path:
    ensure_data_dir()
    df[POSITION_COLUMNS].to_csv(POSITIONS_V5_FILE, index=False, encoding="utf-8-sig")
    return POSITIONS_V5_FILE


def upsert_position_v5(pos: Position | dict[str, Any]) -> Path:
    """Insere ou atualiza a posição ativa do ticker (substitui a existente)."""
    record = asdict(pos) if isinstance(pos, Position) else dict(pos)
    record["ticker"] = str(record["ticker"]).strip().upper()
    df = load_positions_v5()
    df = df[df["ticker"].astype(str).str.upper() != record["ticker"]]
    df = pd.concat([df, pd.DataFrame([record])[POSITION_COLUMNS]], ignore_index=True)
    return _save_positions_v5(df)


def delete_position_v5(ticker: str) -> Path:
    tk = str(ticker).strip().upper()
    df = load_positions_v5()
    df = df[df["ticker"].astype(str).str.upper() != tk]
    return _save_positions_v5(df)


def log_decision(
    ticker: str,
    call: str,
    score: int,
    confidence: str,
    price: float,
    timeframe: str,
    trend_strength: str = "",
    commentary: str = "",
) -> None:
    """Registra uma análise/comentário em decisions_log.json (máx. 200 entradas)."""
    ensure_data_dir()
    try:
        entries: list[dict] = json.loads(DECISIONS_LOG_FILE.read_text(encoding="utf-8")) if DECISIONS_LOG_FILE.exists() else []
    except Exception:
        entries = []

    entries.append({
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": str(ticker).strip().upper(),
        "timeframe": timeframe,
        "call": call,
        "score": score,
        "confidence": confidence,
        "trend_strength": trend_strength,
        "price": round(float(price), 4),
        "commentary": str(commentary)[:300],
    })

    if len(entries) > _DECISIONS_MAX:
        entries = entries[-_DECISIONS_MAX:]

    DECISIONS_LOG_FILE.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_decisions_log(limit: int = 50) -> list[dict]:
    """Retorna as análises mais recentes do log (ordem decrescente)."""
    ensure_data_dir()
    if not DECISIONS_LOG_FILE.exists():
        return []
    try:
        entries = json.loads(DECISIONS_LOG_FILE.read_text(encoding="utf-8"))
        return list(reversed(entries))[:limit]
    except Exception:
        return []


def get_position_v5(ticker: str) -> Position | None:
    tk = str(ticker).strip().upper()
    df = load_positions_v5()
    match = df[df["ticker"].astype(str).str.upper() == tk]
    if match.empty:
        return None
    row = match.iloc[0]
    return Position(
        ticker=tk,
        quantity=int(_coerce_float(row["quantity"]) or 0),
        avg_buy_price=_coerce_float(row["avg_buy_price"]) or 0.0,
        total_invested=_coerce_float(row["total_invested"]) or 0.0,
        buy_date=str(row["buy_date"]) if pd.notna(row["buy_date"]) else "",
        stop_price=_coerce_float(row["stop_price"]),
        target_price=_coerce_float(row["target_price"]),
        notes=str(row["notes"]) if pd.notna(row["notes"]) else "",
    )
