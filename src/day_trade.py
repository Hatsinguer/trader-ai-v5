"""
Mesa Day Trade + Special Skill Mjolnir (interno)
=================================================
Módulo independente que consome o ecossistema v5 sem modificar nenhuma função central.

Seções:
  1. Dataclasses e constantes da Mesa
  2. Funções utilitárias da Mesa (config, robôs, simulação, calor)
  3. MJOLNIR — Special Skill de Scan / Filtragem
  4. render_mesa_day_trade_tab() — layout Streamlit
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
try:
    from zoneinfo import ZoneInfo
    _TZ_BR = ZoneInfo("America/Sao_Paulo")
except ImportError:
    from dateutil import tz as _dtz
    _TZ_BR = _dtz.gettz("America/Sao_Paulo")  # type: ignore[assignment]

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ------------------------------------------------------------------
# Imports do ecossistema v5 (sem modificar nenhuma dessas funções)
# ------------------------------------------------------------------
from src.data import fetch_history
from src.indicators import add_indicators
from src.costs import FeeParams, buy_total_cost, sell_simulation
from src.market_clocks import render_market_clocks, is_b3_open

_MESA_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "mesa_config.json"


# ============================================================
# 1. DATACLASSES E CONSTANTES
# ============================================================

STATUS_VALIDOS = [
    "AGUARDAR",
    "OBSERVAR",
    "SIMULAR",
    "GATILHO ATIVO",
    "BLOQUEADO",
    "SOMENTE PLANEJAMENTO",
    "AGUARDAR ROMPIMENTO",
    "AGUARDAR PULLBACK",
    "OBSERVAR REVERSÃO",
    "AGUARDAR CONFIRMAÇÃO",
    "ENTRADA CONFIRMADA",
    "SINAL INVALIDADO",
    "EXPIRADO",
]

ROBOS_DISPONIVEIS = ["Conservador", "Rompimento", "Pullback", "Reversão"]

TICKERS_FUTUROS = {"WINFUT", "DOLFUT", "WIN", "DOL", "IND", "BGI", "CCM", "ICF"}


@dataclass
class MesaAtivo:
    ticker: str
    preco_atual: Optional[float] = None
    preco_manual: Optional[float] = None
    calor: int = 0
    estrategia: str = ""
    gatilho: str = ""
    entrada_sugerida: Optional[float] = None
    stop_sugerido: Optional[float] = None
    alvo_sugerido: Optional[float] = None
    quantidade_simulada: int = 0
    lucro_bruto: Optional[float] = None
    custos_estimados: Optional[float] = None
    ir_estimado: Optional[float] = None
    lucro_liquido: Optional[float] = None
    status: str = "AGUARDAR"
    fonte_preco: str = ""
    atualizado_em: str = ""
    mjolnir_score: Optional[int] = None
    mjolnir_classificacao: Optional[str] = None
    mjolnir_status: Optional[str] = None
    mjolnir_alerta: Optional[str] = None
    mjolnir_entrada: Optional[float] = None
    mjolnir_stop: Optional[float] = None
    mjolnir_alvo: Optional[float] = None
    mjolnir_fatores_ok: list = field(default_factory=list)
    mjolnir_fatores_atencao: list = field(default_factory=list)


# ============================================================
# 2. FUNÇÕES DA MESA DAY TRADE
# ============================================================

def load_mesa_config() -> dict:
    """Carrega data/mesa_config.json; cria com defaults se ausente."""
    if _MESA_CONFIG_PATH.exists():
        try:
            with open(_MESA_CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "ativos": ["PETR4.SA", "VALE3.SA", "ITUB4.SA", "BOVA11.SA", "WEGE3.SA"],
        "capital_por_operacao": 1000.0,
        "robo_ativo": "Conservador",
        "slippage_pct": 0.1,
        "corretagem": 4.90,
        "precos_manuais": {},
        "ultima_atualizacao": None,
        "mjolnir": {
            "enabled": True,
            "timeframe": "5m",
            "min_score": 80,
            "min_volume_ratio": 1.5,
            "min_rr": 2.0,
            "max_stop_reais": 1.50,
            "stop_buffer_atr_mult": 0.1,
            "entry_buffer_reais": 0.01,
            "region_tolerance_atr_mult": 0.3,
            "confirmation_mode": "fechamento",
            "expiration_candles_by_timeframe": {"1m": 3, "3m": 3, "5m": 5, "15m": 3},
            "last_scan": [],
        },
    }


def save_mesa_config(config: dict) -> None:
    """Salva data/mesa_config.json."""
    _MESA_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MESA_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def atualizar_preco_manual(ticker: str, preco: Optional[float], config: dict) -> dict:
    """Atualiza preço manual no dicionário de configuração (não salva automaticamente)."""
    if "precos_manuais" not in config:
        config["precos_manuais"] = {}
    if preco is None:
        config["precos_manuais"].pop(ticker, None)
    else:
        config["precos_manuais"][ticker] = preco
    return config


def _eh_futuro(ticker: str) -> bool:
    t = ticker.upper().replace(".SA", "")
    return any(t.startswith(f) for f in TICKERS_FUTUROS) or t in TICKERS_FUTUROS


def calcular_calor(opp, gatilho: str, preco_atual: float) -> int:
    """
    Retorna nota de 0 a 100.
    Não é sinal de compra — representa proximidade técnica de uma oportunidade.
    """
    if opp is None:
        return 0
    try:
        score_base = getattr(opp, "score", 0) or 0
        max_score = 17
        score_norm = min(int(score_base) / max_score, 1.0)

        rr = getattr(opp, "risk_reward_ratio", None)
        rr_bonus = 0.0
        if rr and float(rr) >= 2.0:
            rr_bonus = 0.15
        elif rr and float(rr) >= 1.5:
            rr_bonus = 0.08

        gatilho_bonus = 0.10 if "ATIVO" in gatilho.upper() else 0.0

        calor = int(min((score_norm * 0.75 + rr_bonus + gatilho_bonus) * 100, 100))
        return max(calor, 0)
    except Exception:
        return 0


def simular_operacao_dt(
    preco_entrada: float,
    preco_alvo: float,
    preco_stop: float,
    capital_disponivel: float,
    fees: FeeParams,
    slippage_pct: float = 0.1,
) -> dict:
    """Simula operação day trade completa com custos, slippage, IR (20%) e lucro líquido."""
    if preco_entrada <= 0 or preco_alvo <= preco_entrada or preco_stop >= preco_entrada:
        return {"erro": "Parâmetros inválidos: entrada, alvo ou stop inconsistentes."}

    slippage_val = preco_entrada * (slippage_pct / 100.0)
    preco_entrada_real = preco_entrada + slippage_val

    if preco_entrada_real <= 0:
        return {"erro": "Preço de entrada inválido após slippage."}

    quantidade = max(1, int(capital_disponivel / preco_entrada_real))
    financeiro_compra = quantidade * preco_entrada_real

    fees_dt = FeeParams(
        buy_brokerage=fees.buy_brokerage,
        sell_brokerage=fees.sell_brokerage,
        b3_rate_pct=fees.b3_rate_pct,
        xp_operational_rate_pct=fees.xp_operational_rate_pct,
        ir_rate_pct=20.0,  # IR day trade fixo 20%
    )

    compra = buy_total_cost(quantidade, preco_entrada_real, fees_dt)
    venda = sell_simulation(quantidade, preco_alvo, compra["total_investido"], fees_dt)

    risco_por_acao = preco_entrada_real - preco_stop
    retorno_por_acao = preco_alvo - preco_entrada_real
    rr = round(retorno_por_acao / risco_por_acao, 2) if risco_por_acao > 0 else 0.0

    lucro_liq = venda["lucro_liquido_estimado"]
    bloqueado = lucro_liq <= 0

    return {
        "quantidade": quantidade,
        "preco_entrada_real": round(preco_entrada_real, 4),
        "preco_alvo": round(preco_alvo, 4),
        "preco_stop": round(preco_stop, 4),
        "financeiro_compra": round(financeiro_compra, 2),
        "total_investido": compra["total_investido"],
        "lucro_bruto": round(quantidade * (preco_alvo - preco_entrada_real), 2),
        "custos_totais": round(compra["total_custos"] + venda["total_custos"], 2),
        "ir_estimado": venda["ir_estimado"],
        "lucro_liquido": lucro_liq,
        "rentabilidade_pct": venda["rentabilidade_liquida_pct"],
        "rr_tecnico": rr,
        "rr_real": round(venda["lucro_liquido_estimado"] / (risco_por_acao * quantidade), 2) if risco_por_acao > 0 and quantidade > 0 else 0.0,
        "bloqueado": bloqueado,
        "slippage_estimado": round(slippage_val * quantidade, 2),
        "corretagem_total": round(fees_dt.buy_brokerage + fees_dt.sell_brokerage, 2),
        "venda_bruta": venda["venda_bruta"],
    }


# ---------- Robôs de simulação ----------

def _build_fees(config: dict) -> FeeParams:
    return FeeParams(
        buy_brokerage=float(config.get("corretagem", 4.90)),
        sell_brokerage=float(config.get("corretagem", 4.90)),
        b3_rate_pct=0.0230,
        xp_operational_rate_pct=0.0,
        ir_rate_pct=20.0,
    )


def robo_rompimento(df: pd.DataFrame, opp, ticker: str, capital: float, fees: FeeParams, slippage_pct: float = 0.1) -> MesaAtivo:
    """Detecta ativo próximo de romper resistência ou máxima recente."""
    ativo = MesaAtivo(ticker=ticker, estrategia="Rompimento")
    try:
        last = df.iloc[-1]
        preco = float(last["close"])
        atr = float(df["atr14"].iloc[-1]) if "atr14" in df.columns else preco * 0.01

        max_20 = float(df["high"].tail(20).max())
        entrada = round(max_20 * 1.002, 2)
        stop = round(preco - atr, 2)
        alvo = round(entrada + 2 * (entrada - stop), 2)

        distancia_pct = (max_20 - preco) / preco * 100 if preco > 0 else 99
        ativo.gatilho = f"Próx. resistência {max_20:.2f} · dist {distancia_pct:.1f}%"

        if distancia_pct > 3.0:
            ativo.status = "AGUARDAR"
            ativo.calor = max(0, 40 - int(distancia_pct * 5))
        else:
            ativo.status = "GATILHO ATIVO" if distancia_pct < 0.5 else "OBSERVAR"
            ativo.calor = 70 + int((3.0 - distancia_pct) * 10)
            ativo.entrada_sugerida = entrada
            ativo.stop_sugerido = stop
            ativo.alvo_sugerido = alvo
            sim = simular_operacao_dt(entrada, alvo, stop, capital, fees, slippage_pct)
            if "erro" not in sim:
                ativo.quantidade_simulada = sim["quantidade"]
                ativo.lucro_bruto = sim["lucro_bruto"]
                ativo.custos_estimados = sim["custos_totais"]
                ativo.ir_estimado = sim["ir_estimado"]
                ativo.lucro_liquido = sim["lucro_liquido"]
                if sim["bloqueado"]:
                    ativo.status = "BLOQUEADO"
    except Exception:
        ativo.status = "AGUARDAR"
    return ativo


def robo_pullback(df: pd.DataFrame, opp, ticker: str, capital: float, fees: FeeParams, slippage_pct: float = 0.1) -> MesaAtivo:
    """Detecta ativo em tendência positiva retornando para zona técnica."""
    ativo = MesaAtivo(ticker=ticker, estrategia="Pullback")
    try:
        last = df.iloc[-1]
        preco = float(last["close"])
        sma20 = float(df["sma20"].iloc[-1]) if "sma20" in df.columns else preco
        atr = float(df["atr14"].iloc[-1]) if "atr14" in df.columns else preco * 0.01

        zona_pullback = sma20 - atr * 0.5
        distancia_zona = (preco - zona_pullback) / preco * 100

        tendencia_ok = False
        if "sma20" in df.columns and "sma50" in df.columns:
            sma50 = float(df["sma50"].iloc[-1])
            tendencia_ok = sma20 > sma50 and preco > sma20

        ativo.gatilho = f"Pullback zona SMA20 {sma20:.2f} · ATR {atr:.2f}"

        if not tendencia_ok:
            ativo.status = "AGUARDAR"
            ativo.calor = 20
        elif distancia_zona > 3.0:
            ativo.status = "AGUARDAR PULLBACK"
            ativo.calor = 40
        elif distancia_zona <= 1.0:
            entrada = round(preco, 2)
            stop = round(zona_pullback - atr * 0.5, 2)
            alvo = round(entrada + 2 * (entrada - stop), 2)
            ativo.status = "OBSERVAR"
            ativo.calor = min(75, 55 + int((3.0 - distancia_zona) * 10))
            ativo.entrada_sugerida = entrada
            ativo.stop_sugerido = stop
            ativo.alvo_sugerido = alvo
            sim = simular_operacao_dt(entrada, alvo, stop, capital, fees, slippage_pct)
            if "erro" not in sim:
                ativo.quantidade_simulada = sim["quantidade"]
                ativo.lucro_bruto = sim["lucro_bruto"]
                ativo.custos_estimados = sim["custos_totais"]
                ativo.ir_estimado = sim["ir_estimado"]
                ativo.lucro_liquido = sim["lucro_liquido"]
                if sim["bloqueado"]:
                    ativo.status = "BLOQUEADO"
        else:
            ativo.status = "AGUARDAR PULLBACK"
            ativo.calor = 50
    except Exception:
        ativo.status = "AGUARDAR"
    return ativo


def robo_reversao(df: pd.DataFrame, opp, ticker: str, capital: float, fees: FeeParams, slippage_pct: float = 0.1) -> MesaAtivo:
    """Detecta ativo próximo de suporte relevante com possível reversão."""
    ativo = MesaAtivo(ticker=ticker, estrategia="Reversão")
    try:
        last = df.iloc[-1]
        preco = float(last["close"])
        atr = float(df["atr14"].iloc[-1]) if "atr14" in df.columns else preco * 0.01
        min_20 = float(df["low"].tail(20).min())

        distancia_suporte = (preco - min_20) / preco * 100

        rsi_val = float(df["rsi14"].iloc[-1]) if "rsi14" in df.columns else 50
        rsi_ok = rsi_val < 40

        ativo.gatilho = f"Suporte {min_20:.2f} · RSI {rsi_val:.0f} · dist {distancia_suporte:.1f}%"

        if distancia_suporte > 5.0 or not rsi_ok:
            ativo.status = "OBSERVAR REVERSÃO"
            ativo.calor = 25
        elif distancia_suporte <= 2.0 and rsi_ok:
            entrada = round(preco, 2)
            stop = round(min_20 - atr * 0.3, 2)
            alvo = round(entrada + 2 * (entrada - stop), 2)
            ativo.status = "AGUARDAR CONFIRMAÇÃO"
            ativo.calor = 55
            ativo.entrada_sugerida = entrada
            ativo.stop_sugerido = stop
            ativo.alvo_sugerido = alvo
            sim = simular_operacao_dt(entrada, alvo, stop, capital, fees, slippage_pct)
            if "erro" not in sim:
                ativo.quantidade_simulada = sim["quantidade"]
                ativo.lucro_bruto = sim["lucro_bruto"]
                ativo.custos_estimados = sim["custos_totais"]
                ativo.ir_estimado = sim["ir_estimado"]
                ativo.lucro_liquido = sim["lucro_liquido"]
                if sim["bloqueado"]:
                    ativo.status = "BLOQUEADO"
        else:
            ativo.status = "OBSERVAR REVERSÃO"
            ativo.calor = 35
    except Exception:
        ativo.status = "AGUARDAR"
    return ativo


def robo_conservador(df: pd.DataFrame, opp, ticker: str, capital: float, fees: FeeParams, slippage_pct: float = 0.1) -> MesaAtivo:
    """Ativa apenas quando houver confluência de múltiplos fatores favoráveis."""
    ativo = MesaAtivo(ticker=ticker, estrategia="Conservador")
    try:
        last = df.iloc[-1]
        preco = float(last["close"])
        atr = float(df["atr14"].iloc[-1]) if "atr14" in df.columns else preco * 0.01
        rsi_val = float(df["rsi14"].iloc[-1]) if "rsi14" in df.columns else 50
        adx_val = float(df["adx"].iloc[-1]) if "adx" in df.columns else 0

        sma20 = float(df["sma20"].iloc[-1]) if "sma20" in df.columns else preco
        sma50 = float(df["sma50"].iloc[-1]) if "sma50" in df.columns else preco
        di_plus = float(df["di_plus"].iloc[-1]) if "di_plus" in df.columns else 0
        di_minus = float(df["di_minus"].iloc[-1]) if "di_minus" in df.columns else 0

        pontos = 0
        if preco > sma20:
            pontos += 1
        if sma20 > sma50:
            pontos += 1
        if 40 < rsi_val < 70:
            pontos += 1
        if adx_val > 20:
            pontos += 1
        if di_plus > di_minus:
            pontos += 1

        ativo.gatilho = f"Confluência {pontos}/5 · ADX {adx_val:.0f} · RSI {rsi_val:.0f}"
        ativo.calor = pontos * 18

        if pontos >= 4:
            entrada = round(preco + atr * 0.1, 2)
            stop = round(preco - atr, 2)
            alvo = round(entrada + 2 * (entrada - stop), 2)
            ativo.status = "OBSERVAR"
            ativo.entrada_sugerida = entrada
            ativo.stop_sugerido = stop
            ativo.alvo_sugerido = alvo
            sim = simular_operacao_dt(entrada, alvo, stop, capital, fees, slippage_pct)
            if "erro" not in sim:
                ativo.quantidade_simulada = sim["quantidade"]
                ativo.lucro_bruto = sim["lucro_bruto"]
                ativo.custos_estimados = sim["custos_totais"]
                ativo.ir_estimado = sim["ir_estimado"]
                ativo.lucro_liquido = sim["lucro_liquido"]
                if sim["bloqueado"]:
                    ativo.status = "BLOQUEADO"
        else:
            ativo.status = "AGUARDAR"
    except Exception:
        ativo.status = "AGUARDAR"
    return ativo


def _rodar_robo(robo_nome: str, df: pd.DataFrame, opp, ticker: str, capital: float, fees: FeeParams, slippage_pct: float) -> MesaAtivo:
    if robo_nome == "Rompimento":
        return robo_rompimento(df, opp, ticker, capital, fees, slippage_pct)
    elif robo_nome == "Pullback":
        return robo_pullback(df, opp, ticker, capital, fees, slippage_pct)
    elif robo_nome == "Reversão":
        return robo_reversao(df, opp, ticker, capital, fees, slippage_pct)
    else:
        return robo_conservador(df, opp, ticker, capital, fees, slippage_pct)


# ============================================================
# MJOLNIR — SPECIAL SKILL DE SCAN / FILTRAGEM
# ============================================================

_MJ_MIN_ROWS = 220  # mínimo de candles para indicadores mj_ estáveis


@st.cache_data(ttl=60)
def load_mjolnir_intraday_data(ticker: str, timeframe: str) -> tuple[pd.DataFrame, dict]:
    """Busca dados intraday do Mjolnir com cache de 60 s."""
    return prepare_mjolnir_dataframe(ticker, timeframe, {})


def prepare_mjolnir_dataframe(ticker: str, timeframe: str, config: dict) -> tuple[pd.DataFrame, dict]:
    """
    Busca dados intraday via fetch_history(), aplica limpeza, remove candle em formação
    e prepara dataframe específico para o Mjolnir.
    """
    meta_out: dict = {"fonte": "", "atualizado_em": "", "erro": None}
    try:
        # Mapeia timeframe Mjolnir → parâmetros do fetch_history
        tf_map = {
            "1m": ("2d", "1m"),
            "3m": ("5d", "5m"),   # yfinance não tem 3m; usa 5m como proxy
            "5m": ("5d", "5m"),
            "15m": ("10d", "15m"),
            "30m": ("20d", "30m"),
            "1h": ("30d", "1h"),
        }
        period, interval = tf_map.get(timeframe, ("5d", "5m"))

        # yfinance exige sufixo .SA para B3
        sym = ticker.strip().upper()
        if not sym.endswith(("USDT", ".SA")):
            sym = f"{sym}.SA"

        df, meta = fetch_history(sym, period=period, interval=interval, limit=800)
        meta_out["fonte"] = meta.fonte
        meta_out["atualizado_em"] = meta.atualizado_em

        if df.empty:
            meta_out["erro"] = "DataFrame vazio retornado pela fonte."
            return pd.DataFrame(), meta_out

        # Garante colunas obrigatórias
        required_cols = {"open", "high", "low", "close", "volume", "datetime"}
        if not required_cols.issubset(set(df.columns)):
            meta_out["erro"] = f"Colunas ausentes: {required_cols - set(df.columns)}"
            return pd.DataFrame(), meta_out

        df = df.copy().reset_index(drop=True)
        df["datetime"] = pd.to_datetime(df["datetime"])

        # Remove candle em formação (último candle cujo tempo ainda não fechou)
        now = pd.Timestamp.now()
        tf_minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}
        tf_min = tf_minutes.get(timeframe, 5)
        last_dt = df["datetime"].iloc[-1]
        elapsed = (now - last_dt).total_seconds() / 60.0
        if elapsed < tf_min:
            df = df.iloc[:-1].reset_index(drop=True)

        if len(df) < _MJ_MIN_ROWS:
            meta_out["erro"] = f"Dados insuficientes: {len(df)} candles fechados (mínimo {_MJ_MIN_ROWS})."
            return pd.DataFrame(), meta_out

        return df, meta_out

    except Exception as exc:
        meta_out["erro"] = str(exc)
        return pd.DataFrame(), meta_out


def validate_mjolnir_data_contract(df: pd.DataFrame, timeframe: str) -> tuple[bool, list[str]]:
    """
    Verifica se os dados mínimos do Mjolnir existem e são compatíveis com o Scan.
    Retorna (ok, lista_de_erros).
    """
    erros: list[str] = []

    if df.empty:
        return False, ["DataFrame vazio."]

    if len(df) < _MJ_MIN_ROWS:
        erros.append(f"Candles insuficientes: {len(df)} < {_MJ_MIN_ROWS}")

    # Verifica se indicadores mj_ existem
    indicadores_criticos = [
        "mj_ema9", "mj_ema20", "mj_ema200",
        "mj_rsi14", "mj_atr14", "mj_adx14",
        "mj_di_plus", "mj_di_minus",
        "mj_volume_sma20", "mj_vwap",
    ]
    for col in indicadores_criticos:
        if col not in df.columns:
            erros.append(f"Coluna ausente: {col}")
        elif df[col].isna().all():
            erros.append(f"Coluna toda NaN: {col}")

    # Verifica defasagem: último datetime deve ser recente
    if "datetime" in df.columns:
        last_dt = pd.to_datetime(df["datetime"].iloc[-1])
        agora = pd.Timestamp.now()
        tf_minutes = {"1m": 5, "3m": 15, "5m": 30, "15m": 90, "30m": 120, "1h": 240}
        tolerancia = tf_minutes.get(timeframe, 60)
        elapsed_min = (agora - last_dt).total_seconds() / 60.0
        if elapsed_min > tolerancia:
            erros.append(f"Dados defasados: último candle há {elapsed_min:.0f} min (tolerância {tolerancia} min).")

    return len(erros) == 0, erros


def _wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def calculate_mjolnir_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula indicadores próprios do Mjolnir com prefixo mj_.
    Não sobrescreve indicadores gerais do app.
    """
    out = df.copy()

    # EMAs
    out["mj_ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["mj_ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["mj_ema21"] = out["close"].ewm(span=21, adjust=False).mean()
    out["mj_ema200"] = out["close"].ewm(span=200, adjust=False).mean()

    # RSI 14
    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["mj_rsi14"] = 100 - (100 / (1 + rs))

    # ATR 14
    h_l = out["high"] - out["low"]
    h_pc = (out["high"] - out["close"].shift()).abs()
    l_pc = (out["low"] - out["close"].shift()).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    out["mj_atr14"] = tr.rolling(14).mean()

    # ADX 14 + DI+/DI-
    up = out["high"].diff()
    down = -out["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=out.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=out.index)
    atr_s = _wilder_smooth(tr, 14).replace(0, np.nan)
    out["mj_di_plus"] = 100 * _wilder_smooth(plus_dm, 14) / atr_s
    out["mj_di_minus"] = 100 * _wilder_smooth(minus_dm, 14) / atr_s
    dx = 100 * (out["mj_di_plus"] - out["mj_di_minus"]).abs() / (out["mj_di_plus"] + out["mj_di_minus"]).replace(0, np.nan)
    out["mj_adx14"] = _wilder_smooth(dx, 14)

    # Volume SMA20
    out["mj_volume_sma20"] = out["volume"].rolling(20).mean()

    # VWAP intraday com reset diário
    if "datetime" in out.columns:
        typical = (out["high"] + out["low"] + out["close"]) / 3
        day = pd.to_datetime(out["datetime"]).dt.normalize()
        cum_pv = (typical * out["volume"]).groupby(day).cumsum()
        cum_vol = out["volume"].groupby(day).cumsum().replace(0, np.nan)
        out["mj_vwap"] = cum_pv / cum_vol
    else:
        typical = (out["high"] + out["low"] + out["close"]) / 3
        cum_vol = out["volume"].cumsum().replace(0, np.nan)
        out["mj_vwap"] = (typical * out["volume"]).cumsum() / cum_vol

    return out


def detect_mjolnir_hammer(df: pd.DataFrame, candle_idx: int, config: dict) -> dict:
    """
    Detecta Martelo Positivo qualificado em candle fechado.
    Retorna dict com is_hammer, motivos e métricas do candle.
    """
    resultado = {"is_hammer": False, "motivos_ok": [], "motivos_fail": [], "metricas": {}}
    try:
        row = df.iloc[candle_idx]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])

        amplitude = h - l
        if amplitude <= 0:
            resultado["motivos_fail"].append("Amplitude zero.")
            return resultado

        body = abs(c - o)
        corpo_min = o if c > o else c
        corpo_max = c if c > o else o
        pavio_inf = corpo_min - l
        pavio_sup = h - corpo_max

        body_pct = body / amplitude
        close_position_pct = (c - l) / amplitude * 100
        body_position_pct = (corpo_min - l) / amplitude * 100

        atr = float(df["mj_atr14"].iloc[candle_idx]) if "mj_atr14" in df.columns else amplitude
        tick_size = config.get("tick_size", None)

        metricas = {
            "open": o, "high": h, "low": l, "close": c,
            "amplitude": round(amplitude, 4),
            "body": round(body, 4),
            "pavio_inf": round(pavio_inf, 4),
            "pavio_sup": round(pavio_sup, 4),
            "body_pct": round(body_pct * 100, 1),
            "close_position_pct": round(close_position_pct, 1),
            "body_position_pct": round(body_position_pct, 1),
            "candle_positivo": c >= o,
        }
        resultado["metricas"] = metricas

        # Critérios objetivos
        body_min_required = max(tick_size, amplitude * 0.05) if tick_size else amplitude * 0.05

        if body < body_min_required:
            resultado["motivos_fail"].append(f"Corpo muito pequeno ({body:.4f} < {body_min_required:.4f}).")
        else:
            resultado["motivos_ok"].append("Corpo mínimo OK.")

        if body_pct > 0.35:
            resultado["motivos_fail"].append(f"Corpo muito largo ({body_pct*100:.1f}% > 35%).")
        else:
            resultado["motivos_ok"].append("Corpo estreito OK.")

        if pavio_inf < 2 * body:
            resultado["motivos_fail"].append(f"Pavio inferior curto ({pavio_inf:.4f} < 2x corpo {2*body:.4f}).")
        else:
            resultado["motivos_ok"].append("Pavio inferior longo OK.")

        if pavio_sup > 0.30 * body and body > 0:
            resultado["motivos_fail"].append(f"Pavio superior longo ({pavio_sup:.4f} > 30% corpo).")
        else:
            resultado["motivos_ok"].append("Pavio superior pequeno OK.")

        if close_position_pct < 67:
            resultado["motivos_fail"].append(f"Fechamento baixo no candle ({close_position_pct:.1f}% < 67%).")
        else:
            resultado["motivos_ok"].append("Fechamento no terço superior OK.")

        if body_position_pct < 60:
            resultado["motivos_fail"].append(f"Corpo fora do terço superior ({body_position_pct:.1f}% < 60%).")
        else:
            resultado["motivos_ok"].append("Corpo no terço superior operacional OK.")

        # Queda anterior
        queda_ok = False
        if candle_idx >= 5:
            ref_close = float(df["close"].iloc[candle_idx - 3])
            if c < ref_close:
                queda_ok = True
                resultado["motivos_ok"].append("Fechamento abaixo de ref-3 candles.")

        if not queda_ok and candle_idx >= 1 and "mj_atr14" in df.columns:
            max_recente = float(df["high"].iloc[max(0, candle_idx - 10):candle_idx].max())
            if (max_recente - l) >= 0.5 * atr:
                queda_ok = True
                resultado["motivos_ok"].append("Recuo de 0.5x ATR desde topo recente.")

        if not queda_ok:
            resultado["motivos_fail"].append("Sem queda/correção anterior confirmada.")

        resultado["is_hammer"] = len(resultado["motivos_fail"]) == 0
    except Exception as exc:
        resultado["motivos_fail"].append(f"Erro interno: {exc}")
    return resultado


