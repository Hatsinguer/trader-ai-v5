# TRADER AI v5 — BRIEFING COMPLETO PARA DESENVOLVIMENTO
## Documento de especificação técnica para Claude Code

---

## CONTEXTO DO PROJETO

Você está assumindo o desenvolvimento de uma ferramenta chamada **Trader AI**, atualmente na versão 4 (MVP). O objetivo é evoluir para a **versão 5**, transformando-a de um painel técnico em um **assistente operacional de análise de ações**, capaz de responder com clareza: *comprar, aguardar ou vender — e por quê*.

A ferramenta é de **uso pessoal**, roda em **Python com Streamlit**, e será hospedada gratuitamente no **Streamlit Community Cloud** (acessível também via Android pelo Chrome). O operador é o próprio desenvolvedor/usuário.

---

## ESTRUTURA ATUAL DA v4 (leia antes de qualquer alteração)

```
trader_ai_mvp_v4/
├── app.py                  # Interface principal Streamlit (~22k caracteres)
├── requirements.txt        # Dependências atuais
├── src/
│   ├── data.py             # fetch_binance_klines, fetch_yfinance_history
│   ├── indicators.py       # add_indicators (SMA, EMA, MACD, RSI, ATR, BB)
│   ├── analysis.py         # build_signal, build_opportunity_call, ai_commentary
│   ├── backtest.py         # ema_crossover_backtest
│   ├── storage.py          # watchlists, radar_history, positions (CSV/JSON)
│   └── costs.py            # FeeParams, buy_total_cost, sell_simulation, required_sell_price
└── data/                   # Criado em runtime
    ├── watchlists.json
    ├── radar_history.csv
    └── positions.csv
```

### O que a v4 já faz bem (PRESERVE TUDO ISSO):
- `build_signal()` com score por 5 indicadores técnicos
- `build_opportunity_call()` com stop/alvo calculados por múltiplo de ATR
- Backtest de cruzamento EMA12 × EMA26
- Cálculo de custos B3 reais (corretagem, taxa B3, taxa operacional XP)
- `required_sell_price()` — preço mínimo para zerar custos ou atingir lucro-alvo
- Armazenamento local em CSV/JSON (sem banco de dados)
- Suporte a Binance Spot para cripto (sem API key)
- Perfis de risco: Conservador, Moderado, Agressivo

---

## OBJETIVO DA v5 — VISÃO GERAL

A v5 deve funcionar como um **copiloto de operação**, não apenas como tela de indicadores. Para cada ativo, ela deve responder com precisão:

1. Quanto está valendo agora?
2. Esse preço está acima ou abaixo da média recente?
3. A tendência é favorável?
4. O ativo ainda tem espaço técnico de evolução?
5. O risco compensa a entrada?
6. Qual seria uma boa faixa de compra?
7. Onde a tese estaria errada?
8. Onde realizar lucro?
9. A melhor conduta agora é **COMPRAR**, **AGUARDAR** ou **VENDER**?

---

## MÓDULOS A DESENVOLVER — ESPECIFICAÇÃO COMPLETA

---

### MÓDULO 1 — CAMADA DE DADOS (prioridade máxima)

#### 1.1 Substituição de fonte para B3

Criar `src/data_brapi.py` com função `fetch_brapi_quote(ticker: str) -> dict`:

- **URL base:** `https://brapi.dev/api/quote/{ticker}`
- Parâmetros: `range=1y&interval=1d&fundamental=true&dividends=false`
- Retorno esperado: preço atual, variação no dia, volume, histórico OHLCV, dados fundamentalistas básicos
- Tratar erros HTTP com mensagem clara ao usuário
- Adicionar header `User-Agent` para evitar bloqueio

Criar `src/data_brapi.py` com função `fetch_brapi_history(ticker: str, range: str = "1y") -> pd.DataFrame`:
- Converte o histórico retornado pela Brapi no mesmo formato de colunas da v4: `datetime, open, high, low, close, volume`
- Compatível 100% com `add_indicators()` existente sem modificação

#### 1.2 Estratégia de fallback

Refatorar `src/data.py` com função principal `fetch_b3_history(ticker: str, period: str = "1y") -> tuple[pd.DataFrame, str]`:

```python
# Retorna (dataframe, fonte_utilizada)
# Tenta: 1. Brapi → 2. yfinance → 3. raises DataError
# fonte_utilizada = "Brapi (15 min delay)" | "Yahoo Finance (delay)" | "Binance Spot (tempo real)"
```

