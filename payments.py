"""Couche paiement AubePilot — Stripe Connect Express.

Modele : escrow plateforme.
1. Client accepte une enchere -> booking en `pending_payment`
2. Client paie via Stripe Checkout -> webhook -> `funded`
3. Pilote livre, client valide -> Transfer Stripe vers connected account
   du pilote (prix - commission) ; la plateforme garde la commission (degressive)
4. Si dispute, on peut Refund total ou partiel via Stripe.

Mode FAKE (explicitement autorise, ou localhost) : on simule chaque appel et
on retourne des identifiants `acct_fake_*` / `pi_fake_*`. Une installation
publique sans cle est en mode DISABLED et ne simule jamais d'argent.

Le SDK officiel `stripe` est importe paresseusement : si la cle n'est
pas configuree, on n'essaie meme pas de l'importer (utile pour les tests
qui n'ont pas le pkg installe).
"""
import logging
import time
from typing import Any, Optional, Tuple

from config import (
    PLATFORM_FEE_PCT,
    SITE_URL,
    STRIPE_ACCOUNT_ID,
    STRIPE_CONNECT_ENABLED,
    STRIPE_CONNECT_WEBHOOK_SECRET,
    STRIPE_FAKE_MODE,
    STRIPE_LIVE_MODE,
    STRIPE_PAYMENTS_ENABLED,
    STRIPE_PUBLISHABLE_KEY,
    STRIPE_SECRET_KEY,
    STRIPE_WEBHOOK_SECRET,
)

log = logging.getLogger("aubepilot.payments")


class StripeCountryUnsupportedError(Exception):
    """Le pays du pilote n'est pas (encore) supporte par Stripe Connect.

    Levee a la creation du compte connecte plutot que de creer un compte
    avec un pays par defaut errone (qui ne serait jamais payable).
    """


class PaymentUnavailableError(RuntimeError):
    """Le prestataire de paiement n'est pas configure ou indisponible."""


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _stripe():
    """Retourne le module `stripe` configure, ou None hors mode reel/test."""
    if not STRIPE_SECRET_KEY:
        return None
    try:
        import stripe as s
    except ImportError:
        log.error("stripe SDK non installe; paiements indisponibles")
        return None
    s.api_key = STRIPE_SECRET_KEY
    return s


def _plain(obj) -> Any:
    """Copie en dict/list Python d'une reponse du SDK Stripe.

    Depuis stripe-python 15, `StripeObject` n'est plus un dict : plus de
    `.get()`, un attribut absent leve AttributeError. Tout ce qui sort d'un
    appel API et qui est lu ailleurs (webhook, page admin) passe donc par ici,
    quelle que soit la version du SDK : `to_dict()` existe dans toutes les
    versions et la recursion aplatit les objets imbriques des anciennes.
    """
    if obj is None:
        return None
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        obj = to_dict()
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def is_live() -> bool:
    return STRIPE_LIVE_MODE


def is_fake() -> bool:
    return STRIPE_FAKE_MODE


def is_available() -> bool:
    """Vrai si un paiement reel/test ou le simulateur local est utilisable."""
    return STRIPE_FAKE_MODE or (STRIPE_PAYMENTS_ENABLED and _stripe() is not None)


def banner_mode() -> str:
    if STRIPE_LIVE_MODE:
        return "LIVE"
    if STRIPE_SECRET_KEY:
        return "TEST"
    if STRIPE_FAKE_MODE:
        return "FAKE"
    return "DISABLED"


def _require_stripe_or_fake():
    """Retourne le SDK, None en fake, et refuse toute simulation implicite."""
    if STRIPE_FAKE_MODE:
        return None
    s = _stripe()
    if s is None:
        raise PaymentUnavailableError("Le paiement en ligne est indisponible.")
    return s


# ---------------------------------------------------------------------------
# Onboarding pilote (Stripe Connect Express)
# ---------------------------------------------------------------------------

