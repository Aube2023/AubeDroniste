"""Couche paiement AubeDroniste — Stripe Connect Express.

Modele : escrow plateforme.
1. Client accepte une enchere -> booking en `pending_payment`
2. Client paie via Stripe Checkout -> webhook -> `funded`
3. Pilote livre, client valide -> Transfer Stripe vers connected account
   du pilote (70 %) ; la plateforme garde 30 % (la commission)
4. Si dispute, on peut Refund total ou partiel via Stripe.

Mode FAKE (sans cle Stripe) : on simule chaque appel et on retourne des
identifiants `acct_fake_*` / `pi_fake_*`. Cela permet de demolir le flow
en demo sans avoir besoin de configurer un vrai compte Stripe.

Le SDK officiel `stripe` est importe paresseusement : si la cle n'est
pas configuree, on n'essaie meme pas de l'importer (utile pour les tests
qui n'ont pas le pkg installe).
"""
import logging
import time
from typing import Optional, Tuple

from config import (
    PLATFORM_FEE_PCT,
    SITE_URL,
    STRIPE_FAKE_MODE,
    STRIPE_LIVE_MODE,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)

log = logging.getLogger("aubedroniste.payments")


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _stripe():
    """Retourne le module `stripe` configure, ou None en mode fake."""
    if STRIPE_FAKE_MODE:
        return None
    try:
        import stripe as s
    except ImportError:
        log.warning("stripe SDK non installe; passage en mode fake")
        return None
    s.api_key = STRIPE_SECRET_KEY
    return s


def is_live() -> bool:
    return STRIPE_LIVE_MODE


def is_fake() -> bool:
    return STRIPE_FAKE_MODE or _stripe() is None


def banner_mode() -> str:
    if STRIPE_LIVE_MODE:
        return "LIVE"
    if STRIPE_SECRET_KEY:
        return "TEST"
    return "FAKE"


# ---------------------------------------------------------------------------
# Onboarding pilote (Stripe Connect Express)
# ---------------------------------------------------------------------------

def create_pilot_account(user: dict) -> Tuple[str, str]:
    """Cree un compte Connect Express et retourne (account_id, onboarding_url).

    En mode fake : retourne un id `acct_fake_<uid>` et une URL locale
    `/stripe/fake-onboarding/<uid>`.
    """
    s = _stripe()
    return_url = f"{SITE_URL}/stripe/return"
    if s is None:
        return (f"acct_fake_{user['id']}", f"{SITE_URL}/stripe/fake-onboarding/{user['id']}")

    country = _country_for_stripe(user.get("country") or "FR")
    account = s.Account.create(
        type="express",
        country=country,
        email=user["email"],
        capabilities={
            "transfers": {"requested": True},
            "card_payments": {"requested": True},
        },
        business_type="individual",
        metadata={"user_id": str(user["id"]), "username": user.get("username", "")},
    )
    link = s.AccountLink.create(
        account=account.id,
        refresh_url=f"{SITE_URL}/espace/droniste/stripe",
        return_url=return_url,
        type="account_onboarding",
    )
    return account.id, link.url


def fresh_onboarding_link(account_id: str) -> str:
    """Recree un lien d'onboarding (les liens Stripe expirent vite)."""
    s = _stripe()
    if s is None or account_id.startswith("acct_fake_"):
        return f"{SITE_URL}/stripe/fake-onboarding/{account_id}"
    link = s.AccountLink.create(
        account=account_id,
        refresh_url=f"{SITE_URL}/espace/droniste/stripe",
        return_url=f"{SITE_URL}/stripe/return",
        type="account_onboarding",
    )
    return link.url


def get_pilot_status(account_id: Optional[str]) -> dict:
    """Retourne {charges_enabled, payouts_enabled, details_submitted}."""
    if not account_id:
        return {"charges_enabled": False, "payouts_enabled": False,
                "details_submitted": False}
    if account_id.startswith("acct_fake_"):
        # En fake mode, on dit que le pilote est OK des qu'il a un compte.
        return {"charges_enabled": True, "payouts_enabled": True,
                "details_submitted": True}
    s = _stripe()
    if s is None:
        return {"charges_enabled": False, "payouts_enabled": False,
                "details_submitted": False}
    try:
        acc = s.Account.retrieve(account_id)
        return {
            "charges_enabled":   bool(acc.charges_enabled),
            "payouts_enabled":   bool(acc.payouts_enabled),
            "details_submitted": bool(acc.details_submitted),
        }
    except Exception as exc:
        log.error("get_pilot_status(%s) -> %s", account_id, exc)
        return {"charges_enabled": False, "payouts_enabled": False,
                "details_submitted": False}


# ---------------------------------------------------------------------------
# Paiement (escrow)
# ---------------------------------------------------------------------------

