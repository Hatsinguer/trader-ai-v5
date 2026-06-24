from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime

from .storage import DATA_DIR, ensure_data_dir

ALERTS_FILE = DATA_DIR / "alerts.json"

# Tipos de alerta suportados.
ALERT_TYPES = ["preco_abaixo", "preco_acima", "rsi_abaixo", "rsi_acima", "call_mudou"]

ALERT_LABELS = {
    "preco_abaixo": "Preço abaixo de",
    "preco_acima": "Preço acima de",
    "rsi_abaixo": "RSI abaixo de",
    "rsi_acima": "RSI acima de",
    "call_mudou": "Call mudou (em relação à referência)",
}


@dataclass
class Alert:
    ticker: str
    tipo: str          # ver ALERT_TYPES
    valor: float       # preço ou nível de RSI (ignorado em call_mudou)
    ativo: bool = True
    criado_em: str = ""
    disparado_em: str | None = None
    mensagem: str = ""
    referencia: str | None = None  # baseline do call no momento da criação (call_mudou)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_alerts() -> list[Alert]:
    ensure_data_dir()
    if not ALERTS_FILE.exists():
        return []
    try:
        data = json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    valid_keys = set(Alert.__annotations__.keys())
    alerts: list[Alert] = []
    for d in data:
        if isinstance(d, dict) and "ticker" in d and "tipo" in d:
            alerts.append(Alert(**{k: v for k, v in d.items() if k in valid_keys}))
    return alerts


def save_alerts(alerts: list[Alert]) -> None:
    ensure_data_dir()
    ALERTS_FILE.write_text(
        json.dumps([asdict(a) for a in alerts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_alert(ticker: str, tipo: str, valor: float, mensagem: str = "", referencia: str | None = None) -> Alert:
    alerts = load_alerts()
    alert = Alert(
        ticker=str(ticker).strip().upper(),
        tipo=tipo,
        valor=float(valor),
        ativo=True,
        criado_em=_now(),
        disparado_em=None,
        mensagem=mensagem,
        referencia=referencia,
    )
    alerts.append(alert)
    save_alerts(alerts)
    return alert


def remove_alert(index: int) -> None:
    alerts = load_alerts()
    if 0 <= index < len(alerts):
        alerts.pop(index)
        save_alerts(alerts)


def check_alerts(alerts: list[Alert], current_data: dict) -> list[Alert]:
    """Verifica alertas ativos contra os dados atuais e marca os disparados.

    `current_data` mapeia ticker -> {"price": float, "rsi": float, "call": str}.
    Muta os alertas disparados (ativo=False, disparado_em). Retorna os disparados.
    """
    triggered: list[Alert] = []
    for a in alerts:
        if not a.ativo:
            continue
        data = current_data.get(a.ticker)
        if not data:
            continue
        price = data.get("price")
        rsi = data.get("rsi")
        call = data.get("call")

        hit = False
        if a.tipo == "preco_abaixo" and price is not None and price <= a.valor:
            hit = True
        elif a.tipo == "preco_acima" and price is not None and price >= a.valor:
            hit = True
        elif a.tipo == "rsi_abaixo" and rsi is not None and rsi <= a.valor:
            hit = True
        elif a.tipo == "rsi_acima" and rsi is not None and rsi >= a.valor:
            hit = True
        elif a.tipo == "call_mudou" and call is not None and a.referencia and call != a.referencia:
            hit = True

        if hit:
            a.ativo = False
            a.disparado_em = _now()
            triggered.append(a)

    return triggered