Regra:
- Tickers terminando em `.SA` → tentar Brapi primeiro, fallback yfinance
- Tickers terminando em `USDT` → usar Binance Spot direto
- Tickers sem sufixo → adicionar `.SA` e tentar Brapi

#### 1.3 Metadados de confiabilidade

Todo dado retornado deve incluir:
```python
@dataclass
class DataMeta:
    fonte: str           # "Brapi", "Yahoo Finance", "Binance Spot"
    atualizado_em: str   # "14:37:10" formato HH:MM:SS
    tipo_dado: str       # "15 min delay" | "tempo real" | "fim do pregão"
    confiavel: bool      # False se yfinance, True se Brapi ou Binance
```

Exibir esses metadados como badge colorido na interface: verde (Brapi/Binance), amarelo (Yahoo).

---

### MÓDULO 2 — INDICADORES TÉCNICOS EXPANDIDOS

Adicionar em `src/indicators.py` (sem remover os existentes):

#### 2.1 Novos indicadores

```python
# Estocástico Lento (14,3,3)
out["stoch_k"] = stochastic_k(high, low, close, period=14)
out["stoch_d"] = out["stoch_k"].rolling(3).mean()

# OBV — On Balance Volume
out["obv"] = on_balance_volume(close, volume)

# ADX — Average Directional Index (14 períodos)
out["adx"] = adx(high, low, close, period=14)
out["di_plus"] = directional_index_plus(high, low, close, period=14)
out["di_minus"] = directional_index_minus(high, low, close, period=14)

# VWAP diário (apenas para intraday; pular se intervalo for "1d")
out["vwap"] = vwap(high, low, close, volume)  # somente se interval != "1d"

# Médias adicionais
out["ema9"] = close.ewm(span=9, adjust=False).mean()
out["sma200"] = close.rolling(200).mean()
```

#### 2.2 Detecção de padrões de candle

Criar `src/patterns.py`:

```python
def detect_candle_patterns(df: pd.DataFrame) -> dict:
    """
    Detecta os últimos 3 candles. Retorna dict com padrões encontrados.
    Implementar: doji, martelo, estrela cadente, engolfo de alta, engolfo de baixa,
    harami de alta, harami de baixa, três soldados brancos, três corvos negros.
    """
```

---

### MÓDULO 3 — MOTOR DECISÓRIO v5 (núcleo principal)

Refatorar completamente `src/analysis.py`. Manter compatibilidade com `TechnicalSignal` e `OpportunityCall` existentes, mas expandir significativamente.

#### 3.1 Score expandido

Novo sistema de pontuação em `build_signal_v5()`:

| Critério | Pontos | Condição |
|---|---|---|
| EMA9 > EMA21 | +1 | Curto prazo positivo |
| EMA12 > EMA26 | +1 | Tendência curta confirmada |
| Preço > SMA50 | +1 | Médio prazo favorável |
| Preço > SMA200 | +2 | Longo prazo favorável (peso maior) |
| MACD cruzou acima | +2 | Sinal de entrada |
| MACD acima da linha | +1 | Tendência confirmada |
| RSI 40–60 | +1 | Zona neutra saudável |
| RSI < 30 | +1 | Sobrevenda (potencial reversão) |
| RSI > 70 | -2 | Sobrecompra (penalizar compra) |
| Volume > média 20p | +1 | Confirmação de volume |
| ADX > 25 | +1 | Tendência forte |
| Estoc K cruzou D acima | +1 | Confirmação estocástico |
| Bollinger: preço < banda inferior | +1 | Preço esticado para baixo |
| Bollinger: preço > banda superior | -1 | Preço esticado para cima |
| Padrão bullish detectado | +2 | Candle de reversão alta |
| Padrão bearish detectado | -2 | Candle de reversão baixa |

Score máximo teórico: ~17. Classificação:
- ≥ 8: `COMPRA_FORTE`
- 5–7: `COMPRA_TÉCNICA`
- 3–4: `MONITORAR_COMPRA`
- -2 a 2: `AGUARDAR`
- ≤ -3: `EVITAR_COMPRA`
- ≤ -6: `VENDA_TÉCNICA`

#### 3.2 Projeções técnicas por cenário

Adicionar em `OpportunityCall`:

