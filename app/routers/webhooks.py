import stripe
from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services.supabase_client import get_supabase

router = APIRouter()

PLAN_MAP = {
    "price_starter": "starter",
    "price_pro": "pro",
    "price_agency": "agency",
}


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

    if event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        price_id = sub["items"]["data"][0]["price"]["id"] if sub["items"]["data"] else None
        plan = PLAN_MAP.get(price_id, "starter")

        supabase.table("organizations").update(
            {
                "subscription_status": status,
                "subscription_plan": plan,
                "stripe_subscription_id": sub["id"],
            }
        ).eq("stripe_customer_id", customer_id).execute()

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        supabase.table("organizations").update({"subscription_status": "canceled"}).eq(
            "stripe_customer_id", sub["customer"]
        ).execute()

    return {"received": True}
