#!/usr/bin/env python3
"""
Captura de Preços via DOM — Trader AI v5
=========================================
Conecta ao Chrome aberto (via DevTools Protocol) e lê os preços
diretamente do AG Grid do Home Broker da XP Invest.

Instalação:
    pip install playwright
    (NÃO precisa instalar Chromium extra — conecta no Chrome que já está aberto)

Uso:
    1. Abrir Chrome com: start_chrome_debug.bat
    2. Logar no Home Broker normalmente
    3. Deixar a página de posições/watchlist aberta
    4. python captura_precos.py
"""
from __future__ import annotations

import json
import sys
import time
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── dependência ─────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, Error as PWError
except ImportError:
    print("❌  Playwright não instalado. Execute:\n\n    pip install playwright\n")
    sys.exit(1)

# ── caminhos ────────────────────────────────────────────────────────────────
_ROOT       = Path(__file__).resolve().parent
DATA_DIR    = _ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
PRECOS_FILE = DATA_DIR / "precos_capturados.json"

_TZ_BR = timezone(timedelta(hours=-3))

# URL parcial do Home Broker XP (ajuste se necessário)
_HB_URL_KEYWORDS = ["xpinvestimentos", "homebroker", "xpi.com", "xp.com", "clear.com"]

# Porta de debug do Chrome (deve coincidir com start_chrome_debug.bat)
_CDP_PORT = 9222


# ════════════════════════════════════════════════════════════════════════════
# EXTRAÇÃO DO DOM
# ════════════════════════════════════════════════════════════════════════════

def _encontrar_pagina_hb(contexto):
    """Retorna a aba do Home Broker dentre as abertas no Chrome."""
    for page in contexto.pages:
        url = page.url.lower()
        if any(kw in url for kw in _HB_URL_KEYWORDS):
            return page
    return None


def _preco_float(texto: str) -> float | None:
    """Converte '19,02' ou '1.234,56' para float."""
    t = texto.strip().replace(".", "").replace(",", ".")
    try:
        v = float(t)
        return v if 0.001 < v < 1_000_000 else None
    except ValueError:
        return None


def extrair_precos(page) -> dict[str, dict]:
    """
    Lê todas as linhas do AG Grid e retorna:
      { "PETR4": { "preco": 38.45, "variacao": 1.23, "minima": 37.8, "maxima": 39.1 } }

    Estrutura DOM esperada (XP Invest Home Broker):
      div[role="row"][row-id="PETR4"]
        div[col-id="lastPrice"]  → preço atual
        div[col-id="netChange"]  → variação %
        div[col-id="low"]        → mínima
        div[col-id="high"]       → máxima
    """
    resultado: dict[str, dict] = {}

    try:
        # Aguarda o grid estar presente
        page.wait_for_selector('div[role="row"][row-id]', timeout=8_000)
    except Exception:
        print("⚠  Grid de preços não encontrado na página. Verifique se a aba está aberta.")
        return resultado

    rows = page.query_selector_all('div[role="row"][row-id]')

    for row in rows:
        ticker = row.get_attribute("row-id")
        if not ticker or not re.match(r"^[A-Z]{3,6}\d{1,2}[F]?$", ticker.upper()):
            continue
        ticker = ticker.upper()

        def _cell(col_id: str) -> str:
            el = row.query_selector(f'div[col-id="{col_id}"]')
            return el.inner_text().strip() if el else ""

        preco_txt  = _cell("lastPrice")
        var_txt    = _cell("netChange")
        minima_txt = _cell("low")
        maxima_txt = _cell("high")

        preco = _preco_float(preco_txt)
        if preco is None:
            continue

        # Remove símbolos de variação (ex: "▲ 0.16%" → "0.16")
        var_limpo = re.sub(r"[^0-9,.\-]", "", var_txt)
        variacao  = _preco_float(var_limpo)

        resultado[ticker] = {
            "preco":      preco,
            "variacao":   variacao,
            "minima":     _preco_float(minima_txt),
            "maxima":     _preco_float(maxima_txt),
            "capturado_em": datetime.now(tz=_TZ_BR).strftime("%H:%M:%S"),
            "fonte":      "DOM (Home Broker)",
        }

    return resultado


# ════════════════════════════════════════════════════════════════════════════
# PERSISTÊNCIA
# ════════════════════════════════════════════════════════════════════════════

def salvar(precos: dict[str, dict]) -> None:
    PRECOS_FILE.write_text(
        json.dumps(precos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _imprimir(precos: dict[str, dict]) -> None:
    if not precos:
        print("   (nenhum preço extraído)")
        return
    print(f"   {'Ticker':<10} {'Preço':>9}  {'Var%':>7}  {'Mín':>9}  {'Máx':>9}")
    print("   " + "-" * 52)
    for tk, d in sorted(precos.items()):
        var = f"{d['variacao']:+.2f}%" if d.get("variacao") is not None else "  —  "
        mn  = f"{d['minima']:.2f}"  if d.get("minima")   else "  —  "
        mx  = f"{d['maxima']:.2f}"  if d.get("maxima")   else "  —  "
        print(f"   {tk:<10} {d['preco']:>9.2f}  {var:>7}  {mn:>9}  {mx:>9}")


# ════════════════════════════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════

def _intervalo() -> int:
    print("\nIntervalo de atualização:")
    print("  [1]  5 segundos")
    print("  [2] 10 segundos  (padrão)")
    print("  [3] 30 segundos")
    print("  [4] 60 segundos")
    op = input("Escolha (Enter = 10s): ").strip()
    return {1: 5, 2: 10, 3: 30, 4: 60}.get(int(op) if op.isdigit() else 0, 10)


def main():
    print("=" * 60)
    print("  Captura de Preços via DOM — Trader AI v5")
    print("=" * 60)
    print(f"\n🔌 Conectando ao Chrome na porta {_CDP_PORT}...")
    print("   (Chrome deve estar aberto com start_chrome_debug.bat)\n")

    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(f"http://localhost:{_CDP_PORT}")
    except Exception as e:
        print(f"❌  Não foi possível conectar ao Chrome:\n   {e}")
        print("\n➡  Execute start_chrome_debug.bat ANTES de rodar este script.\n")
        input("Pressione Enter para sair...")
        sys.exit(1)

    contexto = browser.contexts[0]
    page = _encontrar_pagina_hb(contexto)

    if page is None:
        print("⚠  Nenhuma aba do Home Broker encontrada.")
        print("   Abas abertas no Chrome:")
        for p in contexto.pages:
            print(f"   • {p.url}")
        print("\n   Abra o Home Broker da XP e tente novamente.")
        input("Pressione Enter para sair...")
        sys.exit(1)

    print(f"✅ Home Broker encontrado: {page.url[:70]}")
    intervalo = _intervalo()

    print(f"\n▶  Capturando a cada {intervalo}s — Ctrl+C para parar\n")

    try:
        while True:
            hora = datetime.now(tz=_TZ_BR).strftime("%H:%M:%S")
            print(f"\n[{hora}] Capturando...")

            try:
                precos = extrair_precos(page)
                salvar(precos)
                _imprimir(precos)
                print(f"   💾 {len(precos)} ativo(s) salvos em data/precos_capturados.json")
            except PWError as e:
                print(f"   ⚠  Erro de página: {e}")
                # Tenta refrescar referência à página
                page = _encontrar_pagina_hb(contexto) or page

            time.sleep(intervalo)

    except KeyboardInterrupt:
        print("\n\n⏹  Captura encerrada pelo usuário.")
    finally:
        try:
            browser.close()
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