def create_pilot_account(user: dict) -> Tuple[str, str]:
    """Cree un compte Connect Express et retourne (account_id, onboarding_url).

    En mode fake : retourne un id `acct_fake_<uid>` et une URL locale
    `/stripe/fake-onboarding/<uid>`.
    """
    s = _require_stripe_or_fake()
    return_url = f"{SITE_URL}/stripe/return"
    if s is None:
        return (f"acct_fake_{user['id']}", f"{SITE_URL}/stripe/fake-onboarding/{user['id']}")

    country = _country_for_stripe(user.get("country") or "")
    if country is None:
        raise StripeCountryUnsupportedError(user.get("country") or "")
    account = s.Account.create(**account_create_kwargs(user, country))
    link = s.AccountLink.create(
        account=account.id,
        refresh_url=f"{SITE_URL}/espace/pilote/stripe",
        return_url=return_url,
        type="account_onboarding",
    )
    return account.id, link.url


def account_create_kwargs(user: dict, country: str) -> dict:
    """Parametres du compte Connect Express d'un pilote.

    - Capacite `transfers` seule : notre modele est « la plateforme encaisse,
      puis vire au pilote » (separate charges and transfers). Demander
      `card_payments` imposerait au pilote un KYC marchand complet, inutile,
      et indisponible dans plusieurs pays.
    - Pas de `business_type` force : Stripe le demande a l'onboarding (une
      ecole ou une societe choisit « entreprise », un pilote « particulier »).
    - Pilote hors du pays de la plateforme : accord de service « recipient »
      (cross-border payouts), obligatoire pour recevoir des virements depuis
      une plateforme d'un autre pays. Le pays du compte plateforme est
      STRIPE_PLATFORM_COUNTRY (env), CA par defaut.
    """
    from config import STRIPE_PLATFORM_COUNTRY
    kwargs = {
        "type": "express",
        "country": country,
        "email": user["email"],
        "capabilities": {"transfers": {"requested": True}},
        # Pre-remplit le profil d'entreprise : evite au pilote l'etape
        # « fournir un site web » a l'onboarding (la plupart n'en ont pas). On
        # pointe vers son profil public AubePilot et on decrit l'activite. Le
        # nom du representant et le compte bancaire restent a sa charge.
        "business_profile": {
            "url": f"{SITE_URL}/pilotes/{user['id']}",
            "product_description": (
                "Prestations de pilote de drone : photo et vidéo aériennes, "
                "inspection technique, cartographie."
            ),
            "mcc": "7333",
        },
        "metadata": {"user_id": str(user["id"]), "username": user.get("username", "")},
    }
    if country.upper() != STRIPE_PLATFORM_COUNTRY:
        kwargs["tos_acceptance"] = {"service_agreement": "recipient"}
    return kwargs


def fresh_onboarding_link(account_id: str) -> str:
    """Recree un lien d'onboarding (les liens Stripe expirent vite)."""
    if STRIPE_FAKE_MODE and account_id.startswith("acct_fake_"):
        return f"{SITE_URL}/stripe/fake-onboarding/{account_id}"
    s = _require_stripe_or_fake()
    assert s is not None
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
    if STRIPE_FAKE_MODE and account_id.startswith("acct_fake_"):
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
    s = _require_stripe_or_fake()
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
        # Le paiement lui-meme porte le numero de reservation (lisible dans le
        # dashboard) et le groupe de transfert que le virement au pilote reprend.
        payment_intent_data={
            "description": f"AubePilot · réservation #{booking_id} · {mission_title[:60]}",
            "metadata": {"booking_id": str(booking_id)},
            "transfer_group": f"booking_{booking_id}",
        },
        success_url=success_url,
        cancel_url=cancel_url,
        idempotency_key=f"booking-{booking_id}-checkout",
    )
    log.info("checkout session %s pour booking=%s (%d cents %s)",
             session.id, booking_id, amount_cents, currency.upper())
    return session.id, session.url


