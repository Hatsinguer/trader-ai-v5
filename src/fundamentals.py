from __future__ import annotations

from .data_brapi import BrapiError, _request


def _num(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_pct(value) -> float | None:
    """Normaliza razões para percentual. Frações (|v|<=1.5) viram %, valores já em % ficam."""
    v = _num(value)
    if v is None:
        return None
    if abs(v) <= 1.5:
        return round(v * 100, 2)
    return round(v, 2)


def _debt_equity(value) -> float | None:
    """debtToEquity costuma vir em % (ex.: 40 = 0,4). Normaliza para razão."""
    v = _num(value)
    if v is None:
        return None
    return round(v / 100, 2) if v > 5 else round(v, 2)


def fetch_fundamentals(ticker: str) -> dict | None:
    """Busca dados fundamentalistas via Brapi (fundamental=true + módulos).

    Retorna dict com (quando disponíveis): pl, pvp, dy, roe, roic, ebitda_margin,
    net_margin, debt_equity, sector, subsector, market_cap. Retorna None se nada vier.
    """
    try:
        result = _request(
            ticker,
            {
                "fundamental": "true",
                "modules": "summaryProfile,defaultKeyStatistics,financialData",
            },
        )
    except BrapiError:
        return None
    except Exception:
        return None

    key_stats = result.get("defaultKeyStatistics") or {}
    financial = result.get("financialData") or {}
    profile = result.get("summaryProfile") or {}

    pl = _num(result.get("priceEarnings")) or _num(key_stats.get("trailingPE")) or _num(key_stats.get("forwardPE"))
    pvp = _num(key_stats.get("priceToBook"))
    dy = _as_pct(key_stats.get("dividendYield"))
    roe = _as_pct(financial.get("returnOnEquity"))
    roic = _as_pct(financial.get("returnOnAssets"))  # proxy quando ROIC não disponível
    ebitda_margin = _as_pct(financial.get("ebitdaMargins"))
    net_margin = _as_pct(financial.get("profitMargins"))
    debt_equity = _debt_equity(financial.get("debtToEquity"))
    sector = profile.get("sector")
    subsector = profile.get("industry")
    market_cap = _num(result.get("marketCap"))

    data = {
        "pl": pl,
        "pvp": pvp,
        "dy": dy,
        "roe": roe,
        "roic": roic,
        "ebitda_margin": ebitda_margin,
        "net_margin": net_margin,
        "debt_equity": debt_equity,
        "sector": sector,
        "subsector": subsector,
        "market_cap": market_cap,
    }

    # Se absolutamente nada veio, sinaliza ausência (provável limite do plano gratuito).
    if all(v is None for v in data.values()):
        return None
    return data


def quick_reading(fund: dict) -> str:
    """Leitura rápida determinística a partir de regras simples (briefing 6.2)."""
    notes: list[str] = []
    pl = fund.get("pl")
    pvp = fund.get("pvp")
    dy = fund.get("dy")
    roe = fund.get("roe")
    debt_equity = fund.get("debt_equity")

    if pl is not None and 0 < pl < 10:
        notes.append("valuation atrativo (P/L baixo)")
    if pvp is not None and pvp < 1:
        notes.append("abaixo do valor patrimonial (P/VP < 1)")
    if dy is not None and dy > 5:
        notes.append("dividend yield atrativo")
    if roe is not None and roe > 15:
        notes.append("boa rentabilidade (ROE alto)")
    if debt_equity is not None and debt_equity > 2:
        notes.append("alavancagem elevada (Dívida/PL > 2)")

    if not notes:
        return "Sem destaques fundamentalistas relevantes pelas regras simples."
    return "Leitura rápida: " + ", ".join(notes) + "."