def identify_mjolnir_region(df: pd.DataFrame, candle_idx: int, config: dict) -> dict:
    """
    Verifica se o martelo ocorreu em suporte, VWAP, EMA, fundo anterior ou região técnica válida.
    """
    resultado = {"em_regiao": False, "regioes_testadas": [], "regiao_principal": ""}
    try:
        row = df.iloc[candle_idx]
        l, c = float(row["low"]), float(row["close"])
        atr_val = float(df["mj_atr14"].iloc[candle_idx]) if "mj_atr14" in df.columns else (float(row["high"]) - l)
        tol_mult = float(config.get("region_tolerance_atr_mult", 0.3))
        tolerancia = atr_val * tol_mult

        def testa_regiao(nome: str, nivel: float) -> bool:
            if nivel <= 0:
                return False
            toca = l <= nivel + tolerancia
            recupera = c > nivel
            if toca and recupera:
                resultado["regioes_testadas"].append(f"{nome} ({nivel:.2f})")
                return True
            return False

        # VWAP
        if "mj_vwap" in df.columns:
            vwap_val = float(df["mj_vwap"].iloc[candle_idx])
            if not np.isnan(vwap_val) and testa_regiao("VWAP", vwap_val):
                resultado["em_regiao"] = True
                resultado["regiao_principal"] = resultado["regiao_principal"] or f"VWAP ({vwap_val:.2f})"

        # EMA20 / EMA21
        for col, nome in [("mj_ema20", "EMA20"), ("mj_ema21", "EMA21")]:
            if col in df.columns:
                val = float(df[col].iloc[candle_idx])
                if not np.isnan(val) and testa_regiao(nome, val):
                    resultado["em_regiao"] = True
                    resultado["regiao_principal"] = resultado["regiao_principal"] or f"{nome} ({val:.2f})"

        # EMA200 (somente com rejeição clara)
        if "mj_ema200" in df.columns:
            val200 = float(df["mj_ema200"].iloc[candle_idx])
            if not np.isnan(val200):
                pavio_inf = float(row["close"] if row["close"] < row["open"] else row["open"]) - l
                if pavio_inf >= atr_val * 0.5 and testa_regiao("EMA200", val200):
                    resultado["em_regiao"] = True
                    resultado["regiao_principal"] = resultado["regiao_principal"] or f"EMA200 ({val200:.2f})"

        # Fundo anterior (mínima dos últimos 10 candles excluindo o atual)
        if candle_idx >= 10:
            prev_lows = df["low"].iloc[candle_idx - 10:candle_idx]
            fundo = float(prev_lows.min())
            if testa_regiao("Fundo anterior", fundo):
                resultado["em_regiao"] = True
                resultado["regiao_principal"] = resultado["regiao_principal"] or f"Fundo ({fundo:.2f})"

        # Suporte intradiário (mínima do dia excluindo o atual)
        if "datetime" in df.columns:
            dt_hoje = pd.to_datetime(df["datetime"].iloc[candle_idx]).normalize()
            idx_hoje = df[pd.to_datetime(df["datetime"]).dt.normalize() == dt_hoje].index
            idx_antes = [i for i in idx_hoje if i < candle_idx]
            if idx_antes:
                suporte = float(df["low"].iloc[idx_antes].min())
                if testa_regiao("Suporte intradiário", suporte):
                    resultado["em_regiao"] = True
                    resultado["regiao_principal"] = resultado["regiao_principal"] or f"Suporte ({suporte:.2f})"

    except Exception as exc:
        resultado["regioes_testadas"].append(f"Erro: {exc}")
    return resultado