def create_contribution_session(*, contribution_id: int, amount: float, currency: str,
                                campaign_title: str, pilot_name: str, return_path: str,
                                supporter_email: Optional[str] = None) -> Tuple[str, str]:
    """Checkout d'une contribution a une collecte. Meme modele que la mission :
    la plateforme encaisse, puis vire au pilote (moins sa part). Le webhook
    reconnait la contribution par metadata.contribution_id."""
    s = _require_stripe_or_fake()
    if s is None:
        return (f"cs_fake_contrib_{contribution_id}_{int(time.time())}",
                f"{SITE_URL}/stripe/fake-contribution/{contribution_id}")
    amount_cents = int(round(float(amount) * 100))
    kwargs = dict(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": currency.lower(),
                "product_data": {
                    "name": f"Soutien à {pilot_name[:60]} — {campaign_title[:70]}",
                    "description": "Contribution à une collecte de pilote via AubePilot",
                },
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        metadata={"contribution_id": str(contribution_id)},
        payment_intent_data={
            "description": f"AubePilot · soutien #{contribution_id} · {pilot_name[:60]}",
            "metadata": {"contribution_id": str(contribution_id)},
            "transfer_group": f"contribution_{contribution_id}",
        },
        success_url=f"{SITE_URL}{return_path}?soutien=merci",
        cancel_url=f"{SITE_URL}{return_path}?soutien=annule",
        idempotency_key=f"contribution-{contribution_id}-checkout",
    )
    if supporter_email:
        kwargs["customer_email"] = supporter_email
    session = s.checkout.Session.create(**kwargs)
    log.info("checkout contribution %s -> session %s (%d cents %s)",
             contribution_id, session.id, amount_cents, currency.upper())
    return session.id, session.url


def get_payment_intent_from_session(session_id: str) -> Optional[str]:
    """Recupere l'ID PaymentIntent associé a une Checkout Session."""
    if STRIPE_FAKE_MODE and session_id.startswith("cs_fake_"):
        return f"pi_fake_{session_id.split('_')[2]}" if "_" in session_id else None
    s = _stripe()
    if s is None:
        return None
    try:
        sess = s.checkout.Session.retrieve(session_id)
        return sess.payment_intent
    except Exception as exc:
        log.error("get_payment_intent_from_session(%s) -> %s", session_id, exc)
        return None


def expire_checkout_session(session_id: str) -> bool:
    """Ferme une session Checkout encore ouverte avant d'annuler un booking.

    Cela evite qu'un client paie via un ancien onglet apres que la reservation
    a ete annulee. Un echec est fail-closed : l'annulation reste en attente.
    """
    if not session_id:
        return True
    if STRIPE_FAKE_MODE and session_id.startswith("cs_fake_"):
        return True
    s = _stripe()
    if s is None:
        log.error("expire_checkout_session refuse: Stripe indisponible")
        return False
    try:
        session = s.checkout.Session.retrieve(session_id)
        status = getattr(session, "status", None)
        if status == "expired":
            return True
        if status == "complete":
            log.warning("session Checkout deja complete: %s", session_id)
            return False
        s.checkout.Session.expire(session_id)
        return True
    except Exception as exc:
        log.error("expire_checkout_session(%s) -> %s", session_id, exc)
        return False


# ---------------------------------------------------------------------------
# Liberation des fonds (Transfer)
# ---------------------------------------------------------------------------

def charge_for_transfer(payment_intent_id: Optional[str]) -> Optional[dict]:
    """Charge reglee derriere un PaymentIntent, pour y adosser un Transfer.

    Retourne {"charge", "currency", "exchange_rate"} : la devise est celle
    de la balance transaction (devise de reglement de la plateforme, ex. CAD
    pour un paiement en EUR) et exchange_rate le taux applique par Stripe
    (None si aucune conversion). None si introuvable ou en mode fake.
    """
    if not payment_intent_id or STRIPE_FAKE_MODE or payment_intent_id.startswith("pi_fake_"):
        return None
    s = _stripe()
    if s is None:
        return None
    try:
        pi = _plain(s.PaymentIntent.retrieve(
            payment_intent_id, expand=["latest_charge.balance_transaction"])) or {}
        charge = pi.get("latest_charge")
        if isinstance(charge, str):
            charge = _plain(s.Charge.retrieve(charge, expand=["balance_transaction"])) or {}
        if not charge or charge.get("status") != "succeeded":
            return None
        bt = charge.get("balance_transaction")
        if isinstance(bt, str):
            bt = _plain(s.BalanceTransaction.retrieve(bt)) or {}
        bt = bt or {}
        return {
            "charge": charge.get("id"),
            "currency": (bt.get("currency") or charge.get("currency") or "").lower(),
            "exchange_rate": bt.get("exchange_rate"),
        }
    except Exception as exc:
        log.warning("charge_for_transfer(%s) -> %s", payment_intent_id, exc)
        return None


