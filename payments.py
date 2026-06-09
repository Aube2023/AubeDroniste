"""Couche paiement AubePilot — Stripe Connect Express.

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

log = logging.getLogger("aubepilot.payments")


class StripeCountryUnsupportedError(Exception):
    """Le pays du pilote n'est pas (encore) supporte par Stripe Connect.

    Levee a la creation du compte connecte plutot que de creer un compte
    avec un pays par defaut errone (qui ne serait jamais payable).
    """


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

    country = _country_for_stripe(user.get("country") or "")
    if country is None:
        raise StripeCountryUnsupportedError(user.get("country") or "")
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
        refresh_url=f"{SITE_URL}/espace/pilote/stripe",
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
        refresh_url=f"{SITE_URL}/espace/pilote/stripe",
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

    IDEMPOTENCY : la cle `booking-{id}-checkout` garantit que si le client
    fait double-clic ou si le navigateur retry la requete, Stripe retourne
    LA MEME session au lieu d'en creer une 2eme. Anti double-debit.
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
                    "description": "Réservation de prestation drone via AubePilot",
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        metadata={"booking_id": str(booking_id)},
        success_url=success_url,
        cancel_url=cancel_url,
        idempotency_key=f"booking-{booking_id}-checkout",
    )
    log.info("checkout session %s pour booking=%s (%d cents %s)",
             session.id, booking_id, amount_cents, currency.upper())
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

    IDEMPOTENCY : `booking-{id}-release` empeche un double-versement au
    pilote si le client clique 2x sur 'Valider la mission' ou si l'auto-
    release J+7 tape en parallele d'une validation manuelle.
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
            idempotency_key=f"booking-{booking_id}-release",
        )
        log.info("transfer %s pour booking=%s (%d cents %s vers %s)",
                 tr.id, booking_id, amount_cents, currency.upper(), pilot_account_id)
        return tr.id
    except Exception as exc:
        log.error("release_to_pilot(booking=%s) -> %s", booking_id, exc)
        return None


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

def refund_payment(payment_intent_id: str, amount: Optional[float] = None,
                   currency: str = "EUR", reason: str = "") -> bool:
    """Rembourse tout ou partie. Si `amount` est None, refund total.

    IDEMPOTENCY : la cle inclut le payment_intent_id ET le montant pour
    autoriser refunds partiels successifs (50€ puis encore 30€) sans que
    Stripe les confonde. Un meme refund (meme PI, meme montant) reste
    idempotent.
    """
    s = _stripe()
    if s is None:
        return True
    try:
        amt_cents = int(round(float(amount) * 100)) if amount is not None else None
        kwargs = {
            "payment_intent": payment_intent_id,
            "metadata": {"reason": reason[:200]},
            "idempotency_key": f"refund-{payment_intent_id}-{amt_cents or 'full'}",
        }
        if amt_cents is not None:
            kwargs["amount"] = amt_cents
        s.Refund.create(**kwargs)
        log.info("refund %s (%s) pour PI=%s",
                 amt_cents if amt_cents else "full",
                 currency.upper(), payment_intent_id)
        return True
    except Exception as exc:
        log.error("refund_payment(%s) -> %s", payment_intent_id, exc)
        return False


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def parse_webhook(payload: bytes, signature: str):
    """Verifie la signature et retourne l'event Stripe (dict-like).

    En mode FAKE (pas de cle Stripe) : on accepte le JSON brut. Sinon, on
    EXIGE STRIPE_WEBHOOK_SECRET — sans secret, le webhook est REFUSE pour
    eviter qu'un attaquant POST des events falsifies marquant des
    bookings comme `funded`.
    """
    s = _stripe()
    if s is None:
        # Mode fake : on accepte le JSON brut (utile pour scripts/tests)
        import json
        try:
            return json.loads(payload)
        except Exception:
            return None
    if not STRIPE_WEBHOOK_SECRET:
        log.error(
            "REFUSE webhook : STRIPE_WEBHOOK_SECRET vide en mode Stripe live/test. "
            "Configure-le dans le dashboard Stripe puis dans /etc/aubepilot.env."
        )
        return None
    try:
        return s.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        log.error("webhook signature invalid: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Mapping pays -> code Stripe (rapide, pas exhaustif)
# ---------------------------------------------------------------------------

# Pays ou Stripe Connect (Express) peut creer un compte connecte payable.
# Liste alignee sur la doc Stripe « Supported countries ». Un pilote hors de
# cette liste est detecte (country_is_payable=False) au lieu de recevoir un
# compte casse — on lui propose alors un versement manuel.
_STRIPE_CONNECT_COUNTRIES = frozenset({
    "AU", "AT", "BE", "BG", "CA", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GI", "GR", "HK", "HU", "IE", "IT", "JP", "LV", "LI", "LT", "LU",
    "MT", "MX", "NL", "NZ", "NO", "PL", "PT", "RO", "SG", "SK", "SI", "ES",
    "SE", "CH", "TH", "AE", "GB", "US", "BR", "IN", "ID", "MY", "PH",
})

# Noms (FR + EN) -> code ISO2. Les codes ISO2 sont aussi acceptes directement.
_COUNTRY_CODES = {
    # Francophonie / Europe
    "france": "FR", "belgique": "BE", "belgium": "BE", "suisse": "CH",
    "switzerland": "CH", "luxembourg": "LU", "allemagne": "DE", "germany": "DE",
    "espagne": "ES", "spain": "ES", "italie": "IT", "italy": "IT",
    "portugal": "PT", "pays-bas": "NL", "netherlands": "NL", "irlande": "IE",
    "ireland": "IE", "autriche": "AT", "austria": "AT", "royaume-uni": "GB",
    "royaume uni": "GB", "united kingdom": "GB", "angleterre": "GB",
    "grande-bretagne": "GB", "danemark": "DK", "denmark": "DK", "suede": "SE",
    "sweden": "SE", "norvege": "NO", "norway": "NO", "finlande": "FI",
    "finland": "FI", "pologne": "PL", "poland": "PL", "grece": "GR",
    "greece": "GR", "tchequie": "CZ", "czechia": "CZ", "roumanie": "RO",
    "romania": "RO", "hongrie": "HU", "hungary": "HU",
    # Ameriques
    "canada": "CA", "quebec": "CA", "québec": "CA", "etats-unis": "US",
    "états-unis": "US", "usa": "US", "united states": "US", "mexique": "MX",
    "mexico": "MX", "bresil": "BR", "brésil": "BR", "brazil": "BR",
    # Asie / Oceanie / Moyen-Orient
    "japon": "JP", "japan": "JP", "singapour": "SG", "singapore": "SG",
    "hong kong": "HK", "inde": "IN", "india": "IN", "thailande": "TH",
    "thaïlande": "TH", "thailand": "TH", "malaisie": "MY", "malaysia": "MY",
    "indonesie": "ID", "indonesia": "ID", "philippines": "PH",
    "australie": "AU", "australia": "AU", "nouvelle-zelande": "NZ",
    "new zealand": "NZ", "emirats arabes unis": "AE",
    "émirats arabes unis": "AE", "uae": "AE", "dubai": "AE", "dubaï": "AE",
}


def _country_for_stripe(country: str) -> Optional[str]:
    """Resout un pays libre en code Stripe Connect (ISO2), ou None si non
    supporte. Ne RABAT PLUS sur 'FR' : un code errone creerait un compte
    connecte incoherent avec l'IBAN du pilote (donc jamais payable)."""
    raw = (country or "").strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.upper() in _STRIPE_CONNECT_COUNTRIES:
        return raw.upper()
    code = _COUNTRY_CODES.get(raw.lower())
    if code and code in _STRIPE_CONNECT_COUNTRIES:
        return code
    return None


def country_is_payable(country: str) -> bool:
    """True si un pilote dans ce pays peut etre paye via Stripe Connect."""
    return _country_for_stripe(country) is not None


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