def create_checkout_session(*, booking_id: int, amount: float, currency: str,
                            mission_title: str, client_email: str) -> Tuple[str, str]:
    """Cree une Checkout Session Stripe (paiement carte hosted).

    Retourne (session_id, redirect_url). En mode fake, retourne une URL locale
    qui simule le paiement.
    """
    s = _stripe()
    success_url = f"{SITE_URL}/reservations/{booking_id}?payment=success"
    cancel_url  = f"{SITE_URL}/reservations/{booking_id}?payment=cancel"
    if s is None:
        return (f"cs_fake_{booking_id}_{int(time.time())}",
                f"{SITE_URL}/stripe/fake-checkout/{booking_id}")

    amount_cents = int(round(float(amount) * 100))
    session = s.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=client_email,
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "product_data": {
                    "name": f"Mission #{booking_id} — {mission_title[:80]}",
                    "description": "Réservation de prestation drone via AubeDroniste",
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        metadata={"booking_id": str(booking_id)},
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.id, session.url


def get_payment_intent_from_session(session_id: str) -> Optional[str]:
    """Recupere l'ID PaymentIntent associé a une Checkout Session."""
    s = _stripe()
    if s is None or session_id.startswith("cs_fake_"):
        return f"pi_fake_{session_id.split('_')[2]}" if "_" in session_id else None
    try:
        sess = s.checkout.Session.retrieve(session_id)
        return sess.payment_intent
    except Exception as exc:
        log.error("get_payment_intent_from_session(%s) -> %s", session_id, exc)
        return None


# ---------------------------------------------------------------------------
# Liberation des fonds (Transfer)
# ---------------------------------------------------------------------------

def release_to_pilot(*, booking_id: int, pilot_amount: float, currency: str,
                     pilot_account_id: str) -> Optional[str]:
    """Transfert depuis le compte plateforme vers le compte pilote.

    Le `pilot_amount` est le brut DESTINE au pilote (i.e. agreed_price -
    platform_fee). Retourne l'ID du transfer Stripe ou un fake.
    """
    s = _stripe()
    if s is None:
        return f"tr_fake_{booking_id}_{int(time.time())}"
    amount_cents = int(round(float(pilot_amount) * 100))
    try:
        tr = s.Transfer.create(
            amount=amount_cents,
            currency=currency.lower(),
            destination=pilot_account_id,
            transfer_group=f"booking_{booking_id}",
            metadata={"booking_id": str(booking_id)},
        )
        return tr.id
    except Exception as exc:
        log.error("release_to_pilot(booking=%s) -> %s", booking_id, exc)
        return None


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

def refund_payment(payment_intent_id: str, amount: Optional[float] = None,
                   currency: str = "EUR", reason: str = "") -> bool:
    """Rembourse tout ou partie. Si `amount` est None, refund total."""
    s = _stripe()
    if s is None:
        return True
    try:
        kwargs = {"payment_intent": payment_intent_id, "metadata": {"reason": reason[:200]}}
        if amount is not None:
            kwargs["amount"] = int(round(float(amount) * 100))
        s.Refund.create(**kwargs)
        return True
    except Exception as exc:
        log.error("refund_payment(%s) -> %s", payment_intent_id, exc)
        return False


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def parse_webhook(payload: bytes, signature: str):
    """Verifie la signature et retourne l'event Stripe (dict-like)."""
    s = _stripe()
    if s is None:
        # En mode fake on accepte tel quel un JSON brut (utile pour scripts/)
        import json
        try:
            return json.loads(payload)
        except Exception:
            return None
    if not STRIPE_WEBHOOK_SECRET:
        log.warning("STRIPE_WEBHOOK_SECRET vide ; on n'authentifie pas le webhook")
        try:
            return s.Event.construct_from(__import__("json").loads(payload), s.api_key)
        except Exception:
            return None
    try:
        return s.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        log.error("webhook signature invalid: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Mapping pays -> code Stripe (rapide, pas exhaustif)
# ---------------------------------------------------------------------------

_COUNTRY_CODES = {
    "France": "FR", "Belgique": "BE", "Suisse": "CH", "Luxembourg": "LU",
    "Canada": "CA", "Quebec": "CA",
    "Maroc": "MA", "Algerie": "DZ", "Tunisie": "TN",
    "Senegal": "SN", "Cote d'Ivoire": "CI", "Mali": "ML", "Burkina Faso": "BF",
    "Niger": "NE", "Cameroun": "CM", "Madagascar": "MG", "Liban": "LB",
}


def _country_for_stripe(country: str) -> str:
    return _COUNTRY_CODES.get(country, "FR")


# ---------------------------------------------------------------------------
# Calcul commission
# ---------------------------------------------------------------------------

def split_amounts(total: float) -> dict:
    """Calcule la repartition selon PLATFORM_FEE_PCT.

    Retourne {total, platform, pilot}. Tous en devise locale (pas en cents).
    """
    fee = round(float(total) * PLATFORM_FEE_PCT / 100.0, 2)
    return {
        "total":    round(float(total), 2),
        "platform": fee,
        "pilot":    round(float(total) - fee, 2),
    }