def release_to_pilot(*, booking_id: int, pilot_amount: float, currency: str,
                     pilot_account_id: str, kind: str = "release",
                     source_payment_intent: Optional[str] = None) -> Optional[str]:
    """Transfert depuis le compte plateforme vers le compte pilote.

    Le `pilot_amount` est le brut DESTINE au pilote (i.e. agreed_price -
    platform_fee). Retourne l'ID du transfer Stripe ou un fake.

    SOURCE_TRANSACTION : un Transfer ordinaire ne puise que dans le solde
    *disponible* de la plateforme ; un paiement carte met 2 a 7 jours a le
    devenir et le versement automatique vide ce solde chaque jour. Adosse a
    la charge d'origine (`source_transaction`), le Transfer est accepte tout
    de suite et Stripe l'execute quand les fonds arrivent. Contraintes :
    devise = devise de reglement de la charge (on convertit au taux applique
    par Stripe si le devis etait dans une autre devise), montant <= charge.
    Sans PaymentIntent connu (ancien dossier), on tente le transfert simple.

    IDEMPOTENCY : `booking-{id}-{kind}` empeche un double-versement au
    pilote si le client clique 2x sur 'Valider la mission' ou si l'auto-
    release J+7 tape en parallele d'une validation manuelle. `kind` distingue
    la liberation normale ("release") du dedommagement d'annulation tardive
    ("cancel-compensation") et du soutien de collecte ("contribution") :
    montants differents, cles differentes.
    """
    if STRIPE_FAKE_MODE:
        return f"tr_fake_{booking_id}_{kind}_{int(time.time())}"
    s = _stripe()
    if s is None:
        log.error("release_to_pilot refuse: Stripe indisponible")
        return None
    amount_cents = int(round(float(pilot_amount) * 100))
    group = f"contribution_{booking_id}" if kind == "contribution" else f"booking_{booking_id}"
    kwargs = {
        "amount": amount_cents,
        "currency": currency.lower(),
        "destination": pilot_account_id,
        "transfer_group": group,
        "metadata": {"booking_id": str(booking_id), "kind": kind},
        "idempotency_key": f"booking-{booking_id}-{kind}",
    }
    src = charge_for_transfer(source_payment_intent)
    if src and src.get("charge"):
        kwargs["source_transaction"] = src["charge"]
        if src["currency"] and src["currency"] != currency.lower():
            rate = src.get("exchange_rate")
            if not rate:
                log.error("release_to_pilot(booking=%s) : devise %s reglee en %s sans taux, "
                          "transfert refuse", booking_id, currency, src["currency"])
                return None
            kwargs["amount"] = int(round(amount_cents * float(rate)))
            kwargs["currency"] = src["currency"]
            kwargs["metadata"]["quoted"] = f"{amount_cents} {currency.lower()}"
    else:
        log.warning("release_to_pilot(booking=%s) : sans source_transaction "
                    "(PaymentIntent %r) — depend du solde disponible", booking_id,
                    source_payment_intent)
    try:
        tr = s.Transfer.create(**kwargs)
        log.info("transfer %s pour booking=%s (%d %s vers %s, source=%s)",
                 tr.id, booking_id, kwargs["amount"], kwargs["currency"].upper(),
                 pilot_account_id, kwargs.get("source_transaction"))
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
    if STRIPE_FAKE_MODE:
        return True
    s = _stripe()
    if s is None:
        log.error("refund_payment refuse: Stripe indisponible")
        return False
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

def _setting(key: str) -> str:
    """Reglage pose par l'app en base (cf. ensure_stripe_configuration) ;
    vide hors contexte applicatif ou si absent."""
    try:
        import db
        return (db.get_setting(key) or "").strip()
    except Exception:
        return ""


def webhook_secret() -> str:
    """Secret du webhook de paiements : l'env d'abord, sinon celui du
    webhook que l'app a cree elle-meme."""
    return STRIPE_WEBHOOK_SECRET or _setting("stripe_webhook_secret")


def connect_webhook_secret() -> str:
    """Secret du webhook Connect (comptes pilotes) : env, sinon base."""
    return STRIPE_CONNECT_WEBHOOK_SECRET or _setting("stripe_connect_webhook_secret")


