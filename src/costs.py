from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FeeParams:
    buy_brokerage: float = 4.90
    sell_brokerage: float = 4.90
    b3_rate_pct: float = 0.0300  # percent, e.g. 0.0300% for swing trade regular
    xp_operational_rate_pct: float = 5.90  # percent over brokerage + B3 charges
    ir_rate_pct: float = 0.0  # percent over net gain when applicable


def pct_to_decimal(value_pct: float) -> float:
    return float(value_pct) / 100.0


def side_cost(financial_value: float, brokerage: float, b3_rate_pct: float, xp_operational_rate_pct: float) -> dict[str, float]:
    """Return estimated side costs for a buy or sell transaction.

    This is a practical estimator for personal control, not a substitute for the brokerage note.
    """
    financial = max(float(financial_value), 0.0)
    brokerage = max(float(brokerage), 0.0)
    b3_rate = pct_to_decimal(b3_rate_pct)
    xp_operational_rate = pct_to_decimal(xp_operational_rate_pct)

    b3_cost = financial * b3_rate
    xp_operational = (brokerage + b3_cost) * xp_operational_rate
    total = brokerage + b3_cost + xp_operational
    return {
        "financeiro": round(financial, 2),
        "corretagem": round(brokerage, 2),
        "custos_b3": round(b3_cost, 2),
        "taxa_operacional_xp": round(xp_operational, 2),
        "total_custos": round(total, 2),
    }


def buy_total_cost(quantity: int, buy_price: float, fees: FeeParams) -> dict[str, float]:
    financial = int(quantity) * float(buy_price)
    costs = side_cost(financial, fees.buy_brokerage, fees.b3_rate_pct, fees.xp_operational_rate_pct)
    total = financial + costs["total_custos"]
    return {**costs, "total_investido": round(total, 2), "preco_medio_com_custos": round(total / max(int(quantity), 1), 4)}


def sell_simulation(quantity: int, sell_price: float, total_buy_cost: float, fees: FeeParams) -> dict[str, float]:
    financial = int(quantity) * float(sell_price)
    costs = side_cost(financial, fees.sell_brokerage, fees.b3_rate_pct, fees.xp_operational_rate_pct)
    sale_net_before_ir = financial - costs["total_custos"]
    gain_before_ir = sale_net_before_ir - float(total_buy_cost)
    ir = max(gain_before_ir, 0.0) * pct_to_decimal(fees.ir_rate_pct)
    sale_net_after_ir = sale_net_before_ir - ir
    gain_after_ir = sale_net_after_ir - float(total_buy_cost)
    return {
        **costs,
        "venda_bruta": round(financial, 2),
        "venda_liquida_antes_ir": round(sale_net_before_ir, 2),
        "lucro_antes_ir": round(gain_before_ir, 2),
        "ir_estimado": round(ir, 2),
        "venda_liquida_depois_ir": round(sale_net_after_ir, 2),
        "lucro_liquido_estimado": round(gain_after_ir, 2),
        "rentabilidade_liquida_pct": round((gain_after_ir / float(total_buy_cost)) * 100, 2) if total_buy_cost else 0.0,
    }


def required_sell_price(quantity: int, total_buy_cost: float, fees: FeeParams, target_profit_after_ir: float = 0.0) -> dict[str, float]:
    """Required sell price to reach target net profit after fees and optional IR.

    target_profit_after_ir is absolute BRL profit after IR. Use 0 for break-even.
    Formula assumes fixed brokerage per order and variable B3/operational components.
    """
    qty = max(int(quantity), 1)
    total_buy_cost = float(total_buy_cost)
    target_profit_after_ir = max(float(target_profit_after_ir), 0.0)

    ir_rate = pct_to_decimal(fees.ir_rate_pct)
    # Required pre-tax gain so that after IR the target profit is achieved.
    if target_profit_after_ir > 0 and ir_rate > 0:
        required_gain_before_ir = target_profit_after_ir / (1.0 - ir_rate)
    else:
        required_gain_before_ir = target_profit_after_ir

    required_net_sale_before_ir = total_buy_cost + required_gain_before_ir

    b3_rate = pct_to_decimal(fees.b3_rate_pct)
    op_rate = pct_to_decimal(fees.xp_operational_rate_pct)
    variable_sell_cost_rate = b3_rate * (1.0 + op_rate)
    fixed_sell_cost = fees.sell_brokerage * (1.0 + op_rate)

    denominator = qty * (1.0 - variable_sell_cost_rate)
    if denominator <= 0:
        raise ValueError("Parâmetros de taxa inválidos: denominador menor ou igual a zero.")
    price = (required_net_sale_before_ir + fixed_sell_cost) / denominator
    sim = sell_simulation(qty, price, total_buy_cost, fees)
    return {
        "preco_venda_necessario": round(price, 4),
        "valor_bruto_venda": round(qty * price, 2),
        "lucro_liquido_alvo": round(target_profit_after_ir, 2),
        **sim,
    }


def default_brokerage_profile(profile_name: str) -> dict[str, float]:
    name = profile_name.lower()
    if "escritório" in name or "escritorio" in name:
        return {"buy_brokerage": 18.90, "sell_brokerage": 18.90, "b3_rate_pct": 0.0300}
    if "day" in name and "rlp" in name and "sem" not in name:
        return {"buy_brokerage": 0.00, "sell_brokerage": 0.00, "b3_rate_pct": 0.0230}
    if "day" in name:
        return {"buy_brokerage": 2.90, "sell_brokerage": 2.90, "b3_rate_pct": 0.0230}
    return {"buy_brokerage": 4.90, "sell_brokerage": 4.90, "b3_rate_pct": 0.0300}
