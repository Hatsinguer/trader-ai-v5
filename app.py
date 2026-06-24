from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from src.analysis import (
    CALLS_V5,
    ai_commentary,
    build_opportunity_call,
    build_opportunity_call_v5,
    build_signal,
    build_signal_v5,
    calculate_radar_score,
    position_action,
)
from src.alerts import (
    ALERT_LABELS,
    ALERT_TYPES,
    add_alert,
    check_alerts,
    load_alerts,
    remove_alert,
    save_alerts,
)
from src.asset_classifier import DEFAULT_B3_RADAR, classify_asset
from src.costs import FeeParams, buy_total_cost, default_brokerage_profile, required_sell_price, sell_simulation
from src.backtest import (
    bollinger_reversion_backtest,
    ema_crossover_backtest,
    macd_signal_backtest,
    rsi_reversal_backtest,
)
from src.report import generate_analysis_report
from src.data import DataMeta, binance_meta, fetch_b3_history, fetch_binance_klines, fetch_yfinance_history
from src.fundamentals import fetch_fundamentals, quick_reading
from src.indicators import add_indicators
from src.storage import (
    Position,
    add_tickers_to_watchlist,
    delete_position_v5,
    delete_watchlist,
    get_position_v5,
    load_decisions_log,
    load_positions,
    load_positions_v5,
    load_radar_history,
    load_watchlists,
    log_decision,
    normalize_tickers,
    save_position,
    save_radar_snapshot,
    upsert_position_v5,
    upsert_watchlist,
)

load_dotenv()

st.set_page_config(page_title="Trader AI MVP", layout="wide")
st.title("Trader AI MVP — análise técnica com IA")
st.caption("Ferramenta educacional para análise própria. Não envia ordens e não constitui recomendação de investimento.")

CALLS = ["COMPRA_TÉCNICA_AGRESSIVA", "COMPRA_TÉCNICA", "MONITORAR_COMPRA", "AGUARDAR", "EVITAR_COMPRA"]


@st.cache_data(ttl=300)
def load_data_with_meta(source: str, symbol: str, interval: str, limit: int | None, period: str | None) -> tuple[pd.DataFrame, DataMeta]:
    """Carrega dados com metadados de fonte. B3 usa fallback Brapi -> yfinance."""
    if source == "Binance Spot":
        df = fetch_binance_klines(symbol=symbol, interval=interval, limit=limit or 500)
        return df, binance_meta()
    return fetch_b3_history(symbol, period=period or "1y", interval=interval)


def load_data(source: str, symbol: str, interval: str, limit: int | None, period: str | None) -> pd.DataFrame:
    df, _meta = load_data_with_meta(source, symbol, interval, limit, period)
    return df


@st.cache_data(ttl=900)
def load_fundamentals_cached(ticker: str) -> dict | None:
    return fetch_fundamentals(ticker)


@st.cache_data(ttl=300)
def get_current_metrics(ticker: str, profile: str = "Moderado", interval: str = "1d", period: str = "1y") -> dict | None:
    """Métricas atuais resumidas de um ticker (preço, RSI, call, SMA20, opp) com cache."""
    try:
        is_crypto = ticker.strip().upper().endswith("USDT")
        src = "Binance Spot" if is_crypto else "B3 (Brapi → Yahoo)"
        if is_crypto:
            df = load_data(src, ticker, interval, 500, None)
        else:
            df = load_data(src, ticker, interval, None, period)
        di = add_indicators(df, interval=interval)
        sig = build_signal_v5(di)
        opp = build_opportunity_call_v5(sig, profile=profile)
        last = di.iloc[-1]
        return {
            "price": float(last["close"]),
            "rsi": float(last["rsi14"]),
            "call": opp.call,
            "sma20": float(sig.snapshot.get("sma20") or 0),
            "opp": opp,
        }
    except Exception:
        return None


def _position_break_even(position: Position) -> float | None:
    """Preço mínimo de venda para zerar custos, usando perfil de custo XP Digital padrão."""
    try:
        fees = FeeParams(**default_brokerage_profile("XP Digital — Ações Swing Trade (R$ 4,90/ordem)"))
        be = required_sell_price(int(position.quantity), float(position.total_invested), fees, target_profit_after_ir=0.0)
        return be["preco_venda_necessario"]
    except Exception:
        return None


def render_position_panel(symbol: str, position: Position, opp, current_price: float, sma20: float | None) -> None:
    """Modo 'Analisar minha posição' — substitui o Bloco 4 quando há posição cadastrada."""
    qty = int(position.quantity)
    avg = float(position.avg_buy_price)
    invested = float(position.total_invested)
    pnl_abs = current_price * qty - invested
    pnl_pct = (current_price - avg) / avg * 100 if avg else 0.0
    action = position_action(position, opp, current_price)
    break_even = _position_break_even(position)
    ir_est = max(0.0, pnl_abs) * 0.15  # IR swing estimado (15%) se vender agora

    stop_sug = position.stop_price if position.stop_price is not None else (sma20 or opp.invalidation_price)
    stop_txt = f"R$ {stop_sug:,.2f}" if stop_sug else "—"
    alvo1 = f"R$ {opp.target_1_atr:,.2f}" if opp.target_1_atr is not None else "—"
    alvo2 = f"R$ {opp.target_2_atr:,.2f}" if opp.target_2_atr is not None else "—"

    body = (
        f"### Sua posição em {symbol}\n\n"
        f"Comprado a: **R$ {avg:,.2f}** ({qty} unid.) · Total investido: **R$ {invested:,.2f}** (com custos)\n\n"
        f"Preço atual: **R$ {current_price:,.2f}**  ·  Resultado: **R$ {pnl_abs:+,.2f} ({pnl_pct:+.2f}%)**\n\n"
        + (f"Preço mínimo de venda (break-even): **R$ {break_even:,.2f}**\n\n" if break_even is not None else "")
        + f"**Conduta para a sua posição:** {action}\n\n"
        f"Stop sugerido: **{stop_txt}**" + f"  ·  Alvo 1: {alvo1}  ·  Alvo 2: {alvo2}\n\n"
        f"IR estimado se vender agora: **R$ {ir_est:,.2f}**"
    )

    if action.startswith(("VENDER", "AVALIAR")):
        st.error("🔴 " + body)
    elif action.startswith(("REALIZAR", "MONITORAR")):
        st.warning("🟡 " + body)
    else:
        st.success("🟢 " + body)


