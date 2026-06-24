from __future__ import annotations

import pandas as pd

# Nomes amigáveis (pt-BR) para exibição.
PATTERN_LABELS = {
    "doji": "Doji",
    "martelo": "Martelo",
    "estrela_cadente": "Estrela cadente",
    "engolfo_alta": "Engolfo de alta",
    "engolfo_baixa": "Engolfo de baixa",
    "harami_alta": "Harami de alta",
    "harami_baixa": "Harami de baixa",
    "tres_soldados_brancos": "Três soldados brancos",
    "tres_corvos_negros": "Três corvos negros",
}


def _candle(row: pd.Series) -> dict:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body = abs(c - o)
    rng = h - l
    return {
        "o": o, "h": h, "l": l, "c": c,
        "body": body,
        "range": rng,
        "upper": h - max(o, c),
        "lower": min(o, c) - l,
        "bull": c > o,
        "bear": c < o,
        "top": max(o, c),
        "bottom": min(o, c),
    }


def _empty_result() -> dict:
    return {
        "detected": [],
        "bullish": [],
        "bearish": [],
        "has_bullish": False,
        "has_bearish": False,
        "summary": "nenhum padrão relevante",
    }


def detect_candle_patterns(df: pd.DataFrame) -> dict:
    """Detecta padrões de candle nos últimos candles do DataFrame.

    Reconhece: doji, martelo, estrela cadente, engolfo de alta/baixa,
    harami de alta/baixa, três soldados brancos e três corvos negros.

    Retorna dict com listas de padrões e viés agregado (bullish/bearish).
    """
    clean = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    if len(clean) < 2:
        return _empty_result()

    last = _candle(clean.iloc[-1])
    prev = _candle(clean.iloc[-2])

    detected: list[str] = []
    bullish: list[str] = []
    bearish: list[str] = []

    def add(name: str, bias: str) -> None:
        if name not in detected:
            detected.append(name)
        if bias == "bull" and name not in bullish:
            bullish.append(name)
        elif bias == "bear" and name not in bearish:
            bearish.append(name)

    rng = last["range"]
    body = last["body"]

    # Doji — corpo muito pequeno em relação ao range (indecisão, neutro).
    if rng > 0 and body <= 0.10 * rng:
        add("doji", "neutral")

    # Martelo — corpo pequeno no topo, sombra inferior longa (reversão de alta).
    if body > 0 and last["lower"] >= 2 * body and last["upper"] <= body:
        add("martelo", "bull")

    # Estrela cadente — corpo pequeno na base, sombra superior longa (reversão de baixa).
    if body > 0 and last["upper"] >= 2 * body and last["lower"] <= body:
        add("estrela_cadente", "bear")

    # Engolfo de alta — candle anterior de baixa totalmente "engolido" por candle de alta.
    if prev["bear"] and last["bull"] and last["c"] >= prev["o"] and last["o"] <= prev["c"]:
        add("engolfo_alta", "bull")

    # Engolfo de baixa — candle anterior de alta engolido por candle de baixa.
    if prev["bull"] and last["bear"] and last["o"] >= prev["c"] and last["c"] <= prev["o"]:
        add("engolfo_baixa", "bear")

    # Harami de alta — corpo atual (de alta) contido no corpo anterior (de baixa).
    if prev["bear"] and last["bull"] and last["top"] <= prev["o"] and last["bottom"] >= prev["c"] and prev["body"] > last["body"]:
        add("harami_alta", "bull")

    # Harami de baixa — corpo atual (de baixa) contido no corpo anterior (de alta).
    if prev["bull"] and last["bear"] and last["top"] <= prev["c"] and last["bottom"] >= prev["o"] and prev["body"] > last["body"]:
        add("harami_baixa", "bear")

    # Padrões de 3 candles
    if len(clean) >= 3:
        c1 = _candle(clean.iloc[-3])
        c2 = _candle(clean.iloc[-2])
        c3 = _candle(clean.iloc[-1])

        # Três soldados brancos — 3 candles de alta com fechamentos crescentes.
        soldiers = (
            c1["bull"] and c2["bull"] and c3["bull"]
            and c2["c"] > c1["c"] and c3["c"] > c2["c"]
            and c2["o"] > c1["bottom"] and c2["o"] < c1["c"]
            and c3["o"] > c2["bottom"] and c3["o"] < c2["c"]
        )
        if soldiers:
            add("tres_soldados_brancos", "bull")

        # Três corvos negros — 3 candles de baixa com fechamentos decrescentes.
        crows = (
            c1["bear"] and c2["bear"] and c3["bear"]
            and c2["c"] < c1["c"] and c3["c"] < c2["c"]
            and c2["o"] < c1["top"] and c2["o"] > c1["c"]
            and c3["o"] < c2["top"] and c3["o"] > c2["c"]
        )
        if crows:
            add("tres_corvos_negros", "bear")

    labels = [PATTERN_LABELS.get(name, name) for name in detected]
    return {
        "detected": detected,
        "bullish": bullish,
        "bearish": bearish,
        "has_bullish": bool(bullish),
        "has_bearish": bool(bearish),
        "summary": "; ".join(labels) if labels else "nenhum padrão relevante",
    }
