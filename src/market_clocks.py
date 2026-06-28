"""Relógios de mercado B3 / Nova York e verificação de status do pregão."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import streamlit as st

_TZ_BR = timezone(timedelta(hours=-3))   # Brasília UTC-3 (sem horário de verão desde 2019)


def is_b3_open() -> tuple[bool, str]:
    """Verifica se a B3 está aberta agora. Retorna (is_open, label)."""
    now = datetime.now(tz=_TZ_BR)
    wd = now.weekday()
    tm = now.hour * 60 + now.minute
    if wd >= 5:
        return False, "Mercado fechado — fim de semana"
    if 570 <= tm < 600:
        return False, "B3: pré-abertura (09:30–10:00)"
    if 600 <= tm < 1015:
        return True, "B3: pregão regular aberto (10:00–16:55)"
    if 1050 <= tm < 1125:
        return False, "B3: after-market (17:30–18:45)"
    return False, "B3: mercado fechado"


def render_market_clocks() -> None:
    """Barra compacta com horário Brasília / Nova York e status da B3 (renderização server-side)."""
    now_br = datetime.now(tz=_TZ_BR)

    # Nova York: EDT (UTC-4) de mar a nov, EST (UTC-5) restante
    ny_off = -4 if 3 <= now_br.month <= 11 else -5
    now_ny = datetime.now(tz=timezone(timedelta(hours=ny_off)))

    # Status B3
    wd = now_br.weekday()
    tm = now_br.hour * 60 + now_br.minute
    if wd >= 5:
        b3_style = "background:#7f1d1d;color:#fca5a5"
        b3_txt = "Fechado"
    elif 570 <= tm < 600:
        b3_style = "background:#78350f;color:#fde68a"
        b3_txt = "Pré-abertura"
    elif 600 <= tm < 1015:
        b3_style = "background:#14532d;color:#86efac"
        b3_txt = "Aberto ✅"
    elif 1050 <= tm < 1125:
        b3_style = "background:#78350f;color:#fde68a"
        b3_txt = "After-market"
    else:
        b3_style = "background:#7f1d1d;color:#fca5a5"
        b3_txt = "Fechado"

    # Status NYSE
    tm_ny = now_ny.hour * 60 + now_ny.minute
    wd_ny = now_ny.weekday()
    if wd_ny >= 5:
        ny_txt = "Fechado"
    elif 570 <= tm_ny < 960:
        ny_txt = "Aberto"
    elif 960 <= tm_ny < 1200:
        ny_txt = "After-hours"
    else:
        ny_txt = "Fechado"

    br_time = now_br.strftime("%H:%M:%S")
    ny_time = now_ny.strftime("%H:%M:%S")

    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:16px;padding:5px 14px;
        background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);
        border-radius:8px;font-family:Inter,sans-serif;font-size:13px;color:#ccc;
        width:fit-content;margin:2px 0 10px 0;">
            <span style="display:flex;align-items:center;gap:6px;">
                <span style="opacity:.55;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">🇧🇷 Brasília</span>
                <b style="font-size:14px;color:#fff;font-variant-numeric:tabular-nums;">{br_time}</b>
                <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;{b3_style};">B3 {b3_txt}</span>
            </span>
            <span style="opacity:.2;">|</span>
            <span style="display:flex;align-items:center;gap:6px;">
                <span style="opacity:.55;font-size:11px;text-transform:uppercase;letter-spacing:.05em;">🇺🇸 Nova York</span>
                <b style="font-size:14px;color:#fff;font-variant-numeric:tabular-nums;">{ny_time}</b>
                <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:20px;background:#1e3a5f;color:#93c5fd;">NYSE {ny_txt}</span>
            </span>
        </div>""",
        unsafe_allow_html=True,
    )