```python
@dataclass
class OpportunityCall:
    # Campos existentes (manter todos)
    call: str
    score: int
    confidence: str
    technical_bias: str
    reference_price: float
    invalidation_price: float | None
    target_1_atr: float | None
    target_2_atr: float | None
    risk_reward_note: str

    # NOVOS CAMPOS v5
    buy_zone_low: float | None       # Faixa ideal de compra — limite inferior
    buy_zone_high: float | None      # Faixa ideal de compra — limite superior
    scenario_conservative: float | None   # Cenário conservador (1x ATR + tendência)
    scenario_base: float | None           # Cenário base (2x ATR)
    scenario_optimistic: float | None     # Cenário otimista (4x ATR)
    horizon_pregoes: int                  # Horizonte estimado em pregões (5, 20 ou 60)
    distance_from_sma20_pct: float        # % de distância do preço em relação à SMA20
    trend_strength: str                   # "forte alta" | "moderada alta" | "neutra" | "moderada baixa" | "forte baixa"
    risk_reward_ratio: float | None       # Risco:Retorno como número (ex: 2.4 = 1:2.4)
    action_plan: str                      # Texto do plano: quando comprar, evitar, vender
    justification_bullets: list[str]      # Lista de motivos objetivos da decisão
```

#### 3.3 Lógica da zona de compra

```python
# Buy zone: faixa entre SMA20 e SMA20 - 0.5*ATR (zona de pullback ideal)
# Se preço já está nessa zona: indicar entrada
# Se preço está acima: aguardar pullback ou rompimento confirmado
# Se preço está abaixo da SMA50: aguardar recuperação

buy_zone_high = sma20
buy_zone_low = sma20 - (0.5 * atr14)
```

#### 3.4 Integração com Claude API

Refatorar `ai_commentary()` para usar **Claude API** (claude-sonnet-4-6) no lugar de OpenAI:

```python
def ai_commentary(signal, symbol: str, timeframe: str, position: dict | None = None) -> str:
    """
    Se ANTHROPIC_API_KEY estiver configurada, usa claude-sonnet-4-6.
    Se não estiver, retorna fallback determinístico (já existente).
    
    O prompt deve instruir o modelo a:
    - Analisar o sinal técnico recebido como JSON
    - Comentar em linguagem clara, direta, sem jargão desnecessário
    - Mencionar o contexto da posição atual SE fornecido (position != None)
    - Nunca prometer retorno; sempre mencionar que é análise técnica
    - Resposta máxima: 250 palavras
    - Idioma: português do Brasil
    """
    
    # Usar endpoint: https://api.anthropic.com/v1/messages
    # Model: claude-sonnet-4-6
    # Max tokens: 400
```

Adicionar ao `.env.example`:
```
ANTHROPIC_API_KEY=sk-ant-xxxxxxx
# OPENAI_API_KEY=sk-xxxxxxx  (legado, ainda suportado como fallback)
```

---

### MÓDULO 4 — INTERFACE v5 (os 4 blocos)

Reestruturar `app.py` mantendo o que existe, adicionando uma nova aba "Análise Completa" com os 4 blocos visuais:

#### Bloco 1 — Situação atual

```
┌─────────────────────────────────────────────────┐
│  PETR4 · Petróleo Brasileiro S.A.               │
│  R$ 38,45   ▲ +1,23%   Vol: R$ 892 mi          │
│  Atualizado: 14:37:10 · Fonte: Brapi (15 min)   │
└─────────────────────────────────────────────────┘
```

Implementar como `st.metric` com delta colorido + badge de fonte.

#### Bloco 2 — Leitura do período

```
┌─────────────────────────────────────────────────┐
│  Período: 60 pregões                            │
│  Média (SMA20): R$ 36,80                        │
│  Preço vs. média: +4,48% (acima)                │
│  Máxima do período: R$ 41,20                    │
│  Mínima do período: R$ 33,10                    │
│  Tendência: alta moderada                        │
│  Força (ADX): 28 (tendência presente)           │
│  Volatilidade (ATR14): R$ 1,42                  │
└─────────────────────────────────────────────────┘
```

#### Bloco 3 — Projeção técnica

```
┌─────────────────────────────────────────────────┐
│  CENÁRIOS TÉCNICOS PROVÁVEIS                    │
│  Conservador: R$ 39,80  (horizonte: 5 pregões)  │
│  Base:        R$ 41,30  (horizonte: 20 pregões) │
│  Otimista:    R$ 44,50  (horizonte: 60 pregões) │
│  ⚠ Projeção baseada em ATR e tendência.         │
│  Não é previsão de preço.                       │
└─────────────────────────────────────────────────┘
```

