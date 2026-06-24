from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, field
from typing import Literal

import pandas as pd

from .patterns import detect_candle_patterns


@dataclass
class TechnicalSignal:
    signal: str
    score: int
    confidence: str
    thesis: str
    risks: list[str]
    invalidation: str
    snapshot: dict


@dataclass
class OpportunityCall:
    call: str
    score: int
    confidence: str
    technical_bias: str
    reference_price: float
    invalidation_price: float | None
    target_1_atr: float | None
    target_2_atr: float | None
    risk_reward_note: str
    # --- Novos campos v5 (default None/0 para preservar build_opportunity_call v4) ---
    buy_zone_low: float | None = None
    buy_zone_high: float | None = None
    scenario_conservative: float | None = None
    scenario_base: float | None = None
    scenario_optimistic: float | None = None
    horizon_pregoes: int = 20
    distance_from_sma20_pct: float = 0.0
    trend_strength: str = "neutra"
    risk_reward_ratio: float | None = None
    action_plan: str = ""
    justification_bullets: list[str] = field(default_factory=list)


RiskProfile = Literal["Conservador", "Moderado", "Agressivo"]

# Multiplicadores de ATR para stop por perfil de risco (compartilhado v4/v5).
PROFILE_ATR_STOP = {"Conservador": 1.2, "Moderado": 1.5, "Agressivo": 2.0}


# Colunas que o build_signal v4 realmente usa (evita que sma200/vwap derrubem o dropna).
_V4_CORE_COLS = ["sma20", "sma50", "ema12", "ema26", "macd", "macd_signal", "rsi14", "atr14", "volume_sma20"]