def parse_webhook(payload: bytes, signature: str, secret: Optional[str] = None):
    """Verifie la signature et retourne l'event Stripe sous forme de dict.

    `secret` : STRIPE_WEBHOOK_SECRET par defaut (evenements de la plateforme),
    ou STRIPE_CONNECT_WEBHOOK_SECRET pour /stripe/webhook/connect (evenements
    des comptes pilotes). En mode FAKE (pas de cle Stripe) : on accepte le
    JSON brut. Sinon, on EXIGE le secret — sans secret, le webhook est REFUSE
    pour eviter qu'un attaquant POST des events falsifies marquant des
    bookings comme `funded`.
    """
    if secret is None:
        secret = webhook_secret()
    if STRIPE_FAKE_MODE:
        # Mode fake : on accepte le JSON brut (utile pour scripts/tests)
        import json
        try:
            return json.loads(payload)
        except Exception:
            return None
    s = _stripe()
    if s is None:
        log.error("REFUSE webhook : Stripe n'est pas configure ou le SDK manque")
        return None
    if not secret:
        log.error(
            "REFUSE webhook : secret vide en mode Stripe live/test. "
            "Configure-le dans le dashboard Stripe puis dans /etc/aubepilot.env."
        )
        return None
    try:
        event = s.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        log.error("webhook signature invalid: %s", exc)
        return None
    # Toujours un dict pour l'appelant (cf. _plain), comme en mode fake.
    return _plain(event)


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

def split_amounts(total: float, fee_pct: Optional[float] = None) -> dict:
    """Calcule la repartition selon le taux de commission (defaut : taux de
    base PLATFORM_FEE_PCT ; la commission reelle d'un booking est degressive,
    cf. services.platform_fee_pct_for).

    Retourne {total, platform, pilot}. Tous en devise locale (pas en cents).
    """
    pct = PLATFORM_FEE_PCT if fee_pct is None else float(fee_pct)
    fee = round(float(total) * pct / 100.0, 2)
    return {
        "total":    round(float(total), 2),
        "platform": fee,
        "pilot":    round(float(total) - fee, 2),
    }


# ---------------------------------------------------------------------------
# Diagnostic (page admin) : ou en est le compte, sans jamais montrer une cle
# ---------------------------------------------------------------------------

# Webhook plateforme (paiements) et webhook Connect (comptes pilotes) : deux
# endpoints Stripe distincts, chacun avec son secret.
WEBHOOK_EVENTS_EXPECTED = ("checkout.session.completed", "charge.refunded", "charge.dispute.created")
CONNECT_WEBHOOK_EVENTS_EXPECTED = ("account.updated",)