Usar `st.progress` bar visual para os 3 cenários.

#### Bloco 4 — Assistente decisório (destaque visual máximo)

```
┌─────────────────────────────────────────────────┐
│  ⚪ CONDUTA TÉCNICA: AGUARDAR                   │  <- cor muda: verde/amarelo/vermelho
│                                                 │
│  Justificativa:                                 │
│  • EMA12 acima da EMA26 (viés positivo)         │
│  • Preço 4,4% acima da SMA20 (esticado)         │
│  • RSI em 62 (neutro, sem sobrecompra)          │
│  • Volume abaixo da média (sem confirmação)     │
│                                                 │
│  PLANO DE AÇÃO:                                 │
│  Comprar se: preço recuar para R$ 36,40–37,20   │
│  Comprar se: romper R$ 40,10 com volume         │
│  Evitar se: perder R$ 35,80                     │
│  Realizar se: atingir R$ 41,30 ou R$ 44,50      │
│                                                 │
│  Stop técnico: R$ 35,80                         │
│  Alvo 1:       R$ 41,30  (R:R 1:1,9)           │
│  Alvo 2:       R$ 44,50  (R:R 1:3,8)           │
│  Confiança:    média (score 5/17)               │
└─────────────────────────────────────────────────┘
```

Cores:
- `COMPRA_FORTE` / `COMPRA_TÉCNICA` → `st.success` (verde)
- `MONITORAR_COMPRA` / `AGUARDAR` → `st.warning` (amarelo)
- `EVITAR_COMPRA` / `VENDA_TÉCNICA` → `st.error` (vermelho)

---

### MÓDULO 5 — RADAR DE OPORTUNIDADES v5

Refatorar completamente a aba de radar em `app.py`.

#### 5.1 Filtros disponíveis

```python
# Painel lateral de filtros do radar
filtro_tipo = st.multiselect("Tipo de ativo", ["Ações ON", "Ações PN", "FIIs", "ETFs", "BDRs"])
filtro_volume_min = st.number_input("Volume financeiro mínimo (R$ mi/dia)", value=5.0)
filtro_call = st.multiselect("Calls a exibir", ["COMPRA_FORTE", "COMPRA_TÉCNICA", "MONITORAR_COMPRA"])
filtro_rsi_max = st.slider("RSI máximo (evitar sobrecompra)", 50, 80, 70)
filtro_adx_min = st.slider("ADX mínimo (força de tendência)", 0, 40, 20)
filtro_score_min = st.slider("Score técnico mínimo", 0, 17, 4)
```

#### 5.2 Classificação automática de ativos

Criar `src/asset_classifier.py`:

```python
ASSET_TYPES = {
    # ETFs
    "BOVA11": "ETF", "SMAL11": "ETF", "IVVB11": "ETF", "HASH11": "ETF",
    "SPXI11": "ETF", "DIVO11": "ETF", "GOLD11": "ETF",
    # FIIs — padrão: 4 letras + 11
    # Detectar automaticamente: se ticker termina com "11" e não é ETF conhecido → FII
    # BDRs — padrão: 4 letras + número + 34
    # Ações ON: terminam em 3
    # Ações PN: terminam em 4
}

def classify_asset(ticker: str) -> str:
    """Retorna: "ETF" | "FII" | "BDR" | "Ação ON" | "Ação PN" | "Cripto" | "Desconhecido" """
```

#### 5.3 Ranking por risco-retorno

```python
def calculate_radar_score(opp: OpportunityCall) -> float:
    """
    Score composto para ranking do radar.
    Considera: score técnico, R:R, confiança, distância da zona de compra.
    Retorna valor 0–100 para ordenação.
    """
    base = opp.score * 5
    rr_bonus = min(opp.risk_reward_ratio * 10, 30) if opp.risk_reward_ratio else 0
    confidence_mult = {"alta": 1.2, "moderada": 1.0, "baixa": 0.7}.get(opp.confidence, 1.0)
    distance_penalty = max(0, opp.distance_from_sma20_pct - 5) * 2  # penaliza esticado
    return max(0, (base + rr_bonus) * confidence_mult - distance_penalty)
```

#### 5.4 Lista padrão expandida

