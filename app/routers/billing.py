"""Checkout Stripe + factures (clés dans chatagentapi/.env)."""

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.services.auth import AgentUser, get_current_agent
from app.services.billing_plans import (
    PaidPlan,
    plan_from_amount_cents,
    plan_from_metadata,
    plan_price_cents,
    plan_product_name,
)
from app.services.supabase_client import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])

PAID_PLANS: tuple[PaidPlan, ...] = ("starter", "pro", "agency")


def _stripe():
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="STRIPE_SECRET_KEY manquant")
    stripe.api_key = settings.stripe_secret_key
    return stripe


def _owner_org(supabase, user_id: str) -> dict:
    org = (
        supabase.table("organizations")
        .select(
            "id, name, owner_id, subscription_plan, subscription_status, "
            "stripe_customer_id, stripe_subscription_id"
        )
        .eq("owner_id", user_id)
        .limit(1)
        .execute()
    )
    if not org.data:
        raise HTTPException(status_code=404, detail="Organisation introuvable")
    return org.data[0]


class CheckoutBody(BaseModel):
    plan: str = Field(min_length=3)


@router.post("/checkout")
async def billing_checkout(body: CheckoutBody, user: AgentUser = Depends(get_current_agent)):
    plan = body.plan.strip().lower()
    if plan not in PAID_PLANS:
        raise HTTPException(status_code=400, detail="Plan invalide")

    paid: PaidPlan = plan  # type: ignore[assignment]
    supabase = get_supabase()
    org = _owner_org(supabase, user.user_id)
    st = _stripe()
    base = settings.app_url.rstrip("/")

    customer_id = org.get("stripe_customer_id")
    if not customer_id:
        customer = st.Customer.create(
            email=user.email,
            name=org.get("name") or "Organization",
            metadata={"organization_id": org["id"]},
        )
        customer_id = customer.id
        supabase.table("organizations").update({"stripe_customer_id": customer_id}).eq(
            "id", org["id"]
        ).execute()

    price_data = {
        "currency": "usd",
        "unit_amount": plan_price_cents(paid),
        "recurring": {"interval": "month"},
        "product_data": {"name": plan_product_name(paid), "metadata": {"plan": paid}},
    }

    sub_id = org.get("stripe_subscription_id")
    if sub_id:
        sub = st.Subscription.retrieve(sub_id)
        item_id = sub["items"]["data"][0]["id"] if sub["items"]["data"] else None
        if not item_id:
            raise HTTPException(status_code=502, detail="Abonnement Stripe invalide")
        st.Subscription.modify(
            sub_id,
            items=[{"id": item_id, "price_data": price_data}],
            proration_behavior="create_prorations",
            metadata={"organization_id": org["id"], "plan": paid},
        )
        supabase.table("organizations").update(
            {"subscription_plan": paid, "subscription_status": "active"}
        ).eq("id", org["id"]).execute()
        return {"ok": True, "updated": True, "plan": paid}

    session = st.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"quantity": 1, "price_data": price_data}],
        success_url=f"{base}/dashboard/pricing?checkout=success",
        cancel_url=f"{base}/dashboard/pricing?checkout=cancel",
        metadata={"organization_id": org["id"], "plan": paid},
        subscription_data={"metadata": {"organization_id": org["id"], "plan": paid}},
    )
    if not session.url:
        raise HTTPException(status_code=502, detail="Session Stripe invalide")
    return {"url": session.url}


@router.get("/invoices")
async def billing_invoices(user: AgentUser = Depends(get_current_agent)):
    supabase = get_supabase()
    org = _owner_org(supabase, user.user_id)
    org_id = org["id"]
    customer_id = org.get("stripe_customer_id")

    if customer_id and settings.stripe_secret_key:
        try:
            st = _stripe()
            invoices = st.Invoice.list(customer=customer_id, limit=24)
            from datetime import datetime, timezone

            for inv in invoices.auto_paging_iter():
                line = inv.lines.data[0] if inv.lines.data else None
                amount = inv.amount_paid or inv.amount_due or (line.amount if line else 0)
                meta = {}
                if line and line.price and line.price.metadata:
                    meta = dict(line.price.metadata)
                plan = plan_from_metadata(meta) or plan_from_amount_cents(amount)

                def _ts(sec: int | None):
                    if not sec:
                        return None
                    return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()

                paid_at = None
                if inv.status_transitions and inv.status_transitions.paid_at:
                    paid_at = _ts(inv.status_transitions.paid_at)

                row = {
                    "organization_id": org_id,
                    "stripe_invoice_id": inv.id,
                    "amount_cents": amount,
                    "currency": inv.currency or "usd",
                    "status": inv.status or "unknown",
                    "plan": plan,
                    "invoice_pdf_url": inv.invoice_pdf,
                    "hosted_invoice_url": inv.hosted_invoice_url,
                    "period_start": _ts(inv.period_start),
                    "period_end": _ts(inv.period_end),
                    "paid_at": paid_at,
                }
                supabase.table("billing_invoices").upsert(
                    row, on_conflict="stripe_invoice_id"
                ).execute()
        except Exception as exc:
            logger.warning("Stripe invoice sync: %s", exc)

    result = (
        supabase.table("billing_invoices")
        .select(
            "id, stripe_invoice_id, amount_cents, currency, status, plan, "
            "invoice_pdf_url, hosted_invoice_url, period_start, period_end, paid_at, created_at"
        )
        .eq("organization_id", org_id)
        .order("created_at", desc=True)
        .limit(48)
        .execute()
    )
    return {"invoices": result.data or []}