def calculate_mjolnir_score(df: pd.DataFrame, candle_idx: int, hammer: dict, region: dict, config: dict) -> dict:
    """
    Calcula score técnico de 0 a 100 para o sinal Mjolnir.
    """
    score = 0
    detalhes: dict[str, int] = {}

    try:
        row = df.iloc[candle_idx]
        c = float(row["close"])

        # A. Formação do martelo — até 20 pontos
        pontos_martelo = len(hammer.get("motivos_ok", []))
        total_criterios = pontos_martelo + len(hammer.get("motivos_fail", []))
        a_pts = int(20 * (pontos_martelo / max(total_criterios, 1)))
        score += a_pts
        detalhes["A_formacao_martelo"] = a_pts

        # B. Região técnica — até 20 pontos
        b_pts = 20 if region.get("em_regiao") else 0
        score += b_pts
        detalhes["B_regiao_tecnica"] = b_pts

        # C. Tendência por EMAs — até 15 pontos
        c_pts = 0
        if "mj_ema9" in df.columns and "mj_ema20" in df.columns and "mj_ema200" in df.columns:
            ema9 = float(df["mj_ema9"].iloc[candle_idx])
            ema20 = float(df["mj_ema20"].iloc[candle_idx])
            ema200 = float(df["mj_ema200"].iloc[candle_idx])
            if not any(np.isnan(v) for v in [ema9, ema20, ema200]):
                if c > ema200:
                    c_pts += 5
                if c > ema20:
                    c_pts += 5
                if ema9 > ema20:
                    c_pts += 5
        score += c_pts
        detalhes["C_tendencia_emas"] = c_pts

        # D. Volume — até 10 pontos
        d_pts = 0
        if "mj_volume_sma20" in df.columns:
            vol = float(row["volume"])
            vol_sma = float(df["mj_volume_sma20"].iloc[candle_idx])
            if not np.isnan(vol_sma) and vol_sma > 0:
                ratio = vol / vol_sma
                if ratio >= 1.5:
                    d_pts = 10
                elif ratio >= 1.0:
                    d_pts = 6
                elif ratio >= 0.8:
                    d_pts = 3
        score += d_pts
        detalhes["D_volume"] = d_pts

        # E. RSI — até 10 pontos
        e_pts = 0
        if "mj_rsi14" in df.columns:
            rsi_val = float(df["mj_rsi14"].iloc[candle_idx])
            if not np.isnan(rsi_val):
                if 30 <= rsi_val <= 50:
                    e_pts = 10
                elif 50 < rsi_val <= 60:
                    e_pts = 7
                elif rsi_val < 30:
                    e_pts = 5  # sobrevenda
                else:
                    e_pts = 2
        score += e_pts
        detalhes["E_rsi"] = e_pts

        # F. ADX/DMI — até 10 pontos
        f_pts = 0
        if "mj_adx14" in df.columns and "mj_di_plus" in df.columns and "mj_di_minus" in df.columns:
            adx_val = float(df["mj_adx14"].iloc[candle_idx])
            di_p = float(df["mj_di_plus"].iloc[candle_idx])
            di_m = float(df["mj_di_minus"].iloc[candle_idx])
            if not any(np.isnan(v) for v in [adx_val, di_p, di_m]):
                if adx_val >= 25 and di_p > di_m:
                    f_pts = 10
                elif adx_val >= 20 and di_p > di_m:
                    f_pts = 7
                elif adx_val >= 15:
                    f_pts = 4
        score += f_pts
        detalhes["F_adx_dmi"] = f_pts

        # G. ATR e risco-retorno — até 10 pontos
        g_pts = 0
        if "mj_atr14" in df.columns:
            atr_val = float(df["mj_atr14"].iloc[candle_idx])
            if not np.isnan(atr_val) and atr_val > 0:
                max_stop = float(config.get("max_stop_reais", 1.50))
                if atr_val <= max_stop:
                    g_pts = 10
                elif atr_val <= max_stop * 1.5:
                    g_pts = 6
                else:
                    g_pts = 3
        score += g_pts
        detalhes["G_atr_rr"] = g_pts

        # H. Contexto de mercado — até 5 pontos
        h_pts = 3  # padrão neutro
        score += h_pts
        detalhes["H_contexto"] = h_pts

        # --- Penalizações ---
        if "mj_di_minus" in df.columns and "mj_di_plus" in df.columns:
            di_m_v = float(df["mj_di_minus"].iloc[candle_idx])
            di_p_v = float(df["mj_di_plus"].iloc[candle_idx])
            if not any(np.isnan(v) for v in [di_m_v, di_p_v]) and di_m_v > di_p_v:
                score -= 15
                detalhes["pen_di_minus_dominante"] = -15

        if "mj_volume_sma20" in df.columns:
            vol_v = float(row["volume"])
            vsma_v = float(df["mj_volume_sma20"].iloc[candle_idx])
            if not np.isnan(vsma_v) and vsma_v > 0 and vol_v < 0.8 * vsma_v:
                score -= 12
                detalhes["pen_volume_baixo"] = -12

        if "mj_rsi14" in df.columns and candle_idx >= 1:
            rsi_now = float(df["mj_rsi14"].iloc[candle_idx])
            rsi_prev = float(df["mj_rsi14"].iloc[candle_idx - 1])
            if not any(np.isnan(v) for v in [rsi_now, rsi_prev]) and rsi_now < rsi_prev:
                score -= 8
                detalhes["pen_rsi_caindo"] = -8

        if "mj_adx14" in df.columns:
            adx_v = float(df["mj_adx14"].iloc[candle_idx])
            if not np.isnan(adx_v) and adx_v < 15:
                score -= 5
                detalhes["pen_adx_fraco"] = -5

        if "mj_ema200" in df.columns and "mj_ema20" in df.columns and "mj_ema9" in df.columns:
            e200 = float(df["mj_ema200"].iloc[candle_idx])
            e20 = float(df["mj_ema20"].iloc[candle_idx])
            e9 = float(df["mj_ema9"].iloc[candle_idx])
            if not any(np.isnan(v) for v in [e200, e20, e9]):
                if c < e200 and e20 < e200 and e9 < e20:
                    score -= 20
                    detalhes["pen_abaixo_ema200_descendente"] = -20

        score = max(0, min(100, score))

    except Exception as exc:
        score = 0
        detalhes["erro"] = str(exc)

    # Classificação
    if score >= 90:
        classificacao = "MJOLNIR SUPREMO"
    elif score >= 80:
        classificacao = "MJOLNIR FORTE"
    elif score >= 70:
        classificacao = "MJOLNIR MODERADO"
    elif score >= 60:
        classificacao = "MJOLNIR FRACO"
    else:
        classificacao = "DESCARTAR"

    return {"score": score, "classificacao": classificacao, "detalhes": detalhes}