```python
DEFAULT_B3_RADAR = [
    # Blue chips
    "PETR4.SA", "VALE3.SA", "ITUB4.SA", "BBAS3.SA", "BBDC4.SA",
    "WEGE3.SA", "ABEV3.SA", "RENT3.SA", "PRIO3.SA", "SUZB3.SA",
    "MGLU3.SA", "LREN3.SA", "HAPV3.SA", "RDOR3.SA", "GGBR4.SA",
    # ETFs
    "BOVA11.SA", "SMAL11.SA", "IVVB11.SA", "HASH11.SA",
    # FIIs representativos
    "MXRF11.SA", "KNRI11.SA", "HGLG11.SA", "VISC11.SA",
]
```

#### 5.5 Snapshot histórico do radar

Manter funcionalidade existente de `save_radar_snapshot()` mas adicionar:
- Comparação com snapshot anterior (quantos ativos melhoraram/pioraram de call)
- Alerta visual quando ativo muda de `AGUARDAR` → `COMPRA_TÉCNICA`

---

### MÓDULO 6 — ANÁLISE FUNDAMENTALISTA BÁSICA

Criar `src/fundamentals.py`:

#### 6.1 Dados via Brapi

```python
def fetch_fundamentals(ticker: str) -> dict | None:
    """
    Busca dados fundamentalistas via Brapi.
    Endpoint: https://brapi.dev/api/quote/{ticker}?fundamental=true
    
    Retorna dict com (quando disponível):
    - pl: P/L (Preço/Lucro)
    - pvp: P/VP (Preço/Valor Patrimonial)
    - dy: Dividend Yield (%)
    - roe: Return on Equity (%)
    - roic: Return on Invested Capital (%)
    - ebitda_margin: Margem EBITDA (%)
    - net_margin: Margem Líquida (%)
    - debt_equity: Dívida/PL
    - sector: Setor
    - subsector: Subsetor
    - market_cap: Capitalização de mercado
    """
```

#### 6.2 Bloco fundamentalista na interface

Exibir como seção colapsável (`st.expander`) após o Bloco 4:

```
┌─────────────────────────────────────────────────┐
│  CONTEXTO FUNDAMENTALISTA                       │
│  Setor: Petróleo, Gás e Biocombustíveis         │
│  P/L: 4,2 · P/VP: 0,8 · DY: 8,3%              │
│  ROE: 22,1% · Margem Liq: 18,4%                │
│  Dívida/PL: 0,4 (controlada)                   │
│                                                 │
│  Leitura rápida: valuation descontado (P/VP<1), │
│  dividend yield atrativo, boa rentabilidade.    │
└─────────────────────────────────────────────────┘
```

A "leitura rápida" deve ser gerada deterministicamente por regras simples:
- P/L < 10 → "valuation atrativo"
- P/VP < 1 → "abaixo do valor patrimonial"
- DY > 5% → "dividend yield atrativo"
- ROE > 15% → "boa rentabilidade"
- Dívida/PL > 2 → "alavancagem elevada"

---

### MÓDULO 7 — MINHAS POSIÇÕES v5

Expandir o módulo de gestão de posições existente.

#### 7.1 Estrutura da posição

```python
@dataclass
class Position:
    ticker: str
    quantity: int
    avg_buy_price: float        # Preço médio de compra
    total_invested: float       # Total investido com custos
    buy_date: str               # Data da primeira compra
    stop_price: float | None    # Stop definido pelo operador
    target_price: float | None  # Alvo definido pelo operador
    notes: str                  # Anotações livres
    # Calculados em runtime (não salvos):
    # current_price, unrealized_pnl, pnl_pct, suggested_action
```

#### 7.2 Análise da posição — modo "Analisar minha posição"

Quando o usuário tem uma posição cadastrada para o ativo analisado, o Bloco 4 deve ser substituído pelo modo posição, exibindo:

```
┌─────────────────────────────────────────────────┐
│  SUA POSIÇÃO EM PETR4                           │
│  Comprado a: R$ 35,20 (180 ações)              │
│  Total investido: R$ 6.362,40 (com custos)      │
│                                                 │
│  Preço atual: R$ 38,45                          │
│  Resultado atual: +R$ 585,00 (+9,2%)            │
│  Preço mínimo de venda (break-even): R$ 35,42   │
│                                                 │
│  CONDUTA PARA A SUA POSIÇÃO:                    │
│  ✅ MANTER COM STOP AJUSTADO                    │
│                                                 │
│  Motivo: posição com lucro de 9,2%.             │
│  Ativo ainda em tendência de alta.              │
│  Sugestão: subir stop para R$ 36,80 (SMA20).   │
│  Realizar parcial se atingir R$ 41,30.          │
│                                                 │
│  Stop sugerido: R$ 36,80 (SMA20 atual)         │
│  Alvo 1: R$ 41,30 · Alvo 2: R$ 44,50          │
│  IR estimado se vender agora: R$ 87,75          │
└─────────────────────────────────────────────────┘
```