def diagnostics() -> dict:
    """Etat du raccordement Stripe tel que l'API le voit, en lecture seule :
    mode, compte plateforme, Connect (inscrit ou non), comptes connectes,
    webhooks. Chaque appel est protege : une erreur devient un message, pas
    une page blanche. Aucune cle n'apparait dans le resultat."""
    out = {
        "mode": banner_mode(),
        "connect_flag": STRIPE_CONNECT_ENABLED,
        "webhook_secret": bool(webhook_secret()),
        "publishable": bool(STRIPE_PUBLISHABLE_KEY),
        "webhook_url": f"{SITE_URL}/stripe/webhook",
        "connect_webhook_url": f"{SITE_URL}/stripe/webhook/connect",
        "connect_webhook_secret": bool(connect_webhook_secret()),
        "expected_account": STRIPE_ACCOUNT_ID,
        "account_mismatch": False,
        "payout_schedule": None, "balance": None,
        "account": None, "connect": None, "connected": None, "webhooks": [], "errors": [],
    }
    s = _stripe()
    if s is None:
        return out
    try:
        acc = _plain(s.Account.retrieve()) or {}
        settings = acc.get("settings") or {}
        out["account"] = {
            "id": acc.get("id"), "country": acc.get("country"),
            "currency": acc.get("default_currency"),
            "name": (settings.get("dashboard") or {}).get("display_name")
                    or (acc.get("business_profile") or {}).get("name"),
            "charges_enabled": bool(acc.get("charges_enabled")),
            "payouts_enabled": bool(acc.get("payouts_enabled")),
            "details_submitted": bool(acc.get("details_submitted")),
        }
        out["account_mismatch"] = bool(STRIPE_ACCOUNT_ID and acc.get("id") != STRIPE_ACCOUNT_ID)
        sched = ((settings.get("payouts") or {}).get("schedule") or {})
        out["payout_schedule"] = {
            "interval": sched.get("interval"), "delay_days": sched.get("delay_days"),
            "manual": sched.get("interval") == "manual",
        }
    except Exception as exc:
        out["errors"].append(f"compte plateforme : {exc}")
    try:
        out["balance"] = balance()
    except Exception as exc:
        out["errors"].append(f"solde : {exc}")
    try:
        accounts = s.Account.list(limit=100)
        out["connected"] = []
        for a in accounts.auto_paging_iter():
            a = _plain(a) or {}
            out["connected"].append({
                "id": a.get("id"), "type": a.get("type"), "country": a.get("country"),
                "charges_enabled": bool(a.get("charges_enabled")),
                "payouts_enabled": bool(a.get("payouts_enabled")),
                "details_submitted": bool(a.get("details_submitted")),
            })
        # Sans Connect, Stripe repond une liste vide plutot qu'une erreur : on
        # ne conclut « oui » qu'avec au moins un compte connecte ; sinon « ? ».
        out["connect"] = True if out["connected"] else None
    except Exception as exc:
        msg = str(exc)
        out["connect"] = False if "Connect" in msg else None
        out["errors"].append(f"comptes connectés : {msg}")
    try:
        hooks = _plain(s.WebhookEndpoint.list(limit=20)) or {}
        for w in hooks.get("data") or []:
            events = list(w.get("enabled_events") or [])
            url = w.get("url")
            expected = ()
            if url == out["webhook_url"]:
                expected = WEBHOOK_EVENTS_EXPECTED
            elif url == out["connect_webhook_url"]:
                expected = CONNECT_WEBHOOK_EVENTS_EXPECTED
            out["webhooks"].append({
                "id": w.get("id"), "url": url, "status": w.get("status"), "events": events,
                "ours": bool(expected),
                "missing": [e for e in expected if e not in events and "*" not in events],
            })
        out["connect_webhook_present"] = any(
            w["url"] == out["connect_webhook_url"] for w in out["webhooks"])
    except Exception as exc:
        out["errors"].append(f"webhooks : {exc}")
    return out


# ---------------------------------------------------------------------------
# Solde de la plateforme et retrait vers sa banque
# ---------------------------------------------------------------------------

def balance() -> dict:
    """Solde Stripe de la plateforme, par devise (majuscules), en unites :
    {"CAD": {"available": 123.45, "pending": 67.0}}. Vide en mode fake."""
    s = _stripe()
    if s is None:
        return {}
    b = _plain(s.Balance.retrieve()) or {}
    out: dict = {}
    for key in ("available", "pending"):
        for row in b.get(key) or []:
            cur = (row.get("currency") or "").upper()
            out.setdefault(cur, {"available": 0.0, "pending": 0.0})
            out[cur][key] = round(int(row.get("amount") or 0) / 100.0, 2)
    return out


