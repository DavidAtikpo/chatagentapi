"""Subscription plan helpers."""

PRO_PLANS = frozenset({"pro", "agency"})
ACTIVE_STATUSES = frozenset({"active", "trialing"})


def has_pro_features(subscription_plan: str | None, subscription_status: str | None) -> bool:
    """WhatsApp / phone / email / CTA contact bar — Pro or Agency, active subscription."""
    plan = (subscription_plan or "starter").lower()
    status = (subscription_status or "").lower()
    return plan in PRO_PLANS and status in ACTIVE_STATUSES
