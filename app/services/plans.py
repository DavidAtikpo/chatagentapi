"""Subscription plan helpers."""

FREE_PLAN = "free"
STARTER_AND_UP = frozenset({"starter", "pro", "agency"})
PRO_PLANS = frozenset({"pro", "agency"})
ACTIVE_STATUSES = frozenset({"active", "trialing"})


def normalize_plan(subscription_plan: str | None) -> str:
    plan = (subscription_plan or FREE_PLAN).lower()
    if plan in STARTER_AND_UP:
        return plan
    if plan == FREE_PLAN:
        return FREE_PLAN
    return FREE_PLAN


def is_subscription_active(subscription_status: str | None) -> bool:
    return (subscription_status or "").lower() in ACTIVE_STATUSES


def has_starter_features(subscription_plan: str | None, subscription_status: str | None) -> bool:
    """Widget embed on external sites + custom logo."""
    if not is_subscription_active(subscription_status):
        return False
    return normalize_plan(subscription_plan) in STARTER_AND_UP


def has_pro_profile_features(subscription_plan: str | None, subscription_status: str | None) -> bool:
    """Emploi & Autre profile types."""
    if not is_subscription_active(subscription_status):
        return False
    return normalize_plan(subscription_plan) in PRO_PLANS


def has_pro_features(subscription_plan: str | None, subscription_status: str | None) -> bool:
    """WhatsApp / phone / email / CTA contact bar."""
    return has_pro_profile_features(subscription_plan, subscription_status)
