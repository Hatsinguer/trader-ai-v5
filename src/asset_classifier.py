from __future__ import annotations

import re

# ETFs conhecidos da B3 (terminam em 11, mas não são FIIs).
ASSET_TYPES = {
    "BOVA11": "ETF", "SMAL11": "ETF", "IVVB11": "ETF", "HASH11": "ETF",
    "SPXI11": "ETF", "DIVO11": "ETF", "GOLD11": "ETF", "BOVV11": "ETF",
    "XINA11": "ETF", "NASD11": "ETF", "FIND11": "ETF", "ECOO11": "ETF",
}

# Lista padrão de varredura do radar v5.
DEFAULT_B3_RADAR = [
    # Blue chips
    "PETR4.SA", "VALE3.SA", "ITUB4.SA", "BBAS3.SA", "BBDC4.SA",
    "WEGE3.SA", "ABEV3.SA", "RENT3.SA", "PRIO3.SA", "SUZB3.SA",
    "MGLU3.SA", "LREN3.SA", "HAPV3.SA", "RDOR3.SA", "GGBR4.SA",
    # ETFs
    "BOVA11.SA", "SMAL11.SA", "IVVB11.SA", "HASH11.SA",
    # FIIs representativos
    "MXRF11.SA", "KNRI11.SA", "HGLG11.SA", "VISC11.SA",
]


def _strip_suffix(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".SA"):
        t = t[:-3]
    return t


def classify_asset(ticker: str) -> str:
    """Classifica o tipo do ativo.

    Retorna: "ETF" | "FII" | "BDR" | "Ação ON" | "Ação PN" | "Cripto" | "Desconhecido"
    """
    raw = ticker.strip().upper()
    if raw.endswith("USDT"):
        return "Cripto"

    t = _strip_suffix(raw)
    if not t:
        return "Desconhecido"

    if t in ASSET_TYPES:
        return ASSET_TYPES[t]

    # Ativos terminados em 11: ETF conhecido (já tratado) ou, por heurística, FII.
    if t.endswith("11") or re.fullmatch(r"[A-Z]{4}11[A-Z]?", t):
        return "FII"

    # BDRs: 4 letras + dois dígitos iniciados por 3 (ex.: AAPL34, GOGL35, ROXO34).
    if re.fullmatch(r"[A-Z]{4}3[0-9][A-Z]?", t):
        return "BDR"

    # Fracionárias: 4 letras + dígito + F (ex.: PETR4F, BBSE3F, VALE3F).
    if re.fullmatch(r"[A-Z]{4}\dF", t):
        return "Ação Fracionária"

    # Ações: 4 letras + dígito final (3 = ON; 4/5/6 = PN).
    if re.fullmatch(r"[A-Z]{4}\d{1,2}", t):
        last = t[-1]
        if last == "3":
            return "Ação ON"
        if last in ("4", "5", "6"):
            return "Ação PN"

    if t.endswith("3"):
        return "Ação ON"
    if t.endswith("4"):
        return "Ação PN"

    return "Desconhecido"
