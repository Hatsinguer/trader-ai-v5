"""Relógios de mercado B3 / Nova York e verificação de status do pregão."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import streamlit as st
import streamlit.components.v1 as components

_TZ_BR = timezone(timedelta(hours=-3))  # Brasília UTC-3 (Brasil não tem horário de verão desde 2019)


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
    """Barra minimalista com relógios Brasília / Nova York e status da B3."""
    components.html(
        """
        <div id="mkt-bar">
            <span class="seg">
                <span class="lbl">🇧🇷 Brasília</span>
                <span id="br-time" class="clk">--:--:--</span>
                <span id="b3-pill" class="pill closed">B3 fechado</span>
            </span>
            <span class="divider">|</span>
            <span class="seg">
                <span class="lbl">🇺🇸 Nova York</span>
                <span id="ny-time" class="clk">--:--:--</span>
                <span id="ny-pill" class="pill ny">NYSE</span>
            </span>
        </div>

        <style>
            #mkt-bar {
                display: flex; align-items: center; gap: 14px;
                padding: 6px 14px; margin: 4px 0 10px 0;
                background: rgba(255,255,255,0.04);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 10px;
                font-family: "Inter","Segoe UI",Arial,sans-serif;
                font-size: 13px; color: #ccc;
                width: fit-content;
            }
            .seg  { display: flex; align-items: center; gap: 7px; }
            .lbl  { opacity: .6; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
            .clk  { font-variant-numeric: tabular-nums; font-weight: 700; color: #fff; font-size: 14px; }
            .divider { opacity: .25; }
            .pill {
                font-size: 10px; font-weight: 700; padding: 2px 7px;
                border-radius: 20px; letter-spacing: .04em; text-transform: uppercase;
            }
            .pill.closed  { background:#7f1d1d; color:#fca5a5; }
            .pill.preopen { background:#78350f; color:#fde68a; }
            .pill.regular { background:#14532d; color:#86efac; }
            .pill.after   { background:#78350f; color:#fde68a; }
            .pill.ny      { background:#1e3a5f; color:#93c5fd; }
        </style>

        <script>
            function getParts(tz) {
                const p = new Intl.DateTimeFormat("en-US",{timeZone:tz,weekday:"short",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).formatToParts(new Date());
                const o={};p.forEach(x=>{if(x.type!=="literal")o[x.type]=x.value;});return o;
            }
            function fmt(tz){
                return new Intl.DateTimeFormat("pt-BR",{timeZone:tz,hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date());
            }
            function b3Status(){
                const p=getParts("America/Sao_Paulo");
                let h=Number(p.hour);if(h===24)h=0;
                const t=h*60+Number(p.minute),w=p.weekday;
                if(w==="Sat"||w==="Sun") return{cls:"closed",txt:"Fechado"};
                if(t>=570&&t<600)  return{cls:"preopen",txt:"Pré-abertura"};
                if(t>=600&&t<1015) return{cls:"regular",txt:"B3 Aberto ✅"};
                if(t>=1050&&t<1125)return{cls:"after",  txt:"After-market"};
                return{cls:"closed",txt:"B3 Fechado"};
            }
            function nyStatus(){
                const p=getParts("America/New_York");
                let h=Number(p.hour);if(h===24)h=0;
                const t=h*60+Number(p.minute),w=p.weekday;
                if(w==="Sat"||w==="Sun") return"Fechado";
                if(t>=570&&t<960)  return"NYSE Aberto";
                if(t>=960&&t<1200) return"After-hours";
                return"NYSE Fechado";
            }
            function tick(){
                const br=document.getElementById("br-time");
                const ny=document.getElementById("ny-time");
                const bp=document.getElementById("b3-pill");
                const np=document.getElementById("ny-pill");
                if(!br)return;
                br.textContent=fmt("America/Sao_Paulo");
                ny.textContent=fmt("America/New_York");
                const s=b3Status();
                bp.textContent=s.txt;
                bp.className="pill "+s.cls;
                np.textContent=nyStatus();
            }
            tick();setInterval(tick,1000);
        </script>
        """,
        height=52,
    )