#### 7.3 Lógica de conduta para posições

```python
def position_action(position: Position, opp: OpportunityCall, current_price: float) -> str:
    pnl_pct = (current_price - position.avg_buy_price) / position.avg_buy_price * 100
    
    if current_price <= (position.stop_price or opp.invalidation_price):
        return "VENDER — stop atingido"
    elif opp.call in ["VENDA_TÉCNICA", "EVITAR_COMPRA"]:
        return "AVALIAR SAÍDA — sinal técnico deteriorou"
    elif pnl_pct > 15 and opp.distance_from_sma20_pct > 8:
        return "REALIZAR PARCIAL — lucro elevado, ativo esticado"
    elif pnl_pct > 0 and opp.call in ["COMPRA_TÉCNICA", "COMPRA_FORTE"]:
        return "MANTER COM STOP AJUSTADO"
    elif pnl_pct < -5 and opp.call == "AGUARDAR":
        return "MONITORAR — prejuízo moderado, aguardar reversão"
    else:
        return "MANTER"
```

---

### MÓDULO 8 — SISTEMA DE ALERTAS

Criar `src/alerts.py` e nova aba "Alertas" no app.

#### 8.1 Estrutura de alertas

```python
@dataclass
class Alert:
    ticker: str
    tipo: str        # "preco_abaixo" | "preco_acima" | "call_mudou" | "rsi_abaixo" | "rsi_acima"
    valor: float     # Preço ou nível de RSI
    ativo: bool      # Se o alerta ainda está ativo
    criado_em: str
    disparado_em: str | None
    mensagem: str    # Texto customizável
```

Salvar em `data/alerts.json`.

#### 8.2 Verificação de alertas

```python
def check_alerts(alerts: list[Alert], current_data: dict) -> list[Alert]:
    """
    Verifica quais alertas foram disparados.
    Chamada ao abrir o app e a cada refresh.
    Alertas disparados exibem banner vermelho/verde no topo do app.
    """
```

#### 8.3 Interface de alertas

Na aba Alertas:
- Tabela de alertas ativos com botão "Remover"
- Formulário: "Adicionar alerta para PETR4 quando preço < R$ 36,00"
- Histórico de alertas disparados nos últimos 30 dias

---

### MÓDULO 9 — EXPORTAÇÃO PDF

Criar `src/report.py` usando a biblioteca `reportlab`.

#### 9.1 Relatório por ativo

```python
def generate_analysis_report(
    ticker: str,
    signal: TechnicalSignal,
    opp: OpportunityCall,
    fundamentals: dict | None,
    position: Position | None,
    chart_image_bytes: bytes | None
) -> bytes:
    """
    Gera PDF com:
    - Cabeçalho: nome do ativo, data/hora, fonte dos dados
    - Bloco 1: preço atual, variação, volume
    - Bloco 2: leitura do período (médias, tendência, volatilidade)
    - Bloco 3: cenários de projeção (conservador, base, otimista)
    - Bloco 4: conduta do assistente + justificativa + plano
    - Bloco 5 (se disponível): fundamentalista
    - Bloco 6 (se disponível): posição atual + resultado
    - Gráfico de candles (se chart_image_bytes fornecido)
    - Rodapé: "Análise técnica para uso pessoal. Não é recomendação de investimento."
    """
```

#### 9.2 Botão de download na interface

```python
pdf_bytes = generate_analysis_report(...)
st.download_button(
    label="📄 Exportar análise em PDF",
    data=pdf_bytes,
    file_name=f"trader_ai_{ticker}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
    mime="application/pdf"
)
```

---

### MÓDULO 10 — BACKTEST EXPANDIDO

Expandir `src/backtest.py` com estratégias adicionais.

#### 10.1 Estratégias a implementar