def _mjolnir_entry_stop_target(df: pd.DataFrame, candle_idx: int, config: dict) -> dict:
    """Calcula entrada, stop e alvo técnicos do sinal Mjolnir."""
    try:
        row = df.iloc[candle_idx]
        h = float(row["high"])
        l = float(row["low"])
        atr_val = float(df["mj_atr14"].iloc[candle_idx]) if "mj_atr14" in df.columns else (h - l)
        if np.isnan(atr_val):
            atr_val = h - l

        buf_entry = float(config.get("entry_buffer_reais", 0.01))
        buf_stop_mult = float(config.get("stop_buffer_atr_mult", 0.1))

        entrada = round(h + buf_entry, 2)
        stop = round(l - atr_val * buf_stop_mult, 2)
        risco = entrada - stop
        alvo1 = round(entrada + risco, 2)
        alvo2 = round(entrada + 2 * risco, 2)

        return {
            "entrada": entrada,
            "stop": stop,
            "alvo1": alvo1,
            "alvo2": alvo2,
            "risco": round(risco, 4),
            "rr": 2.0,
        }
    except Exception:
        return {}


def run_mjolnir_scan_for_mesa(tickers: list, timeframe: str, config: dict) -> list:
    """
    Roda o Scan Mjolnir nos ativos da Mesa Day Trade e retorna sinais qualificados.
    Retorna lista de dicts ordenada por score decrescente.
    """
    mjolnir_cfg = config.get("mjolnir", {})
    min_score = int(mjolnir_cfg.get("min_score", 60))
    resultados: list[dict] = []

    for ticker in tickers:
        resultado_base = {
            "ticker": ticker,
            "status": "BLOQUEADO",
            "score": 0,
            "classificacao": "DESCARTAR",
            "alerta": "BLOQUEADO — dados insuficientes para Mjolnir",
            "entrada": None,
            "stop": None,
            "alvo1": None,
            "alvo2": None,
            "fatores_ok": [],
            "fatores_atencao": [],
            "regiao": "",
        }
        try:
            df_raw, meta = prepare_mjolnir_dataframe(ticker, timeframe, mjolnir_cfg)

            if df_raw.empty or meta.get("erro"):
                resultado_base["alerta"] = f"BLOQUEADO — dados insuficientes para Mjolnir: {meta.get('erro', 'sem dados')}"
                resultados.append(resultado_base)
                continue

            df_mj = calculate_mjolnir_indicators(df_raw)

            valido, erros_contrato = validate_mjolnir_data_contract(df_mj, timeframe)
            if not valido:
                resultado_base["alerta"] = "BLOQUEADO — dados insuficientes para Mjolnir: " + "; ".join(erros_contrato)
                resultados.append(resultado_base)
                continue

            # Analisa o último candle fechado (índice -1 já com candle em formação removido)
            candle_idx = len(df_mj) - 1
            hammer = detect_mjolnir_hammer(df_mj, candle_idx, mjolnir_cfg)

            if not hammer["is_hammer"]:
                resultado_base["status"] = "AGUARDAR"
                resultado_base["alerta"] = "Nenhum martelo qualificado no último candle."
                resultado_base["fatores_atencao"] = hammer.get("motivos_fail", [])
                resultados.append(resultado_base)
                continue

            region = identify_mjolnir_region(df_mj, candle_idx, mjolnir_cfg)

            score_data = calculate_mjolnir_score(df_mj, candle_idx, hammer, region, mjolnir_cfg)
            score = score_data["score"]
            classificacao = score_data["classificacao"]

            est = _mjolnir_entry_stop_target(df_mj, candle_idx, mjolnir_cfg)

            if score < min_score:
                status = "AGUARDAR"
                alerta = f"Score {score}/100 — abaixo do mínimo configurado ({min_score})."
            elif not region["em_regiao"]:
                status = "AGUARDAR ROMPIMENTO"
                alerta = f"Martelo detectado mas sem região técnica confirmada. Score {score}."
            else:
                status = "AGUARDAR ROMPIMENTO"
                alerta = (
                    f"{classificacao} — Score {score}/100 · Região: {region.get('regiao_principal', '—')} · "
                    f"Entrada após romper máxima {est.get('entrada', '—')}."
                )

            resultados.append({
                "ticker": ticker,
                "status": status,
                "score": score,
                "classificacao": classificacao,
                "alerta": alerta,
                "entrada": est.get("entrada"),
                "stop": est.get("stop"),
                "alvo1": est.get("alvo1"),
                "alvo2": est.get("alvo2"),
                "fatores_ok": hammer.get("motivos_ok", []) + region.get("regioes_testadas", []),
                "fatores_atencao": hammer.get("motivos_fail", []),
                "regiao": region.get("regiao_principal", ""),
            })

        except Exception as exc:
            resultado_base["alerta"] = f"BLOQUEADO — dados insuficientes para Mjolnir: {exc}"
            resultados.append(resultado_base)

    resultados.sort(key=lambda x: x["score"], reverse=True)
    return resultados


