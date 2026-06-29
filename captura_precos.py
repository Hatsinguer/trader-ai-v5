#!/usr/bin/env python3
"""
Captura de Preços — Trader AI v5
=====================================
Lê preços do Home Broker via OCR e salva em data/precos_capturados.json
para uso automático na Mesa Day Trade.

Instalação:
    pip install mss pillow pytesseract easyocr
    # Instale também o Tesseract OCR:
    # https://github.com/UB-Mannheim/tesseract/wiki  (Windows installer)

Uso:
    python captura_precos.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont
from pathlib import Path
from datetime import datetime, timezone, timedelta
from threading import Thread

# ── dependências opcionais ──────────────────────────────────────────────────
try:
    import mss
    import mss.tools
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import pytesseract
    # Caminho padrão do Tesseract no Windows
    _tess_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for _p in _tess_paths:
        if os.path.exists(_p):
            pytesseract.pytesseract.tesseract_cmd = _p
            break
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    import easyocr
    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False

# ── caminhos ────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
DATA_DIR = _ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_FILE  = DATA_DIR / "captura_config.json"
PRECOS_FILE  = DATA_DIR / "precos_capturados.json"
MESA_CONFIG  = DATA_DIR / "mesa_config.json"

_TZ_BR = timezone(timedelta(hours=-3))


# ════════════════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ════════════════════════════════════════════════════════════════════════════

def _now_br() -> str:
    return datetime.now(tz=_TZ_BR).strftime("%H:%M:%S")


def _load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _tickers_da_mesa() -> list[str]:
    cfg = _load_json(MESA_CONFIG, {})
    return [t.replace(".SA", "") for t in cfg.get("ativos", [])]


# ════════════════════════════════════════════════════════════════════════════
# OCR
# ════════════════════════════════════════════════════════════════════════════

def _preprocessar_imagem(img: "Image.Image") -> "Image.Image":
    """Aumenta contraste e converte para escala de cinza para melhorar OCR."""
    img = img.convert("L")                          # escala de cinza
    img = ImageEnhance.Contrast(img).enhance(2.5)   # contraste +
    img = ImageEnhance.Sharpness(img).enhance(2.0)  # nitidez +
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)  # amplia
    return img


def _extrair_texto_tesseract(img: "Image.Image") -> str:
    img_proc = _preprocessar_imagem(img)
    config = "--psm 6 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,. "
    return pytesseract.image_to_string(img_proc, lang="por", config=config)


def _extrair_texto_easyocr(img: "Image.Image", reader) -> str:
    import numpy as np
    arr = np.array(_preprocessar_imagem(img))
    resultados = reader.readtext(arr, detail=0, paragraph=False)
    return "\n".join(resultados)


def _parse_precos(texto: str, tickers_esperados: list[str]) -> dict[str, float]:
    """
    Tenta extrair pares ticker→preço do texto OCR.
    Estratégias:
    1. Busca tickers conhecidos e o número mais próximo na mesma linha
    2. Busca padrões "XXXX4 38,45" ou "XXXX4 R$ 38,45"
    """
    precos: dict[str, float] = {}
    linhas = texto.upper().splitlines()

    # Padrão de preço brasileiro: 1234,56 ou 1.234,56 ou 1234.56
    _preco_re = re.compile(r"\b(\d{1,6}[.,]\d{2})\b")
    # Padrão de ticker B3: 4 letras + 1-2 dígitos (ex: PETR4, BOVA11, MXRF11)
    _ticker_re = re.compile(r"\b([A-Z]{3,6}\d{1,2})\b")

    for linha in linhas:
        tickers_linha = _ticker_re.findall(linha)
        precos_linha  = _preco_re.findall(linha)
        if not tickers_linha or not precos_linha:
            continue
        for tk in tickers_linha:
            # Normaliza o ticker
            tk_norm = tk[:4] + tk[4:].lstrip("0") if len(tk) > 4 else tk
            # Verifica se é um ticker esperado ou parece válido
            candidato = tk_norm in tickers_esperados or len(tk_norm) >= 5
            if candidato and precos_linha:
                preco_str = precos_linha[0].replace(".", "").replace(",", ".")
                try:
                    preco_val = float(preco_str)
                    if 0.01 < preco_val < 100_000:   # sanidade
                        precos[tk_norm] = preco_val
                except ValueError:
                    pass

    return precos


# ════════════════════════════════════════════════════════════════════════════
# CAPTURA DE TELA
# ════════════════════════════════════════════════════════════════════════════

class RegionSelector:
    """Overlay transparente para o usuário desenhar a região de captura."""

    def __init__(self):
        self.result: dict | None = None

    def selecionar(self) -> dict | None:
        root = tk.Tk()
        root.attributes("-fullscreen", True)
        root.attributes("-alpha", 0.3)
        root.configure(bg="black")
        root.attributes("-topmost", True)

        canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        lbl = tk.Label(
            root,
            text="Clique e arraste para selecionar a região com os preços no seu Home Broker.\n"
                 "Pressione ESC para cancelar.",
            bg="black", fg="white", font=("Arial", 16, "bold")
        )
        lbl.place(relx=0.5, rely=0.05, anchor="center")

        x0 = y0 = 0
        rect = None

        def on_press(e):
            nonlocal x0, y0, rect
            x0, y0 = e.x, e.y
            rect = canvas.create_rectangle(x0, y0, x0, y0, outline="#00ff88", width=2)

        def on_drag(e):
            canvas.coords(rect, x0, y0, e.x, e.y)

        def on_release(e):
            x1, y1 = e.x, e.y
            self.result = {
                "left":   min(x0, x1),
                "top":    min(y0, y1),
                "width":  abs(x1 - x0),
                "height": abs(y1 - y0),
            }
            root.destroy()

        def on_esc(e):
            root.destroy()

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        root.bind("<Escape>", on_esc)
        root.mainloop()
        return self.result


def capturar_regiao(regiao: dict) -> "Image.Image | None":
    if not HAS_MSS or not HAS_PIL:
        return None
    with mss.mss() as sct:
        shot = sct.grab(regiao)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


# ════════════════════════════════════════════════════════════════════════════
# INTERFACE PRINCIPAL
# ════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Captura de Preços — Trader AI v5")
        self.resizable(False, False)
        self.configure(bg="#0f0f1a")

        self._regiao: dict | None = None
        self._intervalo_var = tk.IntVar(value=10)
        self._rodando = False
        self._thread: Thread | None = None
        self._easyocr_reader = None
        self._precos_atuais: dict[str, float] = {}
        self._precos_manuais: dict[str, str] = {}

        cfg = _load_json(CONFIG_FILE, {})
        if "regiao" in cfg:
            self._regiao = cfg["regiao"]

        self._build_ui()
        self._atualizar_status_deps()
        self._atualizar_tabela_tickers()

    # ── layout ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = dict(padx=10, pady=5)

        # Título
        tk.Label(
            self, text="📡 Captura de Preços", bg="#0f0f1a", fg="#e2e8f0",
            font=("Arial", 14, "bold")
        ).pack(pady=(12, 2))
        tk.Label(
            self, text="Lê preços do Home Broker via OCR → Mesa Day Trade",
            bg="#0f0f1a", fg="#64748b", font=("Arial", 9)
        ).pack(pady=(0, 8))

        # ── Região ──────────────────────────────────────────────────────────
        frame_reg = tk.LabelFrame(
            self, text=" Região de captura ", bg="#0f0f1a", fg="#94a3b8",
            font=("Arial", 9), padx=8, pady=6
        )
        frame_reg.pack(fill="x", padx=12, pady=4)

        self._lbl_regiao = tk.Label(
            frame_reg,
            text=self._descrever_regiao(),
            bg="#0f0f1a", fg="#cbd5e1", font=("Courier", 9)
        )
        self._lbl_regiao.pack(side="left", fill="x", expand=True)

        tk.Button(
            frame_reg, text="📐 Selecionar região", command=self._selecionar_regiao,
            bg="#1e3a5f", fg="#93c5fd", relief="flat", cursor="hand2", padx=8
        ).pack(side="right")

        # ── Intervalo ────────────────────────────────────────────────────────
        frame_int = tk.LabelFrame(
            self, text=" Intervalo de captura ", bg="#0f0f1a", fg="#94a3b8",
            font=("Arial", 9), padx=8, pady=6
        )
        frame_int.pack(fill="x", padx=12, pady=4)

        for s, v in [("5s", 5), ("10s", 10), ("30s", 30), ("1min", 60)]:
            tk.Radiobutton(
                frame_int, text=s, variable=self._intervalo_var, value=v,
                bg="#0f0f1a", fg="#cbd5e1", selectcolor="#1e293b",
                activebackground="#0f0f1a", font=("Arial", 9)
            ).pack(side="left", padx=6)

        # ── Tabela de preços ─────────────────────────────────────────────────
        frame_tab = tk.LabelFrame(
            self, text=" Preços capturados ", bg="#0f0f1a", fg="#94a3b8",
            font=("Arial", 9), padx=8, pady=6
        )
        frame_tab.pack(fill="both", expand=True, padx=12, pady=4)

        cols = ("Ticker", "Preço OCR", "Preço Manual", "Última captura")
        self._tree = ttk.Treeview(frame_tab, columns=cols, show="headings", height=8)
        for c in cols:
            self._tree.heading(c, text=c)
            self._tree.column(c, width=110, anchor="center")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#1e293b", foreground="#e2e8f0",
                        fieldbackground="#1e293b", rowheight=22)
        style.configure("Treeview.Heading", background="#0f0f1a", foreground="#94a3b8")

        vsb = ttk.Scrollbar(frame_tab, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self._tree.bind("<Double-1>", self._editar_manual)

        # ── Botões ───────────────────────────────────────────────────────────
        frame_btn = tk.Frame(self, bg="#0f0f1a")
        frame_btn.pack(fill="x", padx=12, pady=6)

        self._btn_captura = tk.Button(
            frame_btn, text="📸 Capturar agora", command=self._capturar_uma_vez,
            bg="#1e3a5f", fg="#93c5fd", relief="flat", cursor="hand2", padx=10, pady=4
        )
        self._btn_captura.pack(side="left", padx=4)

        self._btn_auto = tk.Button(
            frame_btn, text="▶ Iniciar automático", command=self._toggle_auto,
            bg="#14532d", fg="#86efac", relief="flat", cursor="hand2", padx=10, pady=4
        )
        self._btn_auto.pack(side="left", padx=4)

        tk.Button(
            frame_btn, text="💾 Salvar manual", command=self._salvar_manuais,
            bg="#3b1f00", fg="#fbbf24", relief="flat", cursor="hand2", padx=10, pady=4
        ).pack(side="left", padx=4)

        # ── Status bar ────────────────────────────────────────────────────────
        self._lbl_status = tk.Label(
            self, text="Pronto.", bg="#0f0f1a", fg="#64748b", font=("Arial", 8),
            anchor="w"
        )
        self._lbl_status.pack(fill="x", padx=12, pady=(2, 8))

        # ── Deps ─────────────────────────────────────────────────────────────
        self._lbl_deps = tk.Label(
            self, text="", bg="#0f0f1a", fg="#ef4444", font=("Arial", 8),
            wraplength=400, justify="left"
        )
        self._lbl_deps.pack(fill="x", padx=12, pady=(0, 8))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _descrever_regiao(self) -> str:
        if not self._regiao:
            return "Nenhuma região selecionada"
        r = self._regiao
        return f"x={r['left']}  y={r['top']}  {r['width']}×{r['height']} px"

    def _atualizar_status_deps(self):
        erros = []
        if not HAS_MSS:
            erros.append("• mss não instalado  →  pip install mss")
        if not HAS_PIL:
            erros.append("• Pillow não instalado  →  pip install pillow")
        if not HAS_TESSERACT and not HAS_EASYOCR:
            erros.append(
                "• OCR não disponível.\n"
                "  Opção A: pip install pytesseract  +  instalar Tesseract em\n"
                "           https://github.com/UB-Mannheim/tesseract/wiki\n"
                "  Opção B: pip install easyocr  (mais fácil, ~1 GB download)"
            )
        if erros:
            self._lbl_deps.config(text="⚠ Dependências faltando:\n" + "\n".join(erros))
        else:
            self._lbl_deps.config(text="✅ Todas dependências OK", fg="#86efac")

    def _atualizar_tabela_tickers(self):
        self._tree.delete(*self._tree.get_children())
        tickers = _tickers_da_mesa()
        if not tickers:
            tickers = ["(Mesa vazia — adicione ativos no app)"]
        precos_salvos = _load_json(PRECOS_FILE, {})
        for tk_name in tickers:
            preco_ocr = precos_salvos.get(tk_name, {}).get("preco", "—")
            preco_man = self._precos_manuais.get(tk_name, "")
            hora      = precos_salvos.get(tk_name, {}).get("capturado_em", "—")
            preco_str = f"R$ {preco_ocr:.2f}" if isinstance(preco_ocr, (int, float)) else "—"
            self._tree.insert("", "end", values=(tk_name, preco_str, preco_man, hora))

    # ── ações ────────────────────────────────────────────────────────────────

    def _selecionar_regiao(self):
        self.withdraw()
        time.sleep(0.3)
        sel = RegionSelector()
        regiao = sel.selecionar()
        self.deiconify()
        if regiao and regiao["width"] > 10 and regiao["height"] > 10:
            self._regiao = regiao
            self._lbl_regiao.config(text=self._descrever_regiao())
            cfg = _load_json(CONFIG_FILE, {})
            cfg["regiao"] = regiao
            _save_json(CONFIG_FILE, cfg)
            self._set_status("✅ Região salva.")
        else:
            self._set_status("Seleção cancelada.")

    def _capturar_uma_vez(self):
        if not self._regiao:
            messagebox.showwarning("Região", "Selecione a região da tela primeiro.")
            return
        if not (HAS_MSS and HAS_PIL):
            messagebox.showerror("Dependência", "Instale mss e pillow:\n\npip install mss pillow")
            return
        if not HAS_TESSERACT and not HAS_EASYOCR:
            messagebox.showerror(
                "OCR",
                "Nenhum motor OCR instalado.\n\n"
                "pip install pytesseract\n(+instalar Tesseract)\n\nou:\npip install easyocr"
            )
            return
        self._set_status("Capturando...")
        self.update()
        img = capturar_regiao(self._regiao)
        if img is None:
            self._set_status("❌ Falha ao capturar tela.")
            return
        tickers = _tickers_da_mesa()
        texto = self._ocr(img)
        precos = _parse_precos(texto, tickers)
        self._salvar_precos(precos)
        self._atualizar_tabela_tickers()
        self._set_status(f"✅ {len(precos)} preço(s) capturado(s) · {_now_br()}")

    def _ocr(self, img: "Image.Image") -> str:
        if HAS_TESSERACT:
            return _extrair_texto_tesseract(img)
        if HAS_EASYOCR:
            if self._easyocr_reader is None:
                self._set_status("Carregando modelo EasyOCR (primeira vez demora ~30s)...")
                self.update()
                self._easyocr_reader = easyocr.Reader(["pt", "en"], gpu=False)
            return _extrair_texto_easyocr(img, self._easyocr_reader)
        return ""

    def _salvar_precos(self, precos: dict[str, float]):
        """Mescla preços capturados com os existentes e salva."""
        dados = _load_json(PRECOS_FILE, {})
        hora = _now_br()
        for ticker, preco in precos.items():
            dados[ticker] = {"preco": preco, "capturado_em": hora, "fonte": "OCR"}
        # Inclui preços manuais desta sessão
        for ticker, val_str in self._precos_manuais.items():
            try:
                dados[ticker] = {
                    "preco": float(val_str.replace(",", ".")),
                    "capturado_em": hora,
                    "fonte": "Manual",
                }
            except ValueError:
                pass
        _save_json(PRECOS_FILE, dados)

    def _toggle_auto(self):
        if self._rodando:
            self._rodando = False
            self._btn_auto.config(text="▶ Iniciar automático", bg="#14532d", fg="#86efac")
            self._set_status("Captura automática parada.")
        else:
            if not self._regiao:
                messagebox.showwarning("Região", "Selecione a região da tela primeiro.")
                return
            self._rodando = True
            self._btn_auto.config(text="⏹ Parar automático", bg="#7f1d1d", fg="#fca5a5")
            self._thread = Thread(target=self._loop_auto, daemon=True)
            self._thread.start()

    def _loop_auto(self):
        while self._rodando:
            self.after(0, self._capturar_uma_vez)
            intervalo = self._intervalo_var.get()
            for _ in range(intervalo * 10):
                if not self._rodando:
                    break
                time.sleep(0.1)

    def _editar_manual(self, event):
        """Duplo clique na tabela abre janela para digitar preço manual."""
        item = self._tree.focus()
        if not item:
            return
        vals = self._tree.item(item, "values")
        ticker = vals[0]
        if "(" in ticker:
            return

        popup = tk.Toplevel(self)
        popup.title(f"Preço manual — {ticker}")
        popup.configure(bg="#0f0f1a")
        popup.resizable(False, False)
        popup.grab_set()

        tk.Label(popup, text=f"Preço atual de {ticker}:",
                 bg="#0f0f1a", fg="#e2e8f0").pack(padx=16, pady=(12, 4))
        entry = tk.Entry(popup, font=("Arial", 14), width=12, justify="center",
                         bg="#1e293b", fg="#e2e8f0", insertbackground="white")
        entry.insert(0, self._precos_manuais.get(ticker, ""))
        entry.pack(padx=16, pady=4)
        entry.focus()

        def confirmar():
            self._precos_manuais[ticker] = entry.get().strip()
            self._atualizar_tabela_tickers()
            popup.destroy()

        entry.bind("<Return>", lambda e: confirmar())
        tk.Button(popup, text="✅ Confirmar", command=confirmar,
                  bg="#14532d", fg="#86efac", relief="flat", padx=8).pack(pady=(4, 12))

    def _salvar_manuais(self):
        self._salvar_precos({})
        self._atualizar_tabela_tickers()
        self._set_status(f"✅ Preços manuais salvos · {_now_br()}")

    def _set_status(self, msg: str):
        self._lbl_status.config(text=msg)


# ════════════════════════════════════════════════════════════════════════════
# INTEGRAÇÃO COM MESA DAY TRADE (leitura do arquivo)
# ════════════════════════════════════════════════════════════════════════════

def ler_precos_capturados() -> dict[str, float]:
    """
    Lê data/precos_capturados.json e retorna {TICKER: preco}.
    Ignora entradas com mais de 5 minutos de idade.
    Chamado por day_trade.py para sobrepor preços da API.
    """
    dados = _load_json(PRECOS_FILE, {})
    agora = datetime.now(tz=_TZ_BR)
    precos: dict[str, float] = {}
    for ticker, info in dados.items():
        try:
            h, m, s = info["capturado_em"].split(":")
            capturado = agora.replace(hour=int(h), minute=int(m), second=int(s))
            diff = abs((agora - capturado).total_seconds())
            if diff <= 300:   # máx 5 minutos
                precos[ticker] = float(info["preco"])
        except Exception:
            pass
    return precos


# ════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = App()
    app.mainloop()
