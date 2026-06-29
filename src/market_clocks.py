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
    """Widget compacto de relógios para sidebar."""
    now_br = datetime.now(tz=_TZ_BR)

    ny_off = -4 if 3 <= now_br.month <= 11 else -5
    now_ny = datetime.now(tz=timezone(timedelta(hours=ny_off)))

    # Status B3
    wd = now_br.weekday()
    tm = now_br.hour * 60 + now_br.minute
    if wd >= 5:
        b3_bg, b3_fg, b3_dot, b3_lbl = "#3b0000", "#ff8080", "#ef4444", "Fechado"
    elif 570 <= tm < 600:
        b3_bg, b3_fg, b3_dot, b3_lbl = "#2d1f00", "#fbbf24", "#f59e0b", "Pré-abertura"
    elif 600 <= tm < 1015:
        b3_bg, b3_fg, b3_dot, b3_lbl = "#003b1a", "#6ee7b7", "#10b981", "Aberto"
    elif 1050 <= tm < 1125:
        b3_bg, b3_fg, b3_dot, b3_lbl = "#2d1f00", "#fbbf24", "#f59e0b", "After-market"
    else:
        b3_bg, b3_fg, b3_dot, b3_lbl = "#3b0000", "#ff8080", "#ef4444", "Fechado"

    # Status NYSE
    tm_ny = now_ny.hour * 60 + now_ny.minute
    wd_ny = now_ny.weekday()
    if wd_ny >= 5:
        ny_bg, ny_fg, ny_dot, ny_lbl = "#3b0000", "#ff8080", "#ef4444", "Fechado"
    elif 570 <= tm_ny < 960:
        ny_bg, ny_fg, ny_dot, ny_lbl = "#003b1a", "#6ee7b7", "#10b981", "Aberto"
    elif 960 <= tm_ny < 1200:
        ny_bg, ny_fg, ny_dot, ny_lbl = "#2d1f00", "#fbbf24", "#f59e0b", "After-hours"
    else:
        ny_bg, ny_fg, ny_dot, ny_lbl = "#3b0000", "#ff8080", "#ef4444", "Fechado"

    br_time = now_br.strftime("%H:%M")
    ny_time = now_ny.strftime("%H:%M")

    st.markdown(f"""
<div style="background:#1a1a2e;border:1px solid #2a2a3e;border-radius:8px;
            padding:8px 10px;margin:4px 0 10px;font-family:monospace;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;">
    <span style="font-size:11px;color:#888;">🇧🇷 Brasília</span>
    <span style="font-size:14px;font-weight:700;color:#e2e8f0;letter-spacing:1px;">{br_time}</span>
    <span style="font-size:10px;font-weight:600;padding:2px 7px;border-radius:20px;
                 background:{b3_bg};color:{b3_fg};">
      <span style="color:{b3_dot};">●</span> B3 {b3_lbl}
    </span>
  </div>
  <div style="display:flex;align-items:center;justify-content:space-between;">
    <span style="font-size:11px;color:#888;">🇺🇸 Nova York</span>
    <span style="font-size:14px;font-weight:700;color:#e2e8f0;letter-spacing:1px;">{ny_time}</span>
    <span style="font-size:10px;font-weight:600;padding:2px 7px;border-radius:20px;
                 background:{ny_bg};color:{ny_fg};">
      <span style="color:{ny_dot};">●</span> NYSE {ny_lbl}
    </span>
  </div>
</div>""", unsafe_allow_html=True)