# ============================================================
# 4. CACHE DA MESA
# ============================================================

@st.cache_data(ttl=120)
def load_mesa_data(ticker: str, robo: str, capital: float, fees_params: tuple) -> dict:
    """
    Carrega dados diários do ativo, calcula indicadores e roda o robô selecionado.
    Cache de 120 s.
    """
    fees = FeeParams(*fees_params)
    result = {
        "ticker": ticker,
        "preco": None,
        "fonte": "—",
        "atualizado_em": "—",
        "ativo": None,
        "erro": None,
    }
    try:
        sym = ticker.strip().upper()
        if not sym.endswith(("USDT", ".SA")):
            sym = f"{sym}.SA"

        df, meta = fetch_history(sym, period="3mo", interval="1d", limit=300)
        df_ind = add_indicators(df, interval="1d")

        result["preco"] = round(float(df_ind["close"].iloc[-1]), 2)
        result["fonte"] = meta.fonte
        result["atualizado_em"] = meta.atualizado_em

        ativo = _rodar_robo(robo, df_ind, None, capital, fees, slippage_pct=0.1)
        ativo.ticker = ticker
        ativo.preco_atual = result["preco"]
        ativo.fonte_preco = meta.fonte
        ativo.atualizado_em = meta.atualizado_em

        if _eh_futuro(ticker):
            ativo.status = "SOMENTE PLANEJAMENTO"

        result["ativo"] = ativo
    except Exception as exc:
        result["erro"] = str(exc)
    return result


