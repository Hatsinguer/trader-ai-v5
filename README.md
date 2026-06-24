# Trader AI MVP v4

Ferramenta local em Streamlit para análise técnica, radar de ações, listas salvas, histórico e gestão de posição com cálculo de preço mínimo de venda.

## Rodar no Windows

```bat
cd /d C:\Users\Jerfersom\Downloads\trader_ai_mvp_v4\trader_ai_mvp_v4
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m streamlit run app.py
```

## Novidade da v4

Modo **Gestão de posição / Venda**:

- calcula total investido com custos;
- calcula preço de venda para zerar custos;
- calcula preço de venda para lucro líquido alvo;
- considera corretagem de compra e venda;
- considera custos B3 estimados;
- considera taxa operacional XP estimada;
- permite incluir IR estimado no alvo;
- salva posições em `data/positions.csv`.

A nota de corretagem é a fonte final de conferência.
