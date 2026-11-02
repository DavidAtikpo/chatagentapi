import stripe
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services.billing_plans import plan_from_amount_cents, plan_from_metadata
from app.services.supabase_client import get_supabase

router = APIRouter()


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    if not settings.stripe_secret_key or not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Stripe not configured")

    stripe.api_key = settings.stripe_secret_key
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    supabase = get_supabase()

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        org_id = (session.get("metadata") or {}).get("organization_id")
        plan = plan_from_metadata(session.get("metadata"))
        customer_id = session.get("customer")
        sub_id = session.get("subscription")
        if org_id and plan and customer_id:
            supabase.table("organizations").update(
                {
                    "subscription_plan": plan,
                    "subscription_status": "active",
                    "stripe_customer_id": customer_id,
                    "stripe_subscription_id": sub_id,
                }
            ).eq("id", org_id).execute()

    if event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        plan = plan_from_metadata(sub.get("metadata"))
        if not plan and sub["items"]["data"]:
            price = sub["items"]["data"][0].get("price") or {}
            plan = plan_from_metadata(price.get("metadata"))
            if not plan:
                plan = plan_from_amount_cents(price.get("unit_amount") or 0)
        if not plan:
            plan = "starter"

        supabase.table("organizations").update(
            {
                "subscription_status": status,
                "subscription_plan": plan,
                "stripe_subscription_id": sub["id"],
            }
        ).eq("stripe_customer_id", customer_id).execute()

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        supabase.table("organizations").update(
            {
                "subscription_status": "canceled",
                "subscription_plan": "free",
                "stripe_subscription_id": None,
            }
        ).eq("stripe_customer_id", sub["customer"]).execute()

    return {"received": True}