# ============================================================
# 5. HELPERS DE DISPLAY
# ============================================================

def _calor_badge(calor: int) -> str:
    if calor >= 70:
        return f"🔥 {calor}"
    elif calor >= 40:
        return f"⚠️ {calor}"
    else:
        return f"❄️ {calor}"


def _status_badge(status: str) -> str:
    cores = {
        "GATILHO ATIVO": "🟢",
        "ENTRADA CONFIRMADA": "🟢",
        "OBSERVAR": "🟡",
        "AGUARDAR ROMPIMENTO": "🟡",
        "AGUARDAR CONFIRMAÇÃO": "🟡",
        "AGUARDAR PULLBACK": "🟡",
        "OBSERVAR REVERSÃO": "🟡",
        "SIMULAR": "🟡",
        "AGUARDAR": "⚪",
        "BLOQUEADO": "🔴",
        "SINAL INVALIDADO": "🔴",
        "EXPIRADO": "🔴",
        "SOMENTE PLANEJAMENTO": "🔵",
    }
    icone = cores.get(status, "⚪")
    return f"{icone} {status}"


def _mjolnir_badge(ativo: MesaAtivo) -> str:
    if ativo.mjolnir_score is None:
        return "—"
    score = ativo.mjolnir_score
    classif = ativo.mjolnir_classificacao or ""
    if "SUPREMO" in classif:
        return f"⚡ {score} · SUPREMO"
    elif "FORTE" in classif:
        return f"🔨 {score} · FORTE"
    elif "MODERADO" in classif:
        return f"🔨 {score} · MODERADO"
    elif "FRACO" in classif:
        return f"🔨 {score} · FRACO"
    elif "BLOQUEADO" in (ativo.mjolnir_status or ""):
        return "🚫 BLOQUEADO"
    return f"🔨 {score}"