```python
def macd_signal_backtest(df: pd.DataFrame) -> dict:
    """Compra no cruzamento MACD acima da linha de sinal. Vende no cruzamento para baixo."""

def rsi_reversal_backtest(df: pd.DataFrame, buy_rsi: float = 35, sell_rsi: float = 65) -> dict:
    """Compra quando RSI < buy_rsi. Vende quando RSI > sell_rsi."""

def bollinger_reversion_backtest(df: pd.DataFrame) -> dict:
    """Compra quando preço toca banda inferior. Vende na banda superior ou SMA20."""
```

#### 10.2 Resultado padronizado

Todos os backtests devem retornar o mesmo formato:
```python
{
    "estrategia": str,
    "periodo": str,            # "2022-01-01 a 2024-12-31"
    "total_operacoes": int,
    "operacoes_ganhadoras": int,
    "win_rate_pct": float,
    "retorno_total_pct": float,
    "retorno_buy_hold_pct": float,
    "maior_ganho_pct": float,
    "maior_perda_pct": float,
    "drawdown_maximo_pct": float,
    "sharpe_simplificado": float,
}
```

---

### MÓDULO 11 — INTERFACE GERAL v5

#### 11.1 Estrutura de abas

```python
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Análise Completa",
    "Radar de Oportunidades",
    "Minhas Posições",
    "Alertas",
    "Backtest",
    "Histórico"
])
```

#### 11.2 Seletor de ativo melhorado

```python
# Barra de pesquisa no topo (presente em todas as abas)
ticker_input = st.text_input("Ativo", placeholder="Ex: PETR4, VALE3, BOVA11, BTCUSDT")
# Auto-adicionar .SA se necessário
# Mostrar nome da empresa ao lado do ticker após carregamento
```

#### 11.3 Painel de perfil de risco

```python
# Na sidebar, visível em todas as abas
perfil = st.radio("Perfil de risco", ["Conservador", "Moderado", "Agressivo"])
# Afeta: multiplicadores de stop/alvo no ATR, threshold de score para calls
```

#### 11.4 Aviso legal fixo

```python
# Rodapé fixo em todas as páginas
st.caption(
    "⚠️ Esta ferramenta é um assistente de apoio técnico para uso pessoal. "
    "Não executa ordens. Não constitui recomendação de investimento. "
    "Verifique liquidez, notícias, custos e seu perfil de risco antes de operar. "
    "Responsabilidade exclusiva do operador."
)
```

#### 11.5 Cache e performance

```python
# Todos os fetches de dados com cache de 5 minutos
@st.cache_data(ttl=300)
def load_data_cached(ticker, period, interval):
    ...

# Radar com cache de 10 minutos (operação mais pesada)
@st.cache_data(ttl=600)
def run_radar_cached(tickers, perfil):
    ...
```

---

## REQUIREMENTS.TXT — v5

```
streamlit>=1.38
pandas>=2.2
numpy>=1.26
plotly>=5.22
requests>=2.32
yfinance>=0.2.54
anthropic>=0.40.0
python-dotenv>=1.0
reportlab>=4.2
ta>=0.11.0
```

Remover `openai` como dependência obrigatória (manter como opcional/legado).

---

## ARQUIVO .ENV.EXAMPLE

```env
# Claude API (recomendado para comentários de IA)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxx

# OpenAI (legado, ainda suportado como fallback)
# OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
# OPENAI_MODEL=gpt-4o

# Brapi (opcional — sem key usa plano gratuito com limite de requisições)
# BRAPI_TOKEN=xxxxxxxxxxxxxxxx

# Configurações da ferramenta
TRADER_AI_PERFIL_DEFAULT=Moderado
TRADER_AI_PERIODO_DEFAULT=1y
```

---

## ESTRUTURA DE ARQUIVOS — v5 COMPLETA

```
trader_ai_v5/
├── app.py                      # Interface principal (refatorado)
├── requirements.txt            # Atualizado
├── .env.example                # Template de variáveis
├── README.md                   # Atualizado com instruções
│
├── src/
│   ├── __init__.py
│   ├── data.py                 # fetch_b3_history com fallback Brapi→yfinance
│   ├── data_brapi.py           # NOVO: integração Brapi
│   ├── indicators.py           # Expandido com ADX, OBV, Estocástico, VWAP
│   ├── patterns.py             # NOVO: detecção de padrões de candle
│   ├── analysis.py             # Refatorado: score v5, OpportunityCall expandido
│   ├── fundamentals.py         # NOVO: dados fundamentalistas via Brapi
│   ├── backtest.py             # Expandido: 3 estratégias
│   ├── storage.py              # Expandido: alerts, positions com novos campos
│   ├── costs.py                # Mantido sem alteração
│   ├── alerts.py               # NOVO: sistema de alertas
│   ├── asset_classifier.py     # NOVO: classificação de tipo de ativo
│   └── report.py               # NOVO: exportação PDF com reportlab
│
└── data/                       # Criado em runtime, não versionar
    ├── watchlists.json
    ├── radar_history.csv
    ├── positions.csv
    ├── alerts.json             # NOVO
    └── decisions_log.json      # NOVO: histórico de análises
```

