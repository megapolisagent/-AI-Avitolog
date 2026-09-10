#!/usr/bin/env python3
"""Детерминированный расчёт ежемесячного платежа по ипотеке/рассрочке для
Модуля listing-fin-offer. Считает аннуитетный платёж (ипотека) или равный платёж
с наценкой либо без неё (рассрочка) — никогда не прикидывается моделью в уме,
та же логика, что traffic-calculator для ставки CPA/CPX (SKILL.md, «Принцип»).

Использование: python3 calculate_payment.py <путь к offer.json>
Формат offer.json — см. INPUT_SCHEMA ниже.
"""
from __future__ import annotations

import json
import sys


INPUT_SCHEMA = {
    "price": "число — полная цена лота, ₽, обязательно",
    "down_payment": "число, опционально — сумма первоначального взноса, ₽",
    "down_payment_percent": "число, опционально — ПВ в процентах от price (используется, если down_payment не дан)",
    "term_months": "целое число — срок в месяцах, обязательно",
    "annual_rate_percent": "число, опционально — годовая ставка ипотеки, % (взаимоисключимо с installment_markup_percent)",
    "installment_markup_percent": "число, опционально — наценка рассрочки за весь срок, % (0 — беспроцентная рассрочка)",
}


class InputError(ValueError):
    pass


def _require(offer: dict, field: str):
    if field not in offer or offer[field] is None:
        raise InputError(
            f"Обязательное поле «{field}» не задано в offer.json — не считаю на "
            "предположении (SKILL.md, «Принцип»)."
        )
    return offer[field]


def _resolve_down_payment(offer: dict, price: float) -> float:
    if offer.get("down_payment") is not None:
        return float(offer["down_payment"])
    if offer.get("down_payment_percent") is not None:
        return price * float(offer["down_payment_percent"]) / 100.0
    raise InputError(
        "Нужен либо down_payment, либо down_payment_percent — не выдумываю ПВ по умолчанию."
    )


def calculate_payment(offer: dict) -> dict:
    price = float(_require(offer, "price"))
    term_months = int(_require(offer, "term_months"))
    if term_months <= 0:
        raise InputError("term_months должен быть положительным целым числом.")

    down_payment_amount = _resolve_down_payment(offer, price)
    principal = price - down_payment_amount
    if principal <= 0:
        raise InputError("Первоначальный взнос не может быть больше или равен полной цене.")

    has_rate = offer.get("annual_rate_percent") is not None
    has_markup = offer.get("installment_markup_percent") is not None
    if has_rate and has_markup:
        raise InputError(
            "Указаны и annual_rate_percent, и installment_markup_percent — это два "
            "разных финансовых продукта (ипотека vs рассрочка), выбери один."
        )
    if not has_rate and not has_markup:
        raise InputError(
            "Нужен либо annual_rate_percent (ипотека), либо installment_markup_percent "
            "(рассрочка, 0 — беспроцентная) — не выдумываю условия."
        )

    if has_rate:
        annual_rate = float(offer["annual_rate_percent"])
        if annual_rate == 0:
            monthly_payment = principal / term_months
        else:
            monthly_rate = annual_rate / 100.0 / 12.0
            factor = (1 + monthly_rate) ** term_months
            monthly_payment = principal * monthly_rate * factor / (factor - 1)
        product = "mortgage"
    else:
        markup_percent = float(offer["installment_markup_percent"])
        total_with_markup = principal * (1 + markup_percent / 100.0)
        monthly_payment = total_with_markup / term_months
        product = "installment"

    total_paid = monthly_payment * term_months
    overpayment = total_paid - principal
    overpayment_percent = (overpayment / principal * 100.0) if principal else 0.0

    return {
        "product": product,
        "price": round(price, 2),
        "down_payment_amount": round(down_payment_amount, 2),
        "principal": round(principal, 2),
        "term_months": term_months,
        "monthly_payment": round(monthly_payment, 2),
        "total_paid": round(total_paid, 2),
        "overpayment": round(overpayment, 2),
        "overpayment_percent": round(overpayment_percent, 2),
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Использование: python3 {sys.argv[0]} <путь к offer.json>")
    with open(sys.argv[1], encoding="utf-8") as f:
        offer = json.load(f)
    result = calculate_payment(offer)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
