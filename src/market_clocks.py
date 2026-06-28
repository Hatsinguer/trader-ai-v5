"""Relógios de mercado B3 / Nova York e verificação de status do pregão."""
from __future__ import annotations

from datetime import datetime

import streamlit as st
import streamlit.components.v1 as components

try:
    from zoneinfo import ZoneInfo
    _TZ_BR = ZoneInfo("America/Sao_Paulo")
except ImportError:
    from dateutil import tz as _dtz
    _TZ_BR = _dtz.gettz("America/Sao_Paulo")  # type: ignore[assignment]


def is_b3_open() -> tuple[bool, str]:
    """Verifica se a B3 está aberta agora. Retorna (is_open, label)."""
    now = datetime.now(tz=_TZ_BR)
    wd = now.weekday()  # 0=seg … 6=dom
    tm = now.hour * 60 + now.minute

    if wd >= 5:
        return False, "Mercado fechado — fim de semana"
    if 570 <= tm < 600:
        return False, "B3: pré-abertura / cancelamento (09:30–10:00)"
    if 600 <= tm < 1015:
        return True, "B3: pregão regular aberto (10:00–16:55)"
    if 1050 <= tm < 1125:
        return False, "B3: after-market (17:30–18:45)"
    return False, "B3: mercado fechado"


def render_market_clocks() -> None:
    """Renderiza dois relógios digitais (Brasília e Nova York) com indicador de status da B3."""
    components.html(
        """
        <div class="clock-wrapper">
            <div id="br-clock-card" class="clock-card main-clock closed">
                <div class="clock-label">Horário de Brasília</div>
                <div id="br-time" class="clock-time">--:--:--</div>
                <div id="b3-status" class="market-status">Carregando...</div>
            </div>

            <div class="clock-card ny-clock">
                <div class="clock-label">Nova York (ET)</div>
                <div id="ny-time" class="clock-time small">--:--:--</div>
                <div id="ny-status" class="market-status secondary">Carregando...</div>
            </div>
        </div>

        <style>
            .clock-wrapper {
                display: flex;
                gap: 16px;
                align-items: stretch;
                margin: 12px 0 22px 0;
                font-family: "Inter", "Segoe UI", Arial, sans-serif;
            }
            .clock-card {
                border-radius: 18px;
                padding: 18px 22px;
                color: #ffffff;
                box-shadow: 0 8px 24px rgba(0,0,0,0.18);
                transition: background 0.35s ease, box-shadow 0.35s ease;
            }
            .main-clock { flex: 1.5; min-height: 145px; }
            .ny-clock   { flex: 0.8; min-height: 145px; background: linear-gradient(135deg,#111827,#374151); }
            .clock-label {
                font-size: 0.95rem; font-weight: 700; opacity: 0.9;
                margin-bottom: 8px; letter-spacing: 0.04em; text-transform: uppercase;
            }
            .clock-time {
                font-size: 3.4rem; line-height: 1; font-weight: 900;
                font-variant-numeric: tabular-nums; letter-spacing: 0.04em;
            }
            .clock-time.small { font-size: 2.1rem; }
            .market-status        { margin-top: 12px; font-size: 1.05rem; font-weight: 700; }
            .market-status.secondary { font-size: 0.9rem; opacity: 0.75; }
            .preopen, .after { background: linear-gradient(135deg,#f59e0b,#d97706); box-shadow: 0 8px 28px rgba(245,158,11,0.35); }
            .regular { background: linear-gradient(135deg,#39ff14,#16a34a); color:#052e16; box-shadow: 0 8px 28px rgba(57,255,20,0.35); }
            .closed  { background: linear-gradient(135deg,#dc2626,#7f1d1d); box-shadow: 0 8px 28px rgba(220,38,38,0.35); }
            @media(max-width:760px){
                .clock-wrapper{flex-direction:column;}
                .clock-time{font-size:2.6rem;}
                .clock-time.small{font-size:1.9rem;}
            }
        </style>

        <script>
            function getZonedParts(timeZone) {
                const now = new Date();
                const parts = new Intl.DateTimeFormat("en-US", {
                    timeZone, weekday:"short",
                    hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false
                }).formatToParts(now);
                const obj = {};
                parts.forEach(p => { if(p.type !== "literal") obj[p.type] = p.value; });
                return obj;
            }

            function formatTime(timeZone) {
                return new Intl.DateTimeFormat("pt-BR", {
                    timeZone, hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:false
                }).format(new Date());
            }

            function getB3Status() {
                const br = getZonedParts("America/Sao_Paulo");
                let hour = Number(br.hour); if(hour===24) hour=0;
                const tm = hour*60 + Number(br.minute);
                const wd = br.weekday;
                if(wd==="Sat"||wd==="Sun") return {cls:"closed", label:"Mercado fechado — fim de semana"};
                if(tm>=570&&tm<600)  return {cls:"preopen", label:"B3: pré-abertura / cancelamento"};
                if(tm>=600&&tm<1015) return {cls:"regular", label:"B3: pregão regular aberto ✅"};
                if(tm>=1050&&tm<1125) return {cls:"after",  label:"B3: after-market"};
                return {cls:"closed", label:"B3: mercado fechado"};
            }

            function getNYStatus() {
                const ny = getZonedParts("America/New_York");
                let hour = Number(ny.hour); if(hour===24) hour=0;
                const tm = hour*60 + Number(ny.minute);
                const wd = ny.weekday;
                if(wd==="Sat"||wd==="Sun") return "NYSE: fechado — fim de semana";
                if(tm>=570&&tm<600)  return "NYSE: pré-mercado (09:30)";
                if(tm>=570&&tm<960)  return "NYSE: pregão regular aberto";
                if(tm>=960&&tm<1200) return "NYSE: after-hours";
                return "NYSE: fechado";
            }

            function updateClocks() {
                const brTimeEl  = document.getElementById("br-time");
                const nyTimeEl  = document.getElementById("ny-time");
                const statusEl  = document.getElementById("b3-status");
                const nyStatEl  = document.getElementById("ny-status");
                const brCard    = document.getElementById("br-clock-card");
                if(!brTimeEl||!nyTimeEl||!statusEl||!brCard) return;

                brTimeEl.textContent = formatTime("America/Sao_Paulo");
                nyTimeEl.textContent = formatTime("America/New_York");

                const b3 = getB3Status();
                statusEl.textContent = b3.label;
                brCard.classList.remove("preopen","regular","after","closed");
                brCard.classList.add(b3.cls);

                if(nyStatEl) nyStatEl.textContent = getNYStatus();
            }

            updateClocks();
            setInterval(updateClocks, 1000);
        </script>
        """,
        height=230,
    )