def ensure_stripe_configuration() -> dict:
    """Met le compte Stripe en conformite avec ce que l'app attend, sans
    intervention dans le dashboard (cron de nuit, idempotent) :
      - calendrier de versement de la plateforme en MANUEL, sinon Stripe vide
        le solde vers la banque, sequestre compris (l'app retire ensuite
        elle-meme la commission, cf. services.auto_platform_payout) ;
      - notre webhook de paiements ecoute tous les evenements attendus
        (en ajouter ne change pas son secret).
    Retourne {"payout_schedule": "manual"|"deja"|"refus: ...",
              "webhook": "complete"|"deja"|"absent"|"refus: ..."}."""
    out: dict = {"payout_schedule": None, "webhook": None, "connect_webhook": None}
    s = _stripe()
    if s is None or STRIPE_FAKE_MODE:
        return out
    try:
        acc = _plain(s.Account.retrieve()) or {}
        sched = (((acc.get("settings") or {}).get("payouts") or {}).get("schedule") or {})
        if sched.get("interval") == "manual":
            out["payout_schedule"] = "deja"
        else:
            # Stripe refuse cette modification par API sur le compte de la
            # plateforme (« You cannot use this method on your own account »,
            # verifie le 2026-09-15) : elle ne se fait que dans le dashboard.
            # On tente quand meme (si Stripe l'ouvre un jour) et on explique.
            s.Account.modify(acc["id"], settings={"payouts": {"schedule": {"interval": "manual"}}})
            log.info("calendrier de versement Stripe passe de « %s » a manuel", sched.get("interval"))
            out["payout_schedule"] = "manual"
    except Exception as exc:
        log.warning("calendrier de versement Stripe toujours automatique (%s). A regler une fois "
                    "dans le dashboard : https://dashboard.stripe.com/settings/payouts -> "
                    "calendrier -> manuel ; l'app retire ensuite la commission elle-meme.", exc)
        out["payout_schedule"] = f"refus: {exc}"
    try:
        hooks = (_plain(s.WebhookEndpoint.list(limit=20)) or {}).get("data") or []
    except Exception as exc:
        log.error("webhooks Stripe illisibles : %s", exc)
        out["webhook"] = out["connect_webhook"] = f"refus: {exc}"
        return out
    out["webhook"] = _ensure_webhook(
        s, hooks, url=f"{SITE_URL}/stripe/webhook", events=WEBHOOK_EVENTS_EXPECTED,
        known_secret=webhook_secret(), setting_key="stripe_webhook_secret", connect=False)
    out["connect_webhook"] = _ensure_webhook(
        s, hooks, url=f"{SITE_URL}/stripe/webhook/connect", events=CONNECT_WEBHOOK_EVENTS_EXPECTED,
        known_secret=connect_webhook_secret(), setting_key="stripe_connect_webhook_secret", connect=True)
    return out


def _ensure_webhook(s, hooks: list, *, url: str, events: tuple, known_secret: str,
                    setting_key: str, connect: bool) -> str:
    """Un webhook Stripe vers `url`, avec tous les `events`, dont on connait
    le secret. Complete les evenements manquants (le secret ne change pas).
    Absent, ou present sans qu'on ait son secret (cree autrement, secret
    perdu) : (re)cree par l'app, secret garde en base (app_settings). Une
    entree d'env prime toujours sur la base. Retourne un mot d'etat."""
    ours = [w for w in hooks if w.get("url") == url]
    try:
        if ours and known_secret:
            w = ours[0]
            have = list(w.get("enabled_events") or [])
            missing = [e for e in events if e not in have and "*" not in have]
            if not missing:
                return "deja"
            s.WebhookEndpoint.modify(w["id"], enabled_events=have + missing)
            log.info("webhook %s : evenements ajoutes %s", w["id"], missing)
            return "complete"
        for w in ours:
            # Present mais secret inconnu : inutilisable, on le remplace.
            s.WebhookEndpoint.delete(w["id"])
            log.info("webhook %s (%s) sans secret connu : supprime", w["id"], url)
        kwargs = {"url": url, "enabled_events": list(events),
                  "description": "AubePilot (créé par l'app)" + (" · comptes pilotes" if connect else "")}
        if connect:
            kwargs["connect"] = True
        created = _plain(s.WebhookEndpoint.create(**kwargs)) or {}
        secret = created.get("secret") or ""
        if not secret.startswith("whsec_"):
            log.error("webhook %s cree sans secret exploitable", created.get("id"))
            return "refus: secret absent"
        import db
        db.set_setting(setting_key, secret)
        log.info("webhook %s cree vers %s (%s), secret garde en base", created.get("id"), url,
                 ", ".join(events))
        return "cree"
    except Exception as exc:
        log.error("webhook %s : %s", url, exc)
        return f"refus: {exc}"


def create_platform_payout(amount: float, currency: str, note: str = "") -> Optional[str]:
    """Retrait de la plateforme vers sa banque (calendrier de versement
    manuel). Retourne l'identifiant du Payout, None en cas de refus."""
    s = _stripe()
    if s is None or amount <= 0:
        return None
    try:
        po = s.Payout.create(
            amount=int(round(float(amount) * 100)), currency=currency.lower(),
            description=(note or "Commission AubePilot")[:100],
            metadata={"source": "admin_stripe"},
        )
        log.info("payout %s : %.2f %s", po.id, amount, currency.upper())
        return po.id
    except Exception as exc:
        log.error("create_platform_payout(%.2f %s) -> %s", amount, currency, exc)
        return None