---

## REGRAS INEGOCIÁVEIS DE DESENVOLVIMENTO

1. **Não quebrar o que já funciona.** Todo código existente que funciona deve ser preservado. Adicione, não substitua, a menos que seja explicitamente necessário.

2. **Fallback sempre.** Cada integração externa (Brapi, Claude API, yfinance, Binance) deve ter tratamento de erro silencioso com fallback claro. A ferramenta nunca pode travar por falha de API.

3. **Sem banco de dados.** Armazenamento exclusivamente em CSV e JSON locais. Sem SQLite, sem PostgreSQL, sem Redis.

4. **Sem autenticação.** A ferramenta é de uso pessoal. Sem login, sem múltiplos usuários, sem senhas.

5. **Responsividade.** Layout deve funcionar em tela estreita (Android Chrome). Evitar tabelas muito largas sem scroll. Usar `use_container_width=True` em gráficos.

6. **Aviso legal.** O texto de disclaimer deve aparecer no rodapé de todas as páginas. Não pode ser removido.

7. **Sem ordens automáticas.** A ferramenta apenas analisa e exibe. Não integra com corretoras, não executa ordens.

8. **Dados com fonte visível.** Toda cotação exibida deve mostrar de onde veio e quando foi atualizada.

9. **Idioma.** Interface 100% em português do Brasil. Código e comentários podem ser em inglês.

10. **Custo de API controlado.** Chamadas à Claude API só são feitas quando o usuário clica explicitamente em "Gerar comentário IA". Nunca chamadas automáticas em background.

---

## ORDEM DE DESENVOLVIMENTO RECOMENDADA

Execute nesta sequência para ter valor desde o início:

**Sprint 1 (base sólida):**
- [ ] Criar `src/data_brapi.py`
- [ ] Refatorar `src/data.py` com fallback e `DataMeta`
- [ ] Adicionar badge de fonte na interface atual
- [ ] Atualizar `requirements.txt`

**Sprint 2 (coração da v5):**
- [ ] Expandir `src/indicators.py` com ADX, Estocástico, OBV
- [ ] Criar `src/patterns.py`
- [ ] Refatorar `src/analysis.py` com score v5 e `OpportunityCall` expandido
- [ ] Implementar os 4 blocos visuais na aba "Análise Completa"

**Sprint 3 (radar e fundamentalista):**
- [ ] Criar `src/asset_classifier.py`
- [ ] Criar `src/fundamentals.py`
- [ ] Refatorar aba Radar com filtros e ranking por R:R
- [ ] Adicionar bloco fundamentalista na análise individual

**Sprint 4 (posições e alertas):**
- [ ] Expandir módulo de posições com modo "Analisar minha posição"
- [ ] Criar `src/alerts.py`
- [ ] Implementar aba Alertas na interface

**Sprint 5 (relatórios e backtest):**
- [ ] Criar `src/report.py` com exportação PDF
- [ ] Expandir `src/backtest.py` com 3 estratégias
- [ ] Implementar aba Backtest com comparativo de estratégias

**Sprint 6 (IA e refinamento):**
- [ ] Integrar Claude API em `ai_commentary()`
- [ ] Implementar `decisions_log.json` para histórico
- [ ] Testes finais e ajustes de layout mobile

---

## TESTE DE VALIDAÇÃO FINAL

Ao concluir cada sprint, verificar:

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar a ferramenta
streamlit run app.py

# Verificar:
# 1. PETR4.SA carrega sem erro
# 2. Os 4 blocos aparecem com dados preenchidos
# 3. Radar varre lista padrão sem travar
# 4. Adicionar posição e ver análise de posição
# 5. Exportar PDF de uma análise
# 6. Funciona em janela estreita (simular mobile no Chrome: F12 → modo responsivo)
```

---

*Briefing gerado em: junho de 2026*
*Versão base: Trader AI MVP v4*
*Destino: Trader AI v5*
*Desenvolvido com Claude Code — claude-sonnet-4-6*