def analyze_ticker_v5(ticker: str, profile: str, interval: str, period: str) -> dict:
    """Analisa um ticker com o motor v5 e devolve uma linha para o radar."""
    is_crypto = ticker.strip().upper().endswith("USDT")
    src = "Binance Spot" if is_crypto else "B3 (Brapi → Yahoo)"
    if is_crypto:
        df = load_data(src, ticker, interval, 500, None)
    else:
        df = load_data(src, ticker, interval, None, period)
    df_ind = add_indicators(df, interval=interval)
    signal = build_signal_v5(df_ind)
    opp = build_opportunity_call_v5(signal, profile=profile)
    last = df_ind.iloc[-1]
    vol_fin = float(last["close"]) * float(last["volume"])
    return {
        "Ticker": ticker,
        "Tipo": classify_asset(ticker),
        "Call": opp.call,
        "Score": opp.score,
        "Radar score": round(calculate_radar_score(opp), 1),
        "Confiança": opp.confidence,
        "Tendência": opp.trend_strength,
        "Fechamento": round(float(last["close"]), 2),
        "RSI14": round(float(last["rsi14"]), 1),
        "ADX": round(float(last.get("adx", 0) or 0), 1),
        "Vol R$ mi": round(vol_fin / 1e6, 1),
        "R:R": opp.risk_reward_ratio,
        "Dist SMA20 %": opp.distance_from_sma20_pct,
        "Stop": opp.invalidation_price,
        "Alvo 1": opp.target_1_atr,
        "Alvo 2": opp.target_2_atr,
    }


def render_source_badge(meta: DataMeta) -> None:
    """Badge colorido da fonte do dado: verde (Brapi/Binance), amarelo (Yahoo)."""
    if meta.confiavel:
        bg, fg = "#1b5e20", "#ffffff"  # verde
    else:
        bg, fg = "#f9a825", "#1a1a1a"  # amarelo
    st.markdown(
        f"<span style='background:{bg};color:{fg};padding:3px 12px;border-radius:12px;"
        f"font-size:0.85em;font-weight:600;'>"
        f"● Fonte: {meta.fonte} · {meta.tipo_dado} · atualizado {meta.atualizado_em}</span>",
        unsafe_allow_html=True,
    )


def _fmt_volume_fin(v: float) -> str:
    if v >= 1e9:
        return f"R$ {v / 1e9:,.2f} bi"
    if v >= 1e6:
        return f"R$ {v / 1e6:,.1f} mi"
    if v >= 1e3:
        return f"R$ {v / 1e3:,.1f} mil"
    return f"R$ {v:,.0f}"


def render_fundamentals_block(fund: dict | None) -> None:
    """Bloco fundamentalista colapsável (Brapi)."""
    with st.expander("📚 Contexto fundamentalista"):
        if not fund:
            st.info(
                "Dados fundamentalistas indisponíveis para este ativo "
                "(pode exigir Brapi PRO/BRAPI_TOKEN ou não haver cobertura)."
            )
            return

        def fmt(v, suffix=""):
            return f"{v:,.2f}{suffix}" if isinstance(v, (int, float)) else "—"

        sector = fund.get("sector") or "—"
        subsector = fund.get("subsector") or "—"
        st.write(f"**Setor:** {sector}" + (f" · {subsector}" if subsector != "—" else ""))

        f1, f2, f3 = st.columns(3)
        f1.metric("P/L", fmt(fund.get("pl")))
        f1.metric("P/VP", fmt(fund.get("pvp")))
        f2.metric("Dividend Yield", fmt(fund.get("dy"), "%"))
        f2.metric("ROE", fmt(fund.get("roe"), "%"))
        f3.metric("Margem líquida", fmt(fund.get("net_margin"), "%"))
        f3.metric("Dívida/PL", fmt(fund.get("debt_equity")))

        st.caption(quick_reading(fund))


def render_analise_completa(symbol: str, df_ind: pd.DataFrame, signal, opp, meta: DataMeta, interval: str, fundamentals: dict | None = None, position: Position | None = None) -> None:
    """Renderiza os 4 blocos visuais da Análise Completa v5."""
    snap = signal.snapshot
    valid = df_ind.dropna(subset=["sma20"]).reset_index(drop=True)
    last = df_ind.iloc[-1]
    prev = df_ind.iloc[-2] if len(df_ind) > 1 else last
    price = float(last["close"])
    prev_close = float(prev["close"])
    change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0
    vol_fin = float(last["volume"]) * price

    # ---------- Bloco 1: Situação atual ----------
    st.subheader(f"📊 {symbol}")
    render_source_badge(meta)
    b1c1, b1c2, b1c3 = st.columns(3)
    b1c1.metric("Preço atual", f"R$ {price:,.2f}", f"{change_pct:+.2f}%")
    b1c2.metric("Volume financeiro (dia)", _fmt_volume_fin(vol_fin))
    b1c3.metric("Atualizado", meta.atualizado_em)

    st.divider()

    # ---------- Bloco 2: Leitura do período ----------
    st.markdown("#### Leitura do período")
    period_high = float(df_ind["high"].max())
    period_low = float(df_ind["low"].min())
    sma20 = float(snap.get("sma20") or price)
    adx_val = float(snap.get("adx") or 0)
    atr_val = float(snap.get("atr14") or 0)
    dist = opp.distance_from_sma20_pct
    dist_label = "acima" if dist >= 0 else "abaixo"
    adx_label = "tendência forte" if adx_val >= 25 else ("tendência presente" if adx_val >= 20 else "tendência fraca")

    b2c1, b2c2, b2c3 = st.columns(3)
    with b2c1:
        st.metric("Período (pregões)", len(valid))
        st.metric("Média (SMA20)", f"R$ {sma20:,.2f}")
    with b2c2:
        st.metric("Preço vs. média", f"{dist:+.2f}% ({dist_label})")
        st.metric("Tendência", opp.trend_strength)
    with b2c3:
        st.metric("Máx / Mín do período", f"R$ {period_high:,.2f} / {period_low:,.2f}")
        st.metric("Força (ADX)", f"{adx_val:.0f} ({adx_label})")
    st.caption(f"Volatilidade (ATR14): R$ {atr_val:,.2f}")

    st.divider()

    # ---------- Bloco 3: Projeção técnica ----------
    st.markdown("#### Cenários técnicos prováveis")
    scenarios = [
        ("Conservador", opp.scenario_conservative, 5),
        ("Base", opp.scenario_base, 20),
        ("Otimista", opp.scenario_optimistic, 60),
    ]
    max_gain = max((s - price) for _, s, _ in scenarios if s) or 1.0
    for name, target, horizon in scenarios:
        if target is None:
            continue
        gain_pct = (target - price) / price * 100 if price else 0.0
        ratio = max(0.0, min((target - price) / max_gain, 1.0))
        st.write(f"**{name}:** R$ {target:,.2f}  ({gain_pct:+.2f}% · horizonte ~{horizon} pregões)")
        st.progress(ratio)
    st.caption("⚠ Projeção baseada em ATR e tendência. Não é previsão de preço.")

    st.divider()

    # ---------- Bloco 4: Assistente decisório (ou modo posição) ----------
    if position is not None:
        render_position_panel(symbol, position, opp, price, float(snap.get("sma20") or 0) or None)
    else:
        bullets = "\n".join(f"- {b}" for b in opp.justification_bullets) or "- Sem critérios técnicos relevantes."
        stop_txt = f"R$ {opp.invalidation_price:,.2f}" if opp.invalidation_price is not None else "—"
        alvo1 = f"R$ {opp.target_1_atr:,.2f}" if opp.target_1_atr is not None else "—"
        alvo2 = f"R$ {opp.target_2_atr:,.2f}" if opp.target_2_atr is not None else "—"
        rr_txt = f"1:{opp.risk_reward_ratio}" if opp.risk_reward_ratio else "—"

        body = (
            f"### Conduta técnica: {opp.call.replace('_', ' ')}\n\n"
            f"**Justificativa:**\n{bullets}\n\n"
            f"**Plano de ação:**\n{opp.action_plan}\n\n"
            f"**Stop técnico:** {stop_txt}  ·  **Alvo 1:** {alvo1}  ·  **Alvo 2:** {alvo2}  ·  **R:R:** {rr_txt}\n\n"
            f"**Confiança:** {opp.confidence} (score {opp.score}/17)"
        )
        if opp.call in ("COMPRA_FORTE", "COMPRA_TÉCNICA"):
            st.success("🟢 " + body)
        elif opp.call in ("MONITORAR_COMPRA", "AGUARDAR"):
            st.warning("🟡 " + body)
        else:
            st.error("🔴 " + body)

    # ---------- Bloco fundamentalista (colapsável) ----------
    render_fundamentals_block(fundamentals)

    st.plotly_chart(make_chart(df_ind), use_container_width=True)
    st.caption(
        "⚠️ Assistente técnico para uso pessoal. Não executa ordens e não constitui recomendação de investimento. "
        "Verifique liquidez, notícias, custos e seu perfil de risco antes de operar."
    )


