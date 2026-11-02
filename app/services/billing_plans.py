"""Montants USD — pas de price_… dans .env."""

from typing import Literal

PaidPlan = Literal["starter", "pro", "agency"]

PLAN_USD: dict[PaidPlan, int] = {
    "starter": 10,
    "pro": 20,
    "agency": 40,
}


def plan_price_cents(plan: PaidPlan) -> int:
    return PLAN_USD[plan] * 100


def plan_product_name(plan: PaidPlan) -> str:
    return f"Yayra {plan.capitalize()}"


def plan_from_metadata(meta: dict | None) -> PaidPlan | None:
    if not meta:
        return None
    raw = str(meta.get("plan") or "").lower()
    if raw in PLAN_USD:
        return raw  # type: ignore[return-value]
    return None


def plan_from_amount_cents(amount: int) -> PaidPlan | None:
    for plan, usd in PLAN_USD.items():
        if usd * 100 == amount:
            return plan
    return None
