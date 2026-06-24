from __future__ import annotations

from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_PAGE_W = A4[0] - 4 * cm


def _get_styles():
    base = getSampleStyleSheet()
    title = ParagraphStyle("tr_title", parent=base["Heading1"], fontSize=15, spaceAfter=4)
    section = ParagraphStyle(
        "tr_section", parent=base["Heading2"], fontSize=11, spaceAfter=3,
        textColor=colors.HexColor("#1b5e20"),
    )
    body = base["Normal"]
    caption = ParagraphStyle("tr_caption", parent=base["Normal"], fontSize=8, textColor=colors.grey)
    return title, section, body, caption


def _table(data: list[list], col_widths: list | None = None) -> Table:
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1b5e20")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _fmt(v, suffix: str = "") -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.2f}{suffix}"
    return "—"


def generate_analysis_report(
    ticker: str,
    signal,
    opp,
    fundamentals: dict | None,
    position,
    chart_image_bytes: bytes | None,
    source_label: str = "Brapi",
) -> bytes:
    """Gera PDF completo da análise técnica. Retorna bytes prontos para download."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        rightMargin=2 * cm, leftMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    title_s, section_s, body_s, caption_s = _get_styles()
    W = _PAGE_W
    story = []

    # --- Cabeçalho ---
    story.append(Paragraph(f"Trader AI — Análise Técnica: {ticker}", title_s))
    story.append(Paragraph(
        f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}  ·  Fonte: {source_label}",
        caption_s,
    ))
    story.append(Spacer(1, 0.3 * cm))

    snap = signal.snapshot
    price = float(snap.get("close") or 0.0)

    # --- Bloco 1: Situação atual ---
    story.append(Paragraph("Situação atual", section_s))
    story.append(_table([
        ["Indicador", "Valor"],
        ["Preço atual", f"R$ {price:,.2f}" if price else "—"],
        ["Score técnico", f"{opp.score}/17"],
        ["Confiança", opp.confidence],
        ["Conduta (Call)", opp.call.replace("_", " ")],
    ], [W * 0.5, W * 0.5]))
    story.append(Spacer(1, 0.3 * cm))

    # --- Bloco 2: Leitura do período ---
    story.append(Paragraph("Leitura do período", section_s))
    sma20 = float(snap.get("sma20") or 0)
    adx_val = float(snap.get("adx") or 0)
    atr_val = float(snap.get("atr14") or 0)
    dist = float(getattr(opp, "distance_from_sma20_pct", 0.0))
    story.append(_table([
        ["Indicador", "Valor"],
        ["SMA20", f"R$ {sma20:,.2f}" if sma20 else "—"],
        ["Preço vs. SMA20", f"{dist:+.2f}%"],
        ["Tendência", getattr(opp, "trend_strength", "—")],
        ["Força da tendência (ADX)", f"{adx_val:.1f}"],
        ["Volatilidade (ATR14)", f"R$ {atr_val:,.2f}"],
    ], [W * 0.5, W * 0.5]))
    story.append(Spacer(1, 0.3 * cm))

    # --- Bloco 3: Cenários de projeção ---
    story.append(Paragraph("Cenários técnicos prováveis", section_s))
    b3 = [["Cenário", "Preço alvo", "Ganho estimado", "Horizonte"]]
    for label, target, horizon in [
        ("Conservador", getattr(opp, "scenario_conservative", None), "~5 pregões"),
        ("Base", getattr(opp, "scenario_base", None), "~20 pregões"),
        ("Otimista", getattr(opp, "scenario_optimistic", None), "~60 pregões"),
    ]:
        if target is not None:
            gain = (target - price) / price * 100 if price else 0.0
            b3.append([label, f"R$ {target:,.2f}", f"{gain:+.1f}%", horizon])
    if len(b3) > 1:
        story.append(_table(b3, [W * 0.22, W * 0.28, W * 0.25, W * 0.25]))
    story.append(Paragraph("Projeção baseada em ATR e tendência. Não é previsão de preço.", caption_s))
    story.append(Spacer(1, 0.3 * cm))

    # --- Bloco 4: Conduta e plano ---
    story.append(Paragraph("Conduta do assistente decisório", section_s))
    stop_txt = f"R$ {opp.invalidation_price:,.2f}" if opp.invalidation_price else "—"
    alvo1 = f"R$ {opp.target_1_atr:,.2f}" if opp.target_1_atr else "—"
    alvo2 = f"R$ {opp.target_2_atr:,.2f}" if opp.target_2_atr else "—"
    rr_txt = f"1:{opp.risk_reward_ratio}" if getattr(opp, "risk_reward_ratio", None) else "—"
    story.append(_table([
        ["Item", "Valor"],
        ["Conduta", opp.call.replace("_", " ")],
        ["Stop técnico", stop_txt],
        ["Alvo 1", alvo1],
        ["Alvo 2", alvo2],
        ["Risco:Retorno", rr_txt],
    ], [W * 0.4, W * 0.6]))

    bullets = getattr(opp, "justification_bullets", []) or []
    if bullets:
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("Justificativa:", body_s))
        for b in bullets:
            story.append(Paragraph(f"• {b}", body_s))

    action_plan = getattr(opp, "action_plan", "") or ""
    if action_plan:
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph("Plano de ação:", body_s))
        for line in action_plan.split("\n"):
            if line.strip():
                story.append(Paragraph(line, body_s))
    story.append(Spacer(1, 0.3 * cm))

    # --- Bloco 5: Fundamentalista (opcional) ---
    if fundamentals:
        story.append(Paragraph("Contexto fundamentalista", section_s))
        b5 = [["Indicador", "Valor"]]
        sector = fundamentals.get("sector")
        if sector:
            b5.append(["Setor", str(sector)])
        for key, label, suffix in [
            ("pl", "P/L", ""),
            ("pvp", "P/VP", ""),
            ("dy", "Dividend Yield", "%"),
            ("roe", "ROE", "%"),
            ("net_margin", "Margem líquida", "%"),
            ("debt_equity", "Dívida/PL", ""),
        ]:
            v = fundamentals.get(key)
            if v is not None:
                b5.append([label, _fmt(v, suffix)])
        story.append(_table(b5, [W * 0.5, W * 0.5]))
        story.append(Spacer(1, 0.3 * cm))

    # --- Bloco 6: Posição ativa (opcional) ---
    if position is not None:
        story.append(Paragraph("Sua posição ativa", section_s))
        qty = int(position.quantity)
        avg = float(position.avg_buy_price)
        invested = float(position.total_invested)
        curr_val = price * qty
        pnl_abs = curr_val - invested
        pnl_pct = (price - avg) / avg * 100 if avg else 0.0
        b6 = [
            ["Item", "Valor"],
            ["Preço médio de compra", f"R$ {avg:,.2f}"],
            ["Quantidade", str(qty)],
            ["Total investido", f"R$ {invested:,.2f}"],
            ["Valor atual da posição", f"R$ {curr_val:,.2f}"],
            ["Resultado", f"R$ {pnl_abs:+,.2f} ({pnl_pct:+.2f}%)"],
        ]
        if position.stop_price:
            b6.append(["Stop definido", f"R$ {position.stop_price:,.2f}"])
        if position.notes:
            b6.append(["Anotações", str(position.notes)])
        story.append(_table(b6, [W * 0.45, W * 0.55]))
        story.append(Spacer(1, 0.3 * cm))

    # --- Gráfico (opcional) ---
    if chart_image_bytes:
        story.append(Paragraph("Gráfico de candles", section_s))
        story.append(Image(BytesIO(chart_image_bytes), width=W, height=W * 0.55))
        story.append(Spacer(1, 0.3 * cm))

    # --- Rodapé ---
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "Análise técnica para uso pessoal. Não é recomendação de investimento. "
        "Verifique liquidez, notícias, custos e seu perfil de risco antes de operar.",
        caption_s,
    ))

    doc.build(story)
    return buf.getvalue()