def make_chart(df_ind: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df_ind["datetime"],
            open=df_ind["open"],
            high=df_ind["high"],
            low=df_ind["low"],
            close=df_ind["close"],
            name="Candles",
        )
    )
    fig.add_trace(go.Scatter(x=df_ind["datetime"], y=df_ind["sma20"], mode="lines", name="SMA20"))
    fig.add_trace(go.Scatter(x=df_ind["datetime"], y=df_ind["sma50"], mode="lines", name="SMA50"))
    fig.update_layout(height=620, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def analyze_one_ticker(ticker: str, profile: str, interval: str, period: str) -> dict:
    df = load_data("Yahoo/yfinance", ticker, interval, None, period)
    df_ind = add_indicators(df)
    signal = build_signal(df_ind)
    opp = build_opportunity_call(signal, profile=profile)
    last = df_ind.dropna().iloc[-1]
    bt = ema_crossover_backtest(df_ind)
    return {
        "Ticker": ticker,
        "Call técnico": opp.call,
        "Score": opp.score,
        "Confiança": opp.confidence,
        "Viés": opp.technical_bias,
        "Fechamento": round(float(last["close"]), 2),
        "RSI14": round(float(last["rsi14"]), 2),
        "ATR14": round(float(last["atr14"]), 2),
        "Invalidação": opp.invalidation_price,
        "Alvo 1 ATR": opp.target_1_atr,
        "Alvo 2 ATR": opp.target_2_atr,
        "Backtest %": bt.get("total_return_pct") if "error" not in bt else None,
        "Buy&Hold %": bt.get("buy_hold_return_pct") if "error" not in bt else None,
        "Max DD %": bt.get("max_drawdown_pct") if "error" not in bt else None,
        "Trades": bt.get("trades") if "error" not in bt else None,
        "Tese": signal.thesis,
        "Riscos": "; ".join(signal.risks),
    }


def show_radar_table(radar: pd.DataFrame) -> None:
    st.dataframe(
        radar,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Tese": st.column_config.TextColumn(width="large"),
            "Riscos": st.column_config.TextColumn(width="large"),
        },
    )


watchlists = load_watchlists()
mode = st.sidebar.radio(
    "Modo",
    [
        "Análise Completa (v5)",
        "Minhas Posições",
        "Alertas",
        "Backtest",
        "Análise individual",
        "Radar de ações",
        "Gestão de posição / Venda",
        "Minhas listas e histórico",
    ],
    index=0,
)
profile = st.sidebar.selectbox("Perfil do algoritmo", ["Conservador", "Moderado", "Agressivo"], index=2)
st.sidebar.caption("O perfil agressivo aceita sinais mais antecipados e stops mais largos por ATR, mas tende a gerar mais falsos positivos.")

if mode == "Análise Completa (v5)":
    with st.sidebar:
        st.header("Análise Completa")
        symbol_v5 = st.text_input("Ativo", value="PETR4", help="Ex: PETR4, VALE3, BOVA11, BTCUSDT")
        interval_v5 = st.selectbox("Intervalo", ["1d", "1wk", "1mo"], index=0, key="v5_interval")
        period_v5 = st.selectbox("Período", ["6mo", "1y", "2y", "5y"], index=1, key="v5_period")
        st.caption("Ações da B3: informe só o código (o .SA é adicionado automaticamente). Cripto: use sufixo USDT.")

    sym = symbol_v5.strip().upper()
    is_crypto = sym.endswith("USDT")
    src = "Binance Spot" if is_crypto else "B3 (Brapi → Yahoo)"

    try:
        if is_crypto:
            df, data_meta = load_data_with_meta(src, sym, interval_v5, 500, None)
        else:
            df, data_meta = load_data_with_meta(src, sym, interval_v5, None, period_v5)
        df_ind = add_indicators(df, interval=interval_v5)
        signal_v5 = build_signal_v5(df_ind)
        opp_v5 = build_opportunity_call_v5(signal_v5, profile=profile)
        fund_v5 = None if is_crypto else load_fundamentals_cached(sym)
        position_v5 = get_position_v5(sym)
        render_analise_completa(
            sym, df_ind, signal_v5, opp_v5, data_meta, interval_v5,
            fundamentals=fund_v5, position=position_v5,
        )

        # PDF export
        pdf_state_key = f"_pdf_{sym}"
        col_pdf, _ = st.columns([1, 3])
        with col_pdf:
            if st.button("📄 Preparar PDF", key="btn_pdf_v5"):
                with st.spinner("Gerando PDF..."):
                    st.session_state[pdf_state_key] = generate_analysis_report(
                        sym, signal_v5, opp_v5, fund_v5, position_v5, None,
                        source_label=data_meta.label,
                    )
        if pdf_state_key in st.session_state:
            st.download_button(
                "📥 Baixar análise em PDF",
                data=st.session_state[pdf_state_key],
                file_name=f"trader_ai_{sym}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                mime="application/pdf",
                key="dl_pdf_v5",
            )

        # --- Comentário de IA (Claude / OpenAI / fallback) ---
        st.divider()
        ia_state_key = f"_ia_{sym}"
        has_api = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY"))
        btn_label = "Gerar comentário IA" if has_api else "Ver leitura técnica resumida"
        btn_help = "Usa Claude API (ANTHROPIC_API_KEY) ou OpenAI (OPENAI_API_KEY). Chamada só ocorre ao clicar." if has_api else "Sem chave de API configurada — exibe resumo determinístico do sinal técnico."
        if st.button(btn_label, key="btn_ia_v5", help=btn_help):
            with st.spinner("Gerando comentário..."):
                pos_dict = asdict(position_v5) if position_v5 is not None else None
                commentary = ai_commentary(signal_v5, sym, interval_v5, position=pos_dict)
                st.session_state[ia_state_key] = commentary
                log_decision(
                    ticker=sym,
                    call=opp_v5.call,
                    score=opp_v5.score,
                    confidence=opp_v5.confidence,
                    price=float(signal_v5.snapshot.get("close") or 0),
                    timeframe=interval_v5,
                    trend_strength=opp_v5.trend_strength,
                    commentary=commentary,
                )
        if ia_state_key in st.session_state:
            with st.expander("Leitura do assistente de IA", expanded=True):
                st.write(st.session_state[ia_state_key])
                if has_api:
                    fonte = "Claude (Anthropic)" if os.getenv("ANTHROPIC_API_KEY") else "OpenAI (legado)"
                    st.caption(f"Gerado por {fonte}. Análise técnica para uso pessoal — não é recomendação de investimento.")
                else:
                    st.caption("Resumo determinístico (configure ANTHROPIC_API_KEY no .env para commentary por IA).")

    except Exception as exc:
        st.error(f"Erro ao analisar {sym}: {exc}")
        st.info(
            "Para ações da B3 informe o código (PETR4, VALE3) — o sufixo .SA é adicionado automaticamente. "
            "Para cripto use sufixo USDT (BTCUSDT). Períodos muito curtos podem não ter histórico suficiente."
        )