def _fmt_brl(v: float | None, prefix: str = "R$") -> str:
    if v is None:
        return "—"
    return f"{prefix} {v:,.2f}"


# ============================================================
# 6. PAINEL LATERAL DE DETALHES
# ============================================================

def _render_painel_lateral(ativo: MesaAtivo | None) -> None:
    st.markdown("#### 📋 Detalhes do ativo")
    if ativo is None:
        st.info("Selecione um ativo na tabela para ver os detalhes.")
        return

    st.markdown(f"**{ativo.ticker}**")
    st.markdown(_status_badge(ativo.status))

    if ativo.mjolnir_status:
        st.markdown(f"🔨 Mjolnir: **{ativo.mjolnir_status}**")
        if ativo.mjolnir_score is not None:
            st.progress(ativo.mjolnir_score / 100, text=f"Score {ativo.mjolnir_score}/100")

    st.divider()

    rr_txt = "—"
    if ativo.entrada_sugerida and ativo.stop_sugerido and ativo.alvo_sugerido:
        risco = ativo.entrada_sugerida - ativo.stop_sugerido
        retorno = ativo.alvo_sugerido - ativo.entrada_sugerida
        rr_txt = f"1:{retorno/risco:.1f}" if risco > 0 else "—"

    st.markdown("**Plano operacional**")
    col1, col2 = st.columns(2)
    col1.metric("Entrada", _fmt_brl(ativo.entrada_sugerida))
    col2.metric("Stop", _fmt_brl(ativo.stop_sugerido))
    col1.metric("Alvo", _fmt_brl(ativo.alvo_sugerido))
    col2.metric("R:R", rr_txt)

    if ativo.quantidade_simulada:
        st.metric("Qtd simulada", ativo.quantidade_simulada)

    st.divider()

    st.markdown("**Resultado estimado (alvo)**")
    ll = ativo.lucro_liquido
    if ll is not None:
        color = "normal" if ll > 0 else "inverse"
        st.metric("Lucro líquido", _fmt_brl(ll), delta=f"{(ll/(ativo.lucro_bruto or 1))*100:.1f}%" if ativo.lucro_bruto else None, delta_color=color)

    if any([ativo.lucro_bruto, ativo.custos_estimados, ativo.ir_estimado]):
        with st.expander("Breakdown de custos"):
            st.write(f"Lucro bruto: {_fmt_brl(ativo.lucro_bruto)}")
            st.write(f"Custos totais: {_fmt_brl(ativo.custos_estimados)}")
            st.write(f"IR (20% DT): {_fmt_brl(ativo.ir_estimado)}")
            st.write(f"**Lucro líquido: {_fmt_brl(ativo.lucro_liquido)}**")

    if ativo.mjolnir_alerta:
        st.divider()
        st.markdown(f"🔨 **Alerta Mjolnir:** {ativo.mjolnir_alerta}")

    if ativo.mjolnir_fatores_ok:
        with st.expander("✅ Fatores favoráveis"):
            for f in ativo.mjolnir_fatores_ok:
                st.write(f"- {f}")

    if ativo.mjolnir_fatores_atencao:
        with st.expander("⚠️ Fatores de atenção"):
            for f in ativo.mjolnir_fatores_atencao:
                st.write(f"- {f}")

    st.divider()
    st.caption("Simulação não gera ordens. Execute manualmente no seu Home Broker.")


# ============================================================
# 7. RENDER PRINCIPAL DA ABA
# ============================================================