def build_signal(df: pd.DataFrame) -> TechnicalSignal:
    cols = [c for c in _V4_CORE_COLS if c in df.columns]
    clean = df.dropna(subset=cols).copy()
    if len(clean) < 60:
        raise ValueError("Histórico insuficiente após cálculo dos indicadores. Aumente o período/limite.")

    last = clean.iloc[-1]
    prev = clean.iloc[-2]
    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    if last["ema12"] > last["ema26"]:
        score += 1
        reasons.append("EMA12 acima da EMA26 indica viés de curto prazo positivo.")
    else:
        score -= 1
        reasons.append("EMA12 abaixo da EMA26 indica viés de curto prazo negativo.")

    if last["close"] > last["sma50"]:
        score += 1
        reasons.append("Preço acima da SMA50 sugere tendência intermediária favorável.")
    else:
        score -= 1
        reasons.append("Preço abaixo da SMA50 sugere tendência intermediária fraca.")

    if last["macd"] > last["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
        score += 2
        reasons.append("MACD cruzou para cima da linha de sinal no candle mais recente.")
    elif last["macd"] < last["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
        score -= 2
        reasons.append("MACD cruzou para baixo da linha de sinal no candle mais recente.")
    elif last["macd"] > last["macd_signal"]:
        score += 1
        reasons.append("MACD permanece acima da linha de sinal.")
    else:
        score -= 1
        reasons.append("MACD permanece abaixo da linha de sinal.")

    if last["rsi14"] < 30:
        score += 1
        reasons.append("RSI abaixo de 30 indica sobrevenda, mas não garante reversão.")
        risks.append("Ativo pode permanecer sobrevendido em tendência forte de baixa.")
    elif last["rsi14"] > 70:
        score -= 1
        reasons.append("RSI acima de 70 indica sobrecompra, pedindo cautela em compras tardias.")
        risks.append("Entrada comprada após esticamento aumenta risco de correção.")
    else:
        reasons.append("RSI em zona neutra, sem extremo técnico relevante.")

    if last["volume"] > last["volume_sma20"]:
        reasons.append("Volume acima da média de 20 períodos aumenta relevância do movimento recente.")
    else:
        risks.append("Volume abaixo da média reduz a qualidade da confirmação do movimento.")

    if score >= 3:
        signal = "COMPRA_TÉCNICA"
        confidence = "moderada" if score < 5 else "alta"
    elif score <= -3:
        signal = "VENDA_TÉCNICA"
        confidence = "moderada" if score > -5 else "alta"
    else:
        signal = "NEUTRO_AGUARDAR"
        confidence = "baixa"

    atr = float(last.get("atr14", 0) or 0)
    close = float(last["close"])
    if signal == "COMPRA_TÉCNICA":
        invalidation = f"Perda da região de {close - 1.5 * atr:,.2f}, estimada por 1,5x ATR abaixo do fechamento."
    elif signal == "VENDA_TÉCNICA":
        invalidation = f"Recuperação da região de {close + 1.5 * atr:,.2f}, estimada por 1,5x ATR acima do fechamento."
    else:
        invalidation = "Sem gatilho claro; aguardar rompimento de resistência ou perda de suporte com volume."

    snapshot = {
        "close": round(float(last["close"]), 6),
        "sma20": round(float(last["sma20"]), 6),
        "sma50": round(float(last["sma50"]), 6),
        "rsi14": round(float(last["rsi14"]), 2),
        "macd": round(float(last["macd"]), 6),
        "macd_signal": round(float(last["macd_signal"]), 6),
        "atr14": round(float(last["atr14"]), 6),
        "volume": round(float(last["volume"]), 2),
    }

    return TechnicalSignal(
        signal=signal,
        score=score,
        confidence=confidence,
        thesis=" ".join(reasons),
        risks=risks or ["Sinal baseado apenas em indicadores técnicos; notícias, liquidez e contexto macro podem invalidar a leitura."],
        invalidation=invalidation,
        snapshot=snapshot,
    )


def build_opportunity_call(signal: TechnicalSignal, profile: RiskProfile = "Agressivo") -> OpportunityCall:
    """Transforma indicadores em um call técnico educacional. Não é recomendação CVM."""
    close = float(signal.snapshot["close"])
    atr = float(signal.snapshot.get("atr14", 0) or 0)
    rsi = float(signal.snapshot.get("rsi14", 50) or 50)
    price_above_sma20 = close > float(signal.snapshot.get("sma20", close) or close)
    price_above_sma50 = close > float(signal.snapshot.get("sma50", close) or close)

    if profile == "Conservador":
        buy_threshold = 4
        monitor_threshold = 3
        atr_stop = 1.2
        atr_target_1 = 1.8
        atr_target_2 = 2.5
    elif profile == "Moderado":
        buy_threshold = 3
        monitor_threshold = 2
        atr_stop = 1.5
        atr_target_1 = 2.0
        atr_target_2 = 3.0
    else:  # Agressivo
        buy_threshold = 3
        monitor_threshold = 1
        atr_stop = 2.0
        atr_target_1 = 2.5
        atr_target_2 = 4.0

    if signal.score >= buy_threshold and price_above_sma50 and rsi < 72:
        call = "COMPRA_TÉCNICA_AGRESSIVA" if profile == "Agressivo" else "COMPRA_TÉCNICA"
        technical_bias = "favorável"
    elif signal.score >= monitor_threshold and (price_above_sma20 or price_above_sma50) and rsi < 75:
        call = "MONITORAR_COMPRA"
        technical_bias = "levemente favorável"
    elif signal.score <= -3:
        call = "EVITAR_COMPRA"
        technical_bias = "desfavorável"
    else:
        call = "AGUARDAR"
        technical_bias = "indefinido"

    invalidation_price = close - atr_stop * atr if call in {"COMPRA_TÉCNICA_AGRESSIVA", "COMPRA_TÉCNICA", "MONITORAR_COMPRA"} else None
    target_1 = close + atr_target_1 * atr if invalidation_price is not None else None
    target_2 = close + atr_target_2 * atr if invalidation_price is not None else None

    if invalidation_price is not None and atr > 0:
        risk_pct = (close - invalidation_price) / close * 100
        t1_pct = (target_1 - close) / close * 100 if target_1 is not None else 0
        risk_reward_note = f"Risco técnico aproximado: {risk_pct:.2f}% até invalidação; alvo técnico 1 por ATR: {t1_pct:.2f}%."
    else:
        risk_reward_note = "Sem estrutura técnica suficiente para estimar entrada, invalidação e alvo por ATR."

    return OpportunityCall(
        call=call,
        score=signal.score,
        confidence=signal.confidence,
        technical_bias=technical_bias,
        reference_price=round(close, 4),
        invalidation_price=round(invalidation_price, 4) if invalidation_price is not None else None,
        target_1_atr=round(target_1, 4) if target_1 is not None else None,
        target_2_atr=round(target_2, 4) if target_2 is not None else None,
        risk_reward_note=risk_reward_note,
    )


def ai_commentary(
    signal: TechnicalSignal,
    symbol: str,
    timeframe: str,
    position: dict | None = None,
) -> str:
    """Comentário de IA sobre o sinal técnico.

    Tenta Claude API (anthropic) → OpenAI (legado) → fallback determinístico.
    Só consome API quando ANTHROPIC_API_KEY ou OPENAI_API_KEY estiver configurada.
    """
    snap = {k: v for k, v in signal.snapshot.items() if k in ("close", "rsi14", "macd", "atr14", "sma20", "adx")}

    fallback = (
        f"Sinal: {signal.signal} | Confiança: {signal.confidence} | Score: {signal.score}.\n\n"
        f"Leitura técnica: {signal.thesis}\n\n"
        f"Riscos: {'; '.join(signal.risks)}\n\n"
        f"Invalidação: {signal.invalidation}\n\n"
        "Observação: isto é uma análise técnica automatizada, não é recomendação de investimento."
    )

    position_ctx = ""
    if position:
        avg = float(position.get("avg_buy_price") or 0)
        price = float(snap.get("close") or 0)
        pnl_pct = (price - avg) / avg * 100 if avg else 0.0
        stop = position.get("stop_price") or "não definido"
        notes = position.get("notes") or ""
        position_ctx = (
            f"\n\nContexto da posição ativa:\n"
            f"- Preço médio de entrada: R$ {avg:.2f}\n"
            f"- Resultado atual: {pnl_pct:+.1f}%\n"
            f"- Stop definido: {stop}\n"
            + (f"- Anotações: {notes}\n" if notes else "")
        )

    prompt = (
        f"Você é um analista técnico conservador. Produza comentário objetivo em português do Brasil sobre o ativo {symbol}.\n\n"
        "Regras:\n"
        "- Máximo de 250 palavras\n"
        "- Não dê ordens diretas de compra ou venda\n"
        "- Não prometa retorno financeiro\n"
        "- Comente o cenário técnico, os riscos e o que observar no próximo pregão\n"
        "- Se houver contexto de posição, comente brevemente a situação do operador\n"
        "- Finalize com um disclaimer breve de que é análise técnica para uso pessoal\n\n"
        f"Ativo: {symbol}\n"
        f"Timeframe: {timeframe}\n"
        f"Indicadores: {json.dumps(snap, ensure_ascii=False)}\n"
        f"Sinal: {signal.signal} | Score: {signal.score} | Confiança: {signal.confidence}\n"
        f"Tese: {signal.thesis}\n"
        f"Invalidação: {signal.invalidation}"
        f"{position_ctx}"
    )

    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic as _ant
            client = _ant.Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as exc:
            return fallback + f"\n\n[Claude API indisponível: {exc}]"

    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            response = client.chat.completions.create(
                model=model,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content
        except Exception as exc:
            return fallback + f"\n\n[OpenAI indisponível: {exc}]"

    return fallback


# ======================================================================
# MOTOR DECISÓRIO v5
# ======================================================================

# Colunas usadas pelo score v5 (sma200/vwap ficam de fora do dropna por serem opcionais).
_V5_CORE_COLS = [
    "sma20", "sma50", "ema9", "ema21", "ema12", "ema26",
    "macd", "macd_signal", "rsi14", "atr14", "volume_sma20",
    "adx", "di_plus", "di_minus", "stoch_k", "stoch_d", "bb_upper", "bb_lower",
]

CALLS_V5 = ["COMPRA_FORTE", "COMPRA_TÉCNICA", "MONITORAR_COMPRA", "AGUARDAR", "EVITAR_COMPRA", "VENDA_TÉCNICA"]


def position_action(position, opp: OpportunityCall, current_price: float) -> str:
    """Conduta sugerida para uma posição já aberta (briefing 7.3)."""
    avg = float(getattr(position, "avg_buy_price", 0) or 0)
    if avg <= 0:
        return "MANTER"
    pnl_pct = (current_price - avg) / avg * 100

    stop_ref = getattr(position, "stop_price", None)
    if stop_ref is None:
        stop_ref = opp.invalidation_price

    if stop_ref is not None and current_price <= stop_ref:
        return "VENDER — stop atingido"
    if opp.call in ("VENDA_TÉCNICA", "EVITAR_COMPRA"):
        return "AVALIAR SAÍDA — sinal técnico deteriorou"
    if pnl_pct > 15 and opp.distance_from_sma20_pct > 8:
        return "REALIZAR PARCIAL — lucro elevado, ativo esticado"
    if pnl_pct > 0 and opp.call in ("COMPRA_TÉCNICA", "COMPRA_FORTE"):
        return "MANTER COM STOP AJUSTADO"
    if pnl_pct < -5 and opp.call == "AGUARDAR":
        return "MONITORAR — prejuízo moderado, aguardar reversão"
    return "MANTER"


def calculate_radar_score(opp: "OpportunityCall") -> float:
    """Score composto 0–100 para ranking do radar (técnico + R:R + confiança - esticamento)."""
    base = opp.score * 5
    rr_bonus = min(opp.risk_reward_ratio * 10, 30) if opp.risk_reward_ratio else 0
    confidence_mult = {"alta": 1.2, "moderada": 1.0, "baixa": 0.7}.get(opp.confidence, 1.0)
    distance_penalty = max(0.0, opp.distance_from_sma20_pct - 5) * 2  # penaliza ativo esticado
    return max(0.0, (base + rr_bonus) * confidence_mult - distance_penalty)


def classify_v5(score: int) -> str:
    """Classifica o call técnico a partir do score expandido (tabela do briefing v5)."""
    if score >= 8:
        return "COMPRA_FORTE"
    if score >= 5:
        return "COMPRA_TÉCNICA"
    if score >= 3:
        return "MONITORAR_COMPRA"
    if score >= -2:
        return "AGUARDAR"
    if score >= -5:
        return "EVITAR_COMPRA"
    return "VENDA_TÉCNICA"


def _confidence_v5(score: int) -> str:
    mag = abs(score)
    if mag >= 8:
        return "alta"
    if mag >= 5:
        return "moderada"
    return "baixa"


def _trend_strength(snap: dict) -> str:
    """Classifica força/direção da tendência por ADX + alinhamento de médias/DI."""
    close = snap["close"]
    sma50 = snap.get("sma50", close)
    ema12 = snap.get("ema12", close)
    ema26 = snap.get("ema26", close)
    adx_val = snap.get("adx", 0.0) or 0.0
    di_plus = snap.get("di_plus", 0.0) or 0.0
    di_minus = snap.get("di_minus", 0.0) or 0.0

    bullish = close > sma50 and ema12 > ema26 and di_plus >= di_minus
    bearish = close < sma50 and ema12 < ema26 and di_minus > di_plus

    if adx_val >= 25:
        if bullish:
            return "forte alta"
        if bearish:
            return "forte baixa"
        return "neutra"
    if adx_val >= 20:
        if bullish:
            return "moderada alta"
        if bearish:
            return "moderada baixa"
        return "neutra"
    return "neutra"


def build_signal_v5(df: pd.DataFrame) -> TechnicalSignal:
    """Score técnico expandido v5 (ver tabela de critérios no briefing).

    Espera um DataFrame já processado por add_indicators() (v5).
    """
    cols = [c for c in _V5_CORE_COLS if c in df.columns]
    clean = df.dropna(subset=cols).copy()
    if len(clean) < 20:
        raise ValueError("Histórico insuficiente após cálculo dos indicadores v5. Aumente o período.")

    last = clean.iloc[-1]
    prev = clean.iloc[-2]
    patterns = detect_candle_patterns(df)

    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    def has(col: str) -> bool:
        return col in last and pd.notna(last[col])

    # Médias de curto/médio/longo prazo
    if has("ema9") and has("ema21") and last["ema9"] > last["ema21"]:
        score += 1
        reasons.append("EMA9 acima da EMA21 (curto prazo positivo).")
    if last["ema12"] > last["ema26"]:
        score += 1
        reasons.append("EMA12 acima da EMA26 (tendência curta confirmada).")
    if last["close"] > last["sma50"]:
        score += 1
        reasons.append("Preço acima da SMA50 (médio prazo favorável).")
    if has("sma200"):
        if last["close"] > last["sma200"]:
            score += 2
            reasons.append("Preço acima da SMA200 (longo prazo favorável).")
        else:
            reasons.append("Preço abaixo da SMA200 (longo prazo ainda desfavorável).")
            risks.append("Abaixo da SMA200: tendência primária de baixa não revertida.")

    # MACD
    if last["macd"] > last["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
        score += 2
        reasons.append("MACD cruzou acima da linha de sinal (gatilho de entrada).")
    elif last["macd"] > last["macd_signal"]:
        score += 1
        reasons.append("MACD acima da linha de sinal (tendência confirmada).")
    elif last["macd"] < last["macd_signal"] and prev["macd"] >= prev["macd_signal"]:
        reasons.append("MACD cruzou abaixo da linha de sinal (perda de força).")
        risks.append("Cruzamento negativo recente do MACD.")

    # RSI
    rsi_val = float(last["rsi14"])
    if rsi_val < 30:
        score += 1
        reasons.append(f"RSI em {rsi_val:.0f} (sobrevenda, potencial reversão).")
        risks.append("Sobrevenda pode persistir em tendência de baixa forte.")
    elif 40 <= rsi_val <= 60:
        score += 1
        reasons.append(f"RSI em {rsi_val:.0f} (zona neutra saudável).")
    elif rsi_val > 70:
        score -= 2
        reasons.append(f"RSI em {rsi_val:.0f} (sobrecompra, penaliza compra).")
        risks.append("Entrada esticada com RSI sobrecomprado.")

    # Volume
    if last["volume"] > last["volume_sma20"]:
        score += 1
        reasons.append("Volume acima da média de 20 períodos (confirmação).")
    else:
        risks.append("Volume abaixo da média reduz a qualidade da confirmação.")

    # ADX (força de tendência)
    if has("adx") and last["adx"] > 25:
        score += 1
        reasons.append(f"ADX em {float(last['adx']):.0f} (tendência forte).")

    # Estocástico
    if has("stoch_k") and has("stoch_d") and last["stoch_k"] > last["stoch_d"] and prev["stoch_k"] <= prev["stoch_d"]:
        score += 1
        reasons.append("Estocástico %K cruzou %D para cima (confirmação).")

    # Bollinger
    if has("bb_lower") and last["close"] < last["bb_lower"]:
        score += 1
        reasons.append("Preço abaixo da banda inferior de Bollinger (esticado para baixo).")
    elif has("bb_upper") and last["close"] > last["bb_upper"]:
        score -= 1
        reasons.append("Preço acima da banda superior de Bollinger (esticado para cima).")
        risks.append("Preço fora da banda superior tende a corrigir.")

    # Padrões de candle
    if patterns["has_bullish"]:
        score += 2
        reasons.append(f"Padrão de reversão de alta detectado: {patterns['summary']}.")
    if patterns["has_bearish"]:
        score -= 2
        reasons.append(f"Padrão de reversão de baixa detectado: {patterns['summary']}.")
        risks.append("Padrão de candle de baixa nos últimos pregões.")

    call = classify_v5(score)

    def s(col: str, default=0.0):
        return round(float(last[col]), 6) if has(col) else default

    snapshot = {
        "close": round(float(last["close"]), 6),
        "sma20": s("sma20"),
        "sma50": s("sma50"),
        "sma200": s("sma200", None),
        "ema9": s("ema9"),
        "ema21": s("ema21"),
        "ema12": s("ema12"),
        "ema26": s("ema26"),
        "macd": s("macd"),
        "macd_signal": s("macd_signal"),
        "rsi14": round(rsi_val, 2),
        "atr14": s("atr14"),
        "adx": round(s("adx"), 2),
        "di_plus": round(s("di_plus"), 2),
        "di_minus": round(s("di_minus"), 2),
        "stoch_k": round(s("stoch_k"), 2),
        "stoch_d": round(s("stoch_d"), 2),
        "bb_upper": s("bb_upper"),
        "bb_lower": s("bb_lower"),
        "volume": round(float(last["volume"]), 2),
        "volume_sma20": s("volume_sma20"),
        "patterns": patterns["summary"],
        "reasons": reasons,
    }

    confidence = _confidence_v5(score)
    return TechnicalSignal(
        signal=call,
        score=score,
        confidence=confidence,
        thesis=" ".join(reasons) if reasons else "Sem critérios técnicos relevantes acionados.",
        risks=risks or ["Sinal baseado apenas em indicadores técnicos; notícias, liquidez e macro podem invalidar a leitura."],
        invalidation="",  # detalhado em build_opportunity_call_v5
        snapshot=snapshot,
    )


def build_opportunity_call_v5(signal: TechnicalSignal, profile: RiskProfile = "Moderado") -> OpportunityCall:
    """Constrói o call de oportunidade expandido v5 (zona de compra, cenários, plano)."""
    snap = signal.snapshot
    close = float(snap["close"])
    atr = float(snap.get("atr14", 0) or 0)
    sma20 = float(snap.get("sma20", close) or close)
    score = signal.score
    call = classify_v5(score)

    bullish_call = call in {"COMPRA_FORTE", "COMPRA_TÉCNICA", "MONITORAR_COMPRA"}
    bias_map = {
        "COMPRA_FORTE": "fortemente favorável",
        "COMPRA_TÉCNICA": "favorável",
        "MONITORAR_COMPRA": "levemente favorável",
        "AGUARDAR": "indefinido",
        "EVITAR_COMPRA": "desfavorável",
        "VENDA_TÉCNICA": "fortemente desfavorável",
    }
    technical_bias = bias_map.get(call, "indefinido")

    # Zona de compra (pullback ideal entre SMA20 e SMA20 - 0,5*ATR)
    buy_zone_high = sma20
    buy_zone_low = sma20 - 0.5 * atr

    distance_from_sma20_pct = (close - sma20) / sma20 * 100 if sma20 else 0.0
    trend_strength = _trend_strength(snap)

    # Cenários de projeção por múltiplos de ATR
    scenario_conservative = close + 1.0 * atr
    scenario_base = close + 2.0 * atr
    scenario_optimistic = close + 4.0 * atr

    # Stop técnico por perfil
    atr_stop = PROFILE_ATR_STOP.get(profile, 1.5)
    invalidation_price = close - atr_stop * atr if atr > 0 else None
    target_1 = scenario_base
    target_2 = scenario_optimistic

    if invalidation_price is not None and close > invalidation_price:
        risk = close - invalidation_price
        reward = target_1 - close
        risk_reward_ratio = round(reward / risk, 2) if risk > 0 else None
    else:
        risk_reward_ratio = None

    if risk_reward_ratio:
        risk_reward_note = (
            f"Risco técnico ~{(close - invalidation_price) / close * 100:.2f}% até invalidação; "
            f"relação risco:retorno aproximada 1:{risk_reward_ratio}."
        )
    else:
        risk_reward_note = "Sem estrutura técnica suficiente para estimar risco:retorno por ATR."

    # Plano de ação
    plano: list[str] = []
    if bullish_call:
        plano.append(f"Comprar se: preço recuar para R$ {buy_zone_low:,.2f}–{buy_zone_high:,.2f} (zona de pullback).")
        bb_upper = snap.get("bb_upper")
        if bb_upper:
            plano.append(f"Comprar se: romper R$ {float(bb_upper):,.2f} com volume acima da média.")
    elif call == "AGUARDAR":
        plano.append(f"Aguardar: entrada melhor na zona R$ {buy_zone_low:,.2f}–{buy_zone_high:,.2f} ou rompimento confirmado.")
    else:
        plano.append("Evitar novas compras: viés técnico desfavorável no momento.")
    if invalidation_price is not None:
        plano.append(f"Evitar/sair se: perder R$ {invalidation_price:,.2f} (stop técnico).")
    plano.append(f"Realizar se: atingir R$ {target_1:,.2f} (alvo 1) ou R$ {target_2:,.2f} (alvo 2).")
    action_plan = "\n".join(plano)

    justification_bullets = list(snap.get("reasons", []))[:6]

    return OpportunityCall(
        call=call,
        score=score,
        confidence=signal.confidence,
        technical_bias=technical_bias,
        reference_price=round(close, 4),
        invalidation_price=round(invalidation_price, 4) if invalidation_price is not None else None,
        target_1_atr=round(target_1, 4) if atr > 0 else None,
        target_2_atr=round(target_2, 4) if atr > 0 else None,
        risk_reward_note=risk_reward_note,
        buy_zone_low=round(buy_zone_low, 4),
        buy_zone_high=round(buy_zone_high, 4),
        scenario_conservative=round(scenario_conservative, 4),
        scenario_base=round(scenario_base, 4),
        scenario_optimistic=round(scenario_optimistic, 4),
        horizon_pregoes=20,
        distance_from_sma20_pct=round(distance_from_sma20_pct, 2),
        trend_strength=trend_strength,
        risk_reward_ratio=risk_reward_ratio,
        action_plan=action_plan,
        justification_bullets=justification_bullets,
    )