elif mode == "Minhas Posições":
    st.subheader("Minhas Posições")
    st.write("Cadastre suas posições ativas. A ferramenta calcula resultado, break-even e a conduta técnica sugerida para cada uma.")

    with st.expander("➕ Cadastrar / atualizar posição", expanded=False):
        with st.form("form_position"):
            pc1, pc2, pc3 = st.columns(3)
            with pc1:
                p_ticker = st.text_input("Ativo", value="PETR4")
                p_qty = st.number_input("Quantidade", min_value=1, value=100, step=1)
            with pc2:
                p_avg = st.number_input("Preço médio de compra", min_value=0.01, value=20.00, step=0.01, format="%.2f")
                p_total = st.number_input(
                    "Total investido (com custos)", min_value=0.0, value=0.0, step=10.0, format="%.2f",
                    help="Deixe 0 para estimar como quantidade × preço médio.",
                )
            with pc3:
                p_stop = st.number_input("Stop (opcional)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
                p_target = st.number_input("Alvo (opcional)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
            p_date = st.date_input("Data da compra")
            p_notes = st.text_input("Anotações", value="")
            submitted = st.form_submit_button("Salvar posição", type="primary")
        if submitted:
            tk = p_ticker.strip().upper()
            total = p_total if p_total > 0 else round(p_qty * p_avg, 2)
            upsert_position_v5(Position(
                ticker=tk,
                quantity=int(p_qty),
                avg_buy_price=float(p_avg),
                total_invested=float(total),
                buy_date=str(p_date),
                stop_price=(float(p_stop) or None),
                target_price=(float(p_target) or None),
                notes=p_notes,
            ))
            st.success(f"Posição em {tk} salva.")
            st.rerun()

    positions_df = load_positions_v5()
    if positions_df.empty:
        st.info("Nenhuma posição cadastrada ainda. Use o formulário acima.")
    else:
        for _, row in positions_df.iterrows():
            tk = str(row["ticker"]).upper()
            pos = get_position_v5(tk)
            if pos is None:
                continue
            with st.container(border=True):
                head = st.columns([3, 1])
                head[0].markdown(f"### {tk}")
                if head[1].button("Remover", key=f"del_{tk}"):
                    delete_position_v5(tk)
                    st.rerun()
                metrics = get_current_metrics(tk, profile=profile)
                if metrics is None:
                    st.warning("Não foi possível obter o preço atual deste ativo agora.")
                    st.caption(f"Comprado a R$ {pos.avg_buy_price:,.2f} · {pos.quantity} unid.")
                    continue
                render_position_panel(tk, pos, metrics["opp"], metrics["price"], metrics["sma20"])

    st.caption("⚠️ Assistente técnico para uso pessoal. Não executa ordens e não constitui recomendação de investimento.")

elif mode == "Alertas":
    st.subheader("Alertas")
    st.write("Crie alertas de preço, RSI ou mudança de call. A verificação busca o preço atual de cada ativo com alerta ativo.")

    alerts = load_alerts()

    if st.button("🔄 Verificar alertas agora", type="primary"):
        active_now = [a for a in alerts if a.ativo]
        tickers = sorted({a.ticker for a in active_now})
        current: dict[str, dict] = {}
        if tickers:
            prog = st.progress(0)
            for i, tk in enumerate(tickers, start=1):
                m = get_current_metrics(tk, profile=profile)
                if m:
                    current[tk] = {"price": m["price"], "rsi": m["rsi"], "call": m["call"]}
                prog.progress(i / max(len(tickers), 1))
        triggered = check_alerts(alerts, current)
        save_alerts(alerts)
        if triggered:
            for a in triggered:
                st.error(f"🔔 {a.ticker}: {ALERT_LABELS.get(a.tipo, a.tipo)} {a.valor:g} — {a.mensagem or 'disparado'}")
        else:
            st.info("Nenhum alerta novo disparado.")
        alerts = load_alerts()

    with st.expander("➕ Adicionar alerta", expanded=False):
        with st.form("form_alert"):
            ac1, ac2 = st.columns(2)
            with ac1:
                a_ticker = st.text_input("Ativo", value="PETR4")
                a_tipo = st.selectbox("Tipo", ALERT_TYPES, format_func=lambda t: ALERT_LABELS.get(t, t))
            with ac2:
                a_valor = st.number_input("Valor (preço ou nível de RSI)", min_value=0.0, value=0.0, step=0.01, format="%.2f")
                a_msg = st.text_input("Mensagem (opcional)", value="")
            submitted_a = st.form_submit_button("Criar alerta", type="primary")
        if submitted_a:
            tk = a_ticker.strip().upper()
            ref = None
            if a_tipo == "call_mudou":
                m = get_current_metrics(tk, profile=profile)
                ref = m["call"] if m else None
            add_alert(tk, a_tipo, a_valor, mensagem=a_msg, referencia=ref)
            st.success(f"Alerta criado para {tk}." + (f" Referência de call: {ref}." if ref else ""))
            st.rerun()

    alerts = load_alerts()
    active = [(i, a) for i, a in enumerate(alerts) if a.ativo]
    st.markdown("#### Alertas ativos")
    if not active:
        st.info("Nenhum alerta ativo.")
    else:
        for i, a in active:
            cols = st.columns([5, 1])
            descr = f"**{a.ticker}** · {ALERT_LABELS.get(a.tipo, a.tipo)} {a.valor:g}"
            if a.referencia:
                descr += f" · ref: {a.referencia}"
            if a.mensagem:
                descr += f" · {a.mensagem}"
            cols[0].write(descr)
            if cols[1].button("Remover", key=f"rm_{i}"):
                remove_alert(i)
                st.rerun()

    st.markdown("#### Histórico de disparos (últimos 30 dias)")
    cutoff = datetime.now() - timedelta(days=30)
    rows = []
    for a in alerts:
        if not a.disparado_em:
            continue
        try:
            dt = datetime.strptime(a.disparado_em, "%Y-%m-%d %H:%M:%S")
        except Exception:
            dt = None
        if dt is None or dt >= cutoff:
            rows.append({
                "Ativo": a.ticker,
                "Tipo": ALERT_LABELS.get(a.tipo, a.tipo),
                "Valor": a.valor,
                "Disparado em": a.disparado_em,
                "Mensagem": a.mensagem,
            })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Sem disparos nos últimos 30 dias.")

    st.caption("⚠️ Assistente técnico para uso pessoal. Não executa ordens e não constitui recomendação de investimento.")

elif mode == "Backtest":
    st.subheader("Comparativo de estratégias — Backtest")
    st.write("Compara 4 estratégias técnicas no mesmo histórico: EMA Crossover, MACD Sinal, RSI Reversão e Bollinger Reversão à Média.")

    with st.sidebar:
        st.header("Backtest")
        bt_ticker = st.text_input("Ativo", value="PETR4", key="bt_ticker_input")
        bt_interval = st.selectbox("Intervalo", ["1d", "1wk"], index=0, key="bt_interval")
        bt_period = st.selectbox("Período", ["1y", "2y", "5y"], index=1, key="bt_period")
        bt_fee = st.number_input("Custo por operação (bps)", min_value=0.0, value=5.0, step=0.5, key="bt_fee",
                                  help="Custos de corretagem + emolumentos em pontos-base (5 bps = 0,05%).")
        bt_rsi_buy = st.slider("RSI Reversão — nível de compra", 20, 50, 35, key="bt_rsi_buy")
        bt_rsi_sell = st.slider("RSI Reversão — nível de venda", 50, 80, 65, key="bt_rsi_sell")
        bt_run = st.button("Rodar backtest", type="primary", key="bt_run")

    if bt_run:
        sym_bt = bt_ticker.strip().upper()
        is_crypto_bt = sym_bt.endswith("USDT")
        src_bt = "Binance Spot" if is_crypto_bt else "B3 (Brapi → Yahoo)"
        try:
            with st.spinner(f"Carregando {sym_bt} e rodando 4 estratégias..."):
                if is_crypto_bt:
                    df_bt, _ = load_data_with_meta(src_bt, sym_bt, bt_interval, 500, None)
                else:
                    df_bt, _ = load_data_with_meta(src_bt, sym_bt, bt_interval, None, bt_period)
                df_bt_ind = add_indicators(df_bt, interval=bt_interval)
                strategies = {
                    "EMA Crossover": ema_crossover_backtest(df_bt_ind, fee_bps=bt_fee),
                    "MACD Sinal": macd_signal_backtest(df_bt_ind, fee_bps=bt_fee),
                    "RSI Reversão": rsi_reversal_backtest(df_bt_ind, buy_rsi=bt_rsi_buy, sell_rsi=bt_rsi_sell, fee_bps=bt_fee),
                    "Bollinger": bollinger_reversion_backtest(df_bt_ind, fee_bps=bt_fee),
                }
                st.session_state["_bt_results"] = strategies
                st.session_state["_bt_ticker"] = sym_bt
        except Exception as exc:
            st.error(f"Erro ao rodar backtest: {exc}")

    strategies = st.session_state.get("_bt_results")
    bt_ticker_disp = st.session_state.get("_bt_ticker", "")

    if strategies:
        st.subheader(f"Resultados — {bt_ticker_disp}")

        # Tabela comparativa
        rows = []
        for display_name, res in strategies.items():
            if "error" in res:
                rows.append({
                    "Estratégia": res.get("estrategia", display_name),
                    "Período": "—",
                    "Operações": "—",
                    "Win Rate %": "—",
                    "Retorno %": "—",
                    "Buy & Hold %": "—",
                    "Max DD %": "—",
                    "Sharpe": "—",
                    "Maior ganho %": "—",
                    "Maior perda %": "—",
                    "Erro": res["error"],
                })
            else:
                rows.append({
                    "Estratégia": res.get("estrategia", display_name),
                    "Período": res.get("periodo", "—"),
                    "Operações": res.get("total_operacoes", "—"),
                    "Win Rate %": res.get("win_rate_pct", "—"),
                    "Retorno %": res.get("retorno_total_pct", "—"),
                    "Buy & Hold %": res.get("retorno_buy_hold_pct", "—"),
                    "Max DD %": res.get("drawdown_maximo_pct", "—"),
                    "Sharpe": res.get("sharpe_simplificado", "—"),
                    "Maior ganho %": res.get("maior_ganho_pct", "—"),
                    "Maior perda %": res.get("maior_perda_pct", "—"),
                })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # Curvas de capital
        st.subheader("Curvas de capital (equity curves)")
        eq_frames: dict[str, pd.Series] = {}
        bh_series = None
        for display_name, res in strategies.items():
            if "error" in res:
                continue
            ec = res.get("equity_curve")
            if ec is not None and isinstance(ec, pd.DataFrame) and "equity" in ec.columns:
                idx = pd.to_datetime(ec["datetime"])
                eq_frames[res.get("estrategia", display_name)] = ec.set_index(idx)["equity"]
                if bh_series is None and "buy_hold" in ec.columns:
                    bh_series = ec.set_index(idx)["buy_hold"]

        if eq_frames:
            eq_df = pd.DataFrame(eq_frames)
            if bh_series is not None:
                eq_df["Buy & Hold"] = bh_series
            st.line_chart(eq_df, height=400)

        st.caption(
            "⚠️ Backtest usa dados históricos com custo simplificado. "
            "Resultados passados não garantem performance futura. "
            "A estratégia Bollinger tende a gerar menos operações em tendências fortes."
        )

elif mode == "Análise individual":
    with st.sidebar:
        st.header("Dados")
        source = st.selectbox("Fonte", ["Binance Spot", "B3 (Brapi → Yahoo)"], index=1)
        if source == "Binance Spot":
            symbol = st.text_input("Símbolo", "BTCUSDT")
            interval = st.selectbox("Intervalo", ["15m", "30m", "1h", "4h", "1d"], index=2)
            limit = st.slider("Candles", min_value=100, max_value=1000, value=500, step=50)
            period = None
        else:
            symbol = st.text_input("Ticker", "PETR4.SA")
            interval = st.selectbox("Intervalo", ["1d", "1wk", "1mo"], index=0)
            period = st.selectbox("Período", ["3mo", "6mo", "1y", "2y", "5y"], index=2)
            limit = None

        target_list = st.selectbox("Lista para salvar este ativo", list(watchlists.keys()), index=0)
        add_single = st.button("Adicionar ativo à lista")
        run = st.button("Atualizar análise", type="primary")

    if add_single:
        add_tickers_to_watchlist(target_list, [symbol.strip().upper()])
        st.success(f"{symbol.strip().upper()} adicionado à lista '{target_list}'.")
        st.rerun()

    try:
        df, data_meta = load_data_with_meta(source, symbol, interval, limit, period)
        render_source_badge(data_meta)
        df_ind = add_indicators(df)
        signal = build_signal(df_ind)
        opportunity = build_opportunity_call(signal, profile=profile)

        last = df_ind.dropna().iloc[-1]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Fechamento", f"{last['close']:,.2f}")
        c2.metric("RSI 14", f"{last['rsi14']:.2f}")
        c3.metric("ATR 14", f"{last['atr14']:,.2f}")
        c4.metric("Sinal", signal.signal)
        c5.metric("Call técnico", opportunity.call)

        st.plotly_chart(make_chart(df_ind), use_container_width=True)

        left, right = st.columns([1.2, 1])
        with left:
            st.subheader("Comentário técnico")
            ia_key_ind = f"_ia_ind_{symbol}_{interval}"
            if st.button("Gerar comentário IA", key="btn_ia_ind"):
                with st.spinner("Gerando..."):
                    commentary_ind = ai_commentary(signal, symbol=symbol, timeframe=interval)
                    st.session_state[ia_key_ind] = commentary_ind
                    log_decision(
                        ticker=symbol,
                        call=opportunity.call,
                        score=opportunity.score,
                        confidence=opportunity.confidence,
                        price=float(signal.snapshot.get("close") or 0),
                        timeframe=interval,
                        commentary=commentary_ind,
                    )
            if ia_key_ind in st.session_state:
                st.write(st.session_state[ia_key_ind])
            else:
                st.caption("Clique em 'Gerar comentário IA' para obter a leitura do sinal técnico.")

            st.subheader("Plano técnico do algoritmo")
            st.write(f"**Viés técnico:** {opportunity.technical_bias}")
            st.write(f"**Preço de referência:** {opportunity.reference_price}")
            st.write(f"**Invalidação técnica:** {opportunity.invalidation_price}")
            st.write(f"**Alvo técnico 1 por ATR:** {opportunity.target_1_atr}")
            st.write(f"**Alvo técnico 2 por ATR:** {opportunity.target_2_atr}")
            st.caption(opportunity.risk_reward_note)

        with right:
            st.subheader("Snapshot")
            st.json(signal.snapshot)
            st.warning("Call técnico para apoio decisório próprio. Valide liquidez, notícias, fundamentos, custos, stop e tamanho da posição.")

        st.subheader("Backtest simples: EMA12 x EMA26")
        bt = ema_crossover_backtest(df_ind)
        if "error" in bt:
            st.info(bt["error"])
        else:
            b1, b2, b3, b4 = st.columns(4)
            b1.metric("Retorno estratégia", f"{bt['total_return_pct']:.2f}%")
            b2.metric("Buy & Hold", f"{bt['buy_hold_return_pct']:.2f}%")
            b3.metric("Max drawdown", f"{bt['max_drawdown_pct']:.2f}%")
            b4.metric("Trades", bt["trades"])
            eq = bt["equity_curve"].set_index("datetime")
            st.line_chart(eq)

        with st.expander("Dados brutos"):
            st.dataframe(df_ind.tail(100), use_container_width=True)

    except Exception as exc:
        st.error(f"Erro ao carregar/analisar dados: {exc}")
        st.info("Confira conexão, ticker, período e intervalo. Para ações da B3 via yfinance, use sufixo .SA, por exemplo PETR4.SA.")

elif mode == "Radar de ações":
    st.sidebar.header("Radar v5")
    list_names = list(watchlists.keys())
    selected_list = st.sidebar.selectbox("Carregar lista salva", list_names, index=0)
    use_default = st.sidebar.checkbox(f"Usar lista padrão v5 ({len(DEFAULT_B3_RADAR)} ativos)", value=False)
    default_raw = ", ".join(DEFAULT_B3_RADAR if use_default else watchlists.get(selected_list, []))
    tickers_raw = st.sidebar.text_area("Tickers", value=default_raw, height=160, key=f"tickers_{selected_list}_{use_default}")

    save_col1, save_col2 = st.sidebar.columns(2)
    with save_col1:
        save_current = st.button("Salvar lista")
    with save_col2:
        refresh_lists = st.button("Recarregar")

    new_list_name = st.sidebar.text_input("Salvar como nova lista", value="")
    save_as = st.sidebar.button("Criar/atualizar nova lista")

    interval = st.sidebar.selectbox("Intervalo", ["1d", "1wk", "1mo"], index=0)
    period = st.sidebar.selectbox("Período", ["3mo", "6mo", "1y", "2y", "5y"], index=2)

    st.sidebar.subheader("Filtros")
    asset_type_options = ["Ação ON", "Ação PN", "FII", "ETF", "BDR", "Cripto"]
    filtro_tipo = st.sidebar.multiselect("Tipo de ativo", asset_type_options, default=[])
    filtro_volume_min = st.sidebar.number_input("Volume financeiro mínimo (R$ mi/dia)", min_value=0.0, value=0.0, step=1.0)
    filtro_call = st.sidebar.multiselect("Calls a exibir", CALLS_V5, default=CALLS_V5)
    filtro_rsi_max = st.sidebar.slider("RSI máximo (evitar sobrecompra)", 50, 80, 80)
    filtro_adx_min = st.sidebar.slider("ADX mínimo (força de tendência)", 0, 40, 0)
    filtro_score_min = st.sidebar.slider("Score técnico mínimo", -6, 17, -6)
    run_radar = st.sidebar.button("Rodar radar", type="primary")

    if save_current:
        upsert_watchlist(selected_list, normalize_tickers(tickers_raw, b3=True))
        st.sidebar.success(f"Lista '{selected_list}' salva.")
        st.rerun()

    if save_as:
        if not new_list_name.strip():
            st.sidebar.error("Informe um nome para a nova lista.")
        else:
            upsert_watchlist(new_list_name.strip(), normalize_tickers(tickers_raw, b3=True))
            st.sidebar.success(f"Lista '{new_list_name.strip()}' salva.")
            st.rerun()

    if refresh_lists:
        st.rerun()

    st.subheader("Radar de oportunidades v5")
    st.write("Varre a lista com o motor v5, classifica o tipo de ativo e ordena por **Radar score** (técnico + risco:retorno + confiança − esticamento).")

    if run_radar:
        tickers = normalize_tickers(tickers_raw, b3=True)
        if len(tickers) > 60:
            st.warning("Para evitar demora, analise no máximo 60 tickers por rodada.")
            tickers = tickers[:60]

        rows: list[dict] = []
        errors: list[str] = []
        progress = st.progress(0)
        for idx, ticker in enumerate(tickers, start=1):
            try:
                rows.append(analyze_ticker_v5(ticker, profile=profile, interval=interval, period=period))
            except Exception as exc:
                errors.append(f"{ticker}: {exc}")
            progress.progress(idx / max(len(tickers), 1))

        if not rows:
            st.error("Nenhum ativo foi analisado. Confira os tickers e a conexão.")
        else:
            radar_all = pd.DataFrame(rows).sort_values(by=["Radar score", "Score"], ascending=False)
            st.session_state["last_radar"] = radar_all
            st.session_state["last_radar_meta"] = {
                "profile": profile,
                "interval": interval,
                "period": period,
                "source_list": "Lista padrão v5" if use_default else selected_list,
            }
            st.session_state["last_errors"] = errors

    radar_all = st.session_state.get("last_radar")
    errors = st.session_state.get("last_errors", [])
    meta = st.session_state.get("last_radar_meta", {"profile": profile, "interval": interval, "period": period, "source_list": selected_list})

    if isinstance(radar_all, pd.DataFrame) and not radar_all.empty:
        # Aplica filtros do painel lateral
        radar = radar_all.copy()
        if filtro_tipo:
            radar = radar[radar["Tipo"].isin(filtro_tipo)]
        radar = radar[radar["Vol R$ mi"] >= filtro_volume_min]
        radar = radar[radar["Call"].isin(filtro_call)]
        radar = radar[radar["RSI14"] <= filtro_rsi_max]
        radar = radar[radar["ADX"] >= filtro_adx_min]
        radar = radar[radar["Score"] >= filtro_score_min]

        # Comparação com a varredura anterior (mudança de call)
        call_rank = {c: i for i, c in enumerate(reversed(CALLS_V5))}  # COMPRA_FORTE=mais alto
        prev_calls = st.session_state.get("radar_prev_calls", {})
        curr_calls = dict(zip(radar_all["Ticker"], radar_all["Call"]))
        improved, worsened, into_buy = 0, 0, []
        for tk, call in curr_calls.items():
            if tk in prev_calls and prev_calls[tk] != call:
                if call_rank.get(call, 0) > call_rank.get(prev_calls[tk], 0):
                    improved += 1
                    if call in ("COMPRA_TÉCNICA", "COMPRA_FORTE") and prev_calls[tk] in ("AGUARDAR", "MONITORAR_COMPRA"):
                        into_buy.append(tk)
                else:
                    worsened += 1

        cmp1, cmp2, cmp3 = st.columns(3)
        cmp1.metric("Ativos exibidos", len(radar))
        cmp2.metric("Melhoraram de call", improved)
        cmp3.metric("Pioraram de call", worsened)
        if into_buy:
            st.success("🔔 Mudou para compra: " + ", ".join(into_buy))

        st.dataframe(radar, use_container_width=True, hide_index=True)

        csv = radar.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Baixar radar atual em CSV", data=csv, file_name="radar_oportunidades_v5.csv", mime="text/csv")

        st.divider()
        st.subheader("Memória do radar")
        m1, m2 = st.columns([1, 1])
        with m1:
            top_n = st.slider("Quantidade de ativos para salvar no histórico", 5, 30, 15)
            if st.button("Salvar principais ações do momento"):
                path = save_radar_snapshot(
                    radar,
                    profile=meta.get("profile", profile),
                    interval=meta.get("interval", interval),
                    period=meta.get("period", period),
                    source_list=meta.get("source_list", selected_list),
                    top_n=top_n,
                )
                st.success(f"Top {top_n} salvo no histórico local: {path.name}")
                st.session_state["radar_prev_calls"] = curr_calls
        with m2:
            top_tickers = radar["Ticker"].head(20).tolist()
            picks = st.multiselect("Adicionar estes ativos a uma lista", top_tickers, default=top_tickers[:5])
            dest_list = st.selectbox("Lista de destino", list(load_watchlists().keys()), index=0, key="dest_list_top")
            if st.button("Adicionar selecionados à lista"):
                add_tickers_to_watchlist(dest_list, picks)
                st.success(f"{len(picks)} ativo(s) adicionado(s) à lista '{dest_list}'.")
                st.rerun()

        # Atualiza a referência para a próxima comparação
        st.session_state["radar_prev_calls"] = curr_calls

        st.info("Ordene pelo Radar score, mas valide liquidez, notícia relevante, tamanho da posição e invalidação antes de operar.")

    if errors:
        with st.expander("Erros de tickers/dados"):
            for err in errors:
                st.write("- " + err)

elif mode == "Gestão de posição / Venda":
    st.subheader("Gestão de posição e preço mínimo de venda")
    st.write("Calcule por quanto precisa vender para cobrir compra, venda, custos B3, taxa operacional XP e, se desejar, IR estimado. Use os valores finais da nota de corretagem para ajustar a simulação.")

    profiles = [
        "XP Digital — Ações Swing Trade (R$ 4,90/ordem)",
        "XP Escritório credenciado — Swing Trade (R$ 18,90/ordem)",
        "XP Day Trade sem RLP (R$ 2,90/ordem)",
        "XP Day Trade com RLP (R$ 0,00/ordem)",
        "Personalizado",
    ]

    left, right = st.columns([1, 1])
    with left:
        st.markdown("### Entrada")
        ticker_pos = st.text_input("Ativo", value="AZZA3.SA")
        quantity = st.number_input("Quantidade", min_value=1, value=100, step=1)
        buy_price = st.number_input("Preço de compra por ação", min_value=0.01, value=20.10, step=0.01, format="%.4f")
        current_price = st.number_input("Preço atual / preço simulado de venda", min_value=0.00, value=20.10, step=0.01, format="%.4f")
        profile_cost = st.selectbox("Tabela de custos", profiles, index=0)
        defaults = default_brokerage_profile(profile_cost)

    with right:
        st.markdown("### Custos")
        buy_brokerage = st.number_input("Corretagem compra (R$)", min_value=0.0, value=float(defaults["buy_brokerage"]), step=0.10, format="%.2f")
        sell_brokerage = st.number_input("Corretagem venda (R$)", min_value=0.0, value=float(defaults["sell_brokerage"]), step=0.10, format="%.2f")
        b3_rate_pct = st.number_input("Custos B3 estimados (% sobre financeiro)", min_value=0.0, value=float(defaults["b3_rate_pct"]), step=0.001, format="%.4f")
        xp_operational_rate_pct = st.number_input("Taxa operacional XP estimada (%)", min_value=0.0, value=5.90, step=0.10, format="%.2f")
        ir_mode = st.selectbox("IR no alvo", ["Não considerar IR", "Ações swing tributável — 15%", "Day trade — 20%", "Personalizado"])
        if ir_mode == "Ações swing tributável — 15%":
            ir_rate_pct = 15.0
        elif ir_mode == "Day trade — 20%":
            ir_rate_pct = 20.0
        elif ir_mode == "Personalizado":
            ir_rate_pct = st.number_input("Alíquota IR personalizada (%)", min_value=0.0, max_value=50.0, value=15.0, step=0.5, format="%.2f")
        else:
            ir_rate_pct = 0.0

    fees = FeeParams(
        buy_brokerage=buy_brokerage,
        sell_brokerage=sell_brokerage,
        b3_rate_pct=b3_rate_pct,
        xp_operational_rate_pct=xp_operational_rate_pct,
        ir_rate_pct=ir_rate_pct,
    )

    buy_calc = buy_total_cost(int(quantity), float(buy_price), fees)
    current_sim = sell_simulation(int(quantity), float(current_price), buy_calc["total_investido"], fees)

    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Compra bruta", f"R$ {buy_calc['financeiro']:,.2f}")
    c2.metric("Custo compra", f"R$ {buy_calc['total_custos']:,.2f}")
    c3.metric("Total investido", f"R$ {buy_calc['total_investido']:,.2f}")
    c4.metric("PM com custos", f"R$ {buy_calc['preco_medio_com_custos']:,.4f}")

    st.markdown("### Preço de venda necessário")
    t1, t2 = st.columns([1, 1])
    with t1:
        target_pct = st.number_input("Lucro líquido alvo (%)", min_value=0.0, value=0.0, step=0.5, format="%.2f")
    with t2:
        target_abs = st.number_input("Ou lucro líquido alvo em R$", min_value=0.0, value=0.0, step=10.0, format="%.2f")
    target_profit = target_abs if target_abs > 0 else buy_calc["total_investido"] * (target_pct / 100.0)

    be = required_sell_price(int(quantity), buy_calc["total_investido"], fees, target_profit_after_ir=0.0)
    target = required_sell_price(int(quantity), buy_calc["total_investido"], fees, target_profit_after_ir=target_profit)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Preço para zerar custos", f"R$ {be['preco_venda_necessario']:,.4f}")
    k2.metric("Preço para alvo", f"R$ {target['preco_venda_necessario']:,.4f}")
    k3.metric("Lucro líquido no preço atual", f"R$ {current_sim['lucro_liquido_estimado']:,.2f}")
    k4.metric("Rentab. líquida atual", f"{current_sim['rentabilidade_liquida_pct']:,.2f}%")

    st.code(f"ORDEM MANUAL SUGERIDA: VENDER {ticker_pos.upper()} | QTD {int(quantity)} | PREÇO LIMITE mínimo R$ {target['preco_venda_necessario']:,.4f}", language="text")

    table = pd.DataFrame([
        {"Cenário": "Compra", **buy_calc},
        {"Cenário": "Venda no preço atual", **current_sim},
        {"Cenário": "Venda para zerar custos", **be},
        {"Cenário": "Venda para alvo", **target},
    ])
    st.dataframe(table, use_container_width=True, hide_index=True)

    with st.expander("Detalhamento e fórmula prática"):
        st.write("Preço de venda necessário = valor líquido desejado + custos fixos de venda, dividido pela quantidade ajustada pelos custos variáveis.")
        st.write("Custos estimados = corretagem + custos B3 + taxa operacional XP sobre corretagem e custos B3.")
        st.warning("A nota de corretagem da XP é a fonte final. Ajuste corretagem, B3 e taxa operacional conforme sua conta, assessor, tipo de ordem, RLP e ativo.")

    st.divider()
    st.markdown("### Salvar posição em memória local")
    notes = st.text_input("Observações", value="")
    if st.button("Salvar esta posição"):
        save_position({
            "Ticker": ticker_pos.upper(),
            "Quantidade": int(quantity),
            "Preço compra": round(float(buy_price), 4),
            "Compra bruta": buy_calc["financeiro"],
            "Custos compra": buy_calc["total_custos"],
            "Total investido": buy_calc["total_investido"],
            "Preço zerar custos": be["preco_venda_necessario"],
            "Preço alvo": target["preco_venda_necessario"],
            "Lucro alvo R$": round(target_profit, 2),
            "Perfil custos": profile_cost,
            "Observações": notes,
        })
        st.success("Posição salva em data/positions.csv")

    positions = load_positions()
    if not positions.empty:
        st.markdown("### Posições salvas")
        st.dataframe(positions.sort_values(by="Criado em", ascending=False), use_container_width=True, hide_index=True)
        csv_pos = positions.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Baixar posições em CSV", data=csv_pos, file_name="posicoes_trader_ai.csv", mime="text/csv")

elif mode == "Minhas listas e histórico":
    st.subheader("Minhas listas e histórico")
    st.write("Aqui a ferramenta guarda localmente suas listas de foco e os principais resultados do radar.")

    watchlists = load_watchlists()
    tabs = st.tabs(["Listas salvas", "Histórico do radar", "Histórico de análises"])

    with tabs[0]:
        selected = st.selectbox("Editar lista", list(watchlists.keys()), index=0)
        edited = st.text_area("Tickers", value=", ".join(watchlists.get(selected, [])), height=180, key=f"edit_{selected}")
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            if st.button("Salvar alterações"):
                upsert_watchlist(selected, normalize_tickers(edited, b3=True))
                st.success(f"Lista '{selected}' atualizada.")
                st.rerun()
        with c2:
            if st.button("Excluir lista"):
                delete_watchlist(selected)
                st.warning(f"Lista '{selected}' excluída.")
                st.rerun()
        with c3:
            new_name = st.text_input("Nova lista", value="", key="new_list_in_manage")
            if st.button("Criar lista"):
                if not new_name.strip():
                    st.error("Informe um nome.")
                else:
                    upsert_watchlist(new_name.strip(), [])
                    st.success(f"Lista '{new_name.strip()}' criada.")
                    st.rerun()

        st.write("Resumo das listas:")
        summary = pd.DataFrame(
            [{"Lista": name, "Qtd. ativos": len(tickers), "Tickers": ", ".join(tickers)} for name, tickers in watchlists.items()]
        )
        st.dataframe(summary, use_container_width=True, hide_index=True)

    with tabs[1]:
        hist = load_radar_history()
        if hist.empty:
            st.info("Ainda não há histórico. Rode o radar e clique em 'Salvar principais ações do momento'.")
        else:
            st.metric("Registros salvos", len(hist))
            st.dataframe(hist.sort_values(by="Salvo em", ascending=False), use_container_width=True, hide_index=True)
            csv = hist.to_csv(index=False).encode("utf-8-sig")
            st.download_button("Baixar histórico completo", data=csv, file_name="historico_radar.csv", mime="text/csv")

    with tabs[2]:
        log_entries = load_decisions_log(limit=100)
        if not log_entries:
            st.info(
                "Sem análises registradas. Clique em 'Gerar comentário IA' (ou 'Ver leitura técnica resumida') "
                "na Análise Completa para iniciar o histórico."
            )
        else:
            st.metric("Análises registradas", len(log_entries))
            df_log = pd.DataFrame(log_entries)
            col_order = ["datetime", "ticker", "timeframe", "call", "score", "confidence", "trend_strength", "price", "commentary"]
            df_log = df_log[[c for c in col_order if c in df_log.columns]]
            st.dataframe(
                df_log,
                use_container_width=True,
                hide_index=True,
                column_config={"commentary": st.column_config.TextColumn("Comentário (início)", width="large")},
            )
            csv_log = df_log.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Baixar histórico de análises",
                data=csv_log,
                file_name="historico_analises.csv",
                mime="text/csv",
            )
