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
    """Duas linhas de caption na sidebar: horário BR e NY com status de mercado."""
    now_br = datetime.now(tz=_TZ_BR)

    ny_off = -4 if 3 <= now_br.month <= 11 else -5
    now_ny = datetime.now(tz=timezone(timedelta(hours=ny_off)))

    wd = now_br.weekday()
    tm = now_br.hour * 60 + now_br.minute
    if wd >= 5:
        b3_txt = "🔴 fechado"
    elif 570 <= tm < 600:
        b3_txt = "🟡 pré-abertura"
    elif 600 <= tm < 1015:
        b3_txt = "🟢 aberto"
    elif 1050 <= tm < 1125:
        b3_txt = "🟡 after-market"
    else:
        b3_txt = "🔴 fechado"

    tm_ny = now_ny.hour * 60 + now_ny.minute
    wd_ny = now_ny.weekday()
    if wd_ny >= 5:
        ny_txt = "🔴 fechado"
    elif 570 <= tm_ny < 960:
        ny_txt = "🟢 aberto"
    elif 960 <= tm_ny < 1200:
        ny_txt = "🟡 after-hours"
    else:
        ny_txt = "🔴 fechado"

    br_time = now_br.strftime("%H:%M")
    ny_time = now_ny.strftime("%H:%M")

    st.caption(f"🇧🇷 Brasília {br_time} · B3 {b3_txt}")
    st.caption(f"🇺🇸 Nova York {ny_time} · NYSE {ny_txt}")