def render_mesa_day_trade_tab() -> None:
    """Renderiza a aba 🎯 Mesa Day Trade no Streamlit."""

    config = load_mesa_config()
    mjolnir_cfg = config.get("mjolnir", {})

    # ---------- Relógios e status de mercado ----------
    render_market_clocks()
    b3_open, b3_status_label = is_b3_open()
    st.caption("⚠️ Dados com possível atraso (Brapi/Yahoo). Use o campo Preço manual para inserir cotação direta do Home Broker.")

    # ---------- Layout: sidebar config | tabela | painel ----------
    col_sb, col_main, col_rp = st.columns([1, 3, 1.2])

    # ---- Sidebar de configurações ----
    with col_sb:
        st.markdown("#### ⚙️ Configurações")
        capital = st.number_input(
            "Capital por operação (R$)",
            min_value=100.0, max_value=1_000_000.0,
            value=float(config.get("capital_por_operacao", 1000.0)),
            step=100.0, format="%.2f", key="dt_capital",
        )
        robo_ativo = st.selectbox(
            "Robô ativo", ROBOS_DISPONIVEIS,
            index=ROBOS_DISPONIVEIS.index(config.get("robo_ativo", "Conservador")),
            key="dt_robo",
        )
        corretagem = st.number_input(
            "Corretagem por ordem (R$)",
            min_value=0.0, value=float(config.get("corretagem", 4.90)),
            step=0.10, format="%.2f", key="dt_corretagem",
        )
        slippage_pct = st.slider(
            "Slippage estimado (%)", 0.0, 0.5,
            value=float(config.get("slippage_pct", 0.1)),
            step=0.05, key="dt_slippage",
        )

        st.markdown("---")
        st.markdown(f"#### 📋 Ativos da Mesa ({len(config['ativos'])}/20)")
        ativos_str = st.text_area(
            "Tickers (um por linha ou vírgula)",
            value="\n".join(config["ativos"]),
            height=160, key="dt_ativos_input",
        )

        if st.button("Salvar configurações", key="dt_salvar_cfg"):
            novos_ativos = [
                a.strip().upper()
                for a in ativos_str.replace(",", "\n").splitlines()
                if a.strip()
            ][:20]
            config["ativos"] = novos_ativos
            config["capital_por_operacao"] = capital
            config["robo_ativo"] = robo_ativo
            config["corretagem"] = corretagem
            config["slippage_pct"] = slippage_pct
            save_mesa_config(config)
            st.success("Configurações salvas.")
            st.rerun()

    # ---- Área principal ----
    with col_main:
        st.markdown("### 🎯 Mesa Day Trade")
        st.caption("Simulação e planejamento de operações day trade")

        ativos_lista = config.get("ativos", [])
        fees = FeeParams(
            buy_brokerage=corretagem,
            sell_brokerage=corretagem,
            b3_rate_pct=0.0230,
            xp_operational_rate_pct=0.0,
            ir_rate_pct=20.0,
        )
        fees_tuple = (fees.buy_brokerage, fees.sell_brokerage, fees.b3_rate_pct, fees.xp_operational_rate_pct, fees.ir_rate_pct)

        # Botões de ação
        btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 2])
        atualizar = btn_col1.button("🔄 Atualizar mesa", key="dt_atualizar", type="primary")
        scan_mjolnir = btn_col2.button(
            "🔨 Scan Mjolnir",
            key="dt_mjolnir",
            disabled=not b3_open,
            help="Scan Mjolnir usa dados intraday. Disponível apenas durante o pregão (10:00–16:55, dias úteis)." if not b3_open else None,
        )
        btn_col3.caption(f"Atualizado · {datetime.now(tz=_TZ_BR).strftime('%H:%M:%S')}")
        if not b3_open:
            st.info(f"🔴 {b3_status_label} — Scan Mjolnir indisponível fora do pregão. Use **Atualizar mesa** para ver a análise baseada no fechamento anterior.")

        # Preços manuais na session_state
        if "dt_precos_manuais" not in st.session_state:
            st.session_state["dt_precos_manuais"] = dict(config.get("precos_manuais", {}))
        if "dt_ativos_carregados" not in st.session_state or atualizar:
            st.session_state["dt_ativos_carregados"] = {}

        # Carrega/atualiza dados
        if atualizar or not st.session_state["dt_ativos_carregados"]:
            prog = st.progress(0, text="Carregando ativos...")
            for i, ticker in enumerate(ativos_lista):
                dados = load_mesa_data(ticker, robo_ativo, capital, fees_tuple)
                st.session_state["dt_ativos_carregados"][ticker] = dados
                prog.progress((i + 1) / max(len(ativos_lista), 1), text=f"Carregando {ticker}...")
            prog.empty()

        # Scan Mjolnir
        if scan_mjolnir:
            timeframe_mj = mjolnir_cfg.get("timeframe", "5m")
            with st.spinner(f"🔨 Rodando Scan Mjolnir ({timeframe_mj}) nos {len(ativos_lista)} ativos..."):
                sinais_mj = run_mjolnir_scan_for_mesa(ativos_lista, timeframe_mj, config)
            st.session_state["dt_mjolnir_sinais"] = {s["ticker"]: s for s in sinais_mj}
            config["mjolnir"]["last_scan"] = [
                {"ticker": s["ticker"], "score": s["score"], "status": s["status"], "alerta": s["alerta"]}
                for s in sinais_mj
            ]
            save_mesa_config(config)
            mj_ok = [s for s in sinais_mj if s["score"] >= int(mjolnir_cfg.get("min_score", 60))]
            if mj_ok:
                st.success(f"🔨 Mjolnir: {len(mj_ok)} sinal(is) qualificado(s). Melhor: {mj_ok[0]['ticker']} · Score {mj_ok[0]['score']}/100 · {mj_ok[0]['classificacao']}")
            else:
                st.info("🔨 Mjolnir: Nenhum martelo qualificado encontrado nos ativos da Mesa.")

        sinais_mj = st.session_state.get("dt_mjolnir_sinais", {})

        # Mescla dados do Mjolnir nos ativos
        dados_mesa: list[MesaAtivo] = []
        for ticker in ativos_lista:
            dado = st.session_state["dt_ativos_carregados"].get(ticker, {})
            ativo: MesaAtivo = dado.get("ativo") or MesaAtivo(ticker=ticker, status="AGUARDAR", fonte_preco="—", atualizado_em="—")

            pm = st.session_state["dt_precos_manuais"].get(ticker)
            if pm:
                ativo.preco_manual = pm

            if ticker in sinais_mj:
                mj = sinais_mj[ticker]
                ativo.mjolnir_score = mj["score"]
                ativo.mjolnir_classificacao = mj["classificacao"]
                ativo.mjolnir_status = mj["status"]
                ativo.mjolnir_alerta = mj["alerta"]
                ativo.mjolnir_entrada = mj.get("entrada")
                ativo.mjolnir_stop = mj.get("stop")
                ativo.mjolnir_alvo = mj.get("alvo1")
                ativo.mjolnir_fatores_ok = mj.get("fatores_ok", [])
                ativo.mjolnir_fatores_atencao = mj.get("fatores_atencao", [])

            dados_mesa.append(ativo)

        # Seleção de ativo para painel lateral
        if "dt_ativo_sel" not in st.session_state:
            st.session_state["dt_ativo_sel"] = None

        # Tabela principal
        st.markdown("---")
        if not dados_mesa:
            st.info("Nenhum ativo na mesa. Adicione ativos no painel de configurações.")
        else:
            # Cabeçalho
            hdr = st.columns([1.2, 0.9, 0.9, 0.6, 0.7, 1.2, 0.7, 0.7, 0.7, 0.5, 0.9, 1.0, 1.0, 0.6])
            for h, txt in zip(hdr, [
                "Ativo", "Preço", "Preço manual", "Calor", "Robô",
                "Gatilho", "Entrada", "Stop", "Alvo", "Qtd",
                "Lucro líq.", "Status", "Mjolnir", "Sel."
            ]):
                h.markdown(f"**{txt}**")

            st.markdown("---")

            for ativo in dados_mesa:
                is_sel = st.session_state["dt_ativo_sel"] == ativo.ticker
                sel_style = "🔷" if is_sel else "🔹"
                cols = st.columns([1.2, 0.9, 0.9, 0.6, 0.7, 1.2, 0.7, 0.7, 0.7, 0.5, 0.9, 1.0, 1.0, 0.6])

                # Ativo
                cols[0].markdown(f"**{ativo.ticker}**")

                # Preço atual
                preco_ref = ativo.preco_manual if ativo.preco_manual else ativo.preco_atual
                if preco_ref:
                    label_pm = " ✏️" if ativo.preco_manual else ""
                    cols[1].markdown(f"R$ {preco_ref:,.2f}{label_pm}")
                    cols[1].caption(f"{ativo.fonte_preco} · {ativo.atualizado_em}")
                else:
                    cols[1].markdown("—")

                # Preço manual (input)
                pm_key = f"dt_pm_{ativo.ticker}"
                pm_val = st.session_state["dt_precos_manuais"].get(ativo.ticker, 0.0)
                pm_input = cols[2].number_input(
                    "", min_value=0.0, value=float(pm_val or 0.0),
                    step=0.01, format="%.2f", key=pm_key, label_visibility="collapsed",
                )
                if pm_input > 0 and pm_input != pm_val:
                    st.session_state["dt_precos_manuais"][ativo.ticker] = pm_input

                # Calor
                cols[3].markdown(_calor_badge(ativo.calor))

                # Robô
                cols[4].markdown(ativo.estrategia or "—")

                # Gatilho
                cols[5].caption(ativo.gatilho or "—")

                # Entrada / Stop / Alvo / Qtd
                cols[6].markdown(_fmt_brl(ativo.entrada_sugerida))
                cols[7].markdown(_fmt_brl(ativo.stop_sugerido))
                cols[8].markdown(_fmt_brl(ativo.alvo_sugerido))
                cols[9].markdown(str(ativo.quantidade_simulada) if ativo.quantidade_simulada else "—")

                # Lucro líquido
                ll = ativo.lucro_liquido
                if ll is not None:
                    cor = "green" if ll > 0 else "red"
                    cols[10].markdown(f"<span style='color:{cor}'>R$ {ll:,.2f}</span>", unsafe_allow_html=True)
                else:
                    cols[10].markdown("—")

                # Status
                cols[11].markdown(_status_badge(ativo.status))

                # Mjolnir badge
                cols[12].markdown(_mjolnir_badge(ativo))

                # Seleção
                if cols[13].button(sel_style, key=f"dt_sel_{ativo.ticker}"):
                    st.session_state["dt_ativo_sel"] = ativo.ticker
                    st.rerun()

        # Legenda de calor
        st.caption("❄️ 0–39 FRIO  ·  ⚠️ 40–69 ATENÇÃO  ·  🔥 70–100 QUENTE  ·  Fontes: Brapi (possível atraso) / Yahoo Finance  ·  Preços manuais têm prioridade")

    # ---- Painel lateral de detalhes ----
    with col_rp:
        ativo_sel_ticker = st.session_state.get("dt_ativo_sel")
        ativo_sel: MesaAtivo | None = None

        if ativo_sel_ticker and dados_mesa:
            for a in dados_mesa:
                if a.ticker == ativo_sel_ticker:
                    ativo_sel = a
                    break

        _render_painel_lateral(ativo_sel)

    # ---------- Aviso legal obrigatório ----------
    st.divider()
    st.caption(
        "🎯 Mesa Day Trade — Ferramenta de apoio decisório para uso pessoal. "
        "Não executa ordens. Não é recomendação de investimento. "
        "Toda operação deve ser executada e confirmada manualmente no Home Broker. "
        "Verifique sempre liquidez, book, notícias e suitability antes de operar. "
        "Day trade envolve risco elevado de perda. IR: 20% sobre lucro líquido (day trade). "
        "🔨 Mjolnir é uma Special Skill de filtragem técnica baseada em candles fechados e indicadores validados."
    )
    st.caption(
        "⚠️ Mini índice e mini dólar requerem margem de garantia — permitido apenas para estudo e simulação. "
        "Status fixo: SOMENTE PLANEJAMENTO."
    )
