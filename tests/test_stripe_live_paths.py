"""Chemins d'argent en mode reel (SDK simule par des bouchons) : ce qui
change par rapport au mode fake et qui ne se voit qu'en LIVE.

- Le virement au pilote est adosse a la charge d'origine (source_transaction),
  sinon il depend du solde disponible, vide chaque jour par le versement
  automatique de la plateforme.
- Devis dans une autre devise que la devise de reglement : montant converti
  au taux applique par Stripe, dans la devise de reglement.
- Deux webhooks (plateforme, Connect), chacun avec son secret.
- Resynchronisation du statut d'un compte pilote hors webhook.
"""
import pytest


class _Obj:
    """Objet du SDK minimal : attributs + to_dict(), comme stripe-python 15."""
    def __init__(self, **d):
        self._d = d
        for k, v in d.items():
            setattr(self, k, v)

    def to_dict(self):
        return self._d


def _sdk_stub(calls, *, settled_currency="cad", exchange_rate=None, charge_status="succeeded"):
    class PaymentIntent:
        @staticmethod
        def retrieve(pi, expand=None):
            return _Obj(id=pi, latest_charge={
                "id": "ch_1", "status": charge_status, "currency": "cad",
                "balance_transaction": {"currency": settled_currency, "exchange_rate": exchange_rate},
            })

    class Transfer:
        @staticmethod
        def create(**kw):
            calls.append(kw)
            return _Obj(id="tr_1")

    class _SDK:
        pass
    _SDK.PaymentIntent = PaymentIntent
    _SDK.Transfer = Transfer
    return _SDK


def _live(monkeypatch, payments):
    monkeypatch.setattr(payments, "STRIPE_FAKE_MODE", False)
    monkeypatch.setattr(payments, "STRIPE_SECRET_KEY", "sk_test_x")


def test_virement_adosse_a_la_charge(monkeypatch):
    import payments
    _live(monkeypatch, payments)
    calls = []
    monkeypatch.setattr(payments, "_stripe", lambda: _sdk_stub(calls))
    tid = payments.release_to_pilot(booking_id=7, pilot_amount=8.0, currency="CAD",
                                    pilot_account_id="acct_p", source_payment_intent="pi_7")
    assert tid == "tr_1"
    kw = calls[0]
    assert kw["source_transaction"] == "ch_1"
    assert kw["amount"] == 800 and kw["currency"] == "cad"
    assert kw["destination"] == "acct_p" and kw["transfer_group"] == "booking_7"
    assert kw["idempotency_key"] == "booking-7-release"


def test_virement_converti_dans_la_devise_de_reglement(monkeypatch):
    """Devis 100 EUR sur une plateforme qui regle en CAD : le Transfer avec
    source_transaction doit etre en CAD, au taux de la balance transaction."""
    import payments
    _live(monkeypatch, payments)
    calls = []
    monkeypatch.setattr(payments, "_stripe",
                        lambda: _sdk_stub(calls, settled_currency="cad", exchange_rate=1.48))
    payments.release_to_pilot(booking_id=8, pilot_amount=80.0, currency="EUR",
                              pilot_account_id="acct_p", source_payment_intent="pi_8")
    kw = calls[0]
    assert kw["currency"] == "cad" and kw["amount"] == 11840
    assert kw["metadata"]["quoted"] == "8000 eur"
    # Sans taux connu, on refuse plutot que de virer un montant faux
    calls.clear()
    monkeypatch.setattr(payments, "_stripe",
                        lambda: _sdk_stub(calls, settled_currency="cad", exchange_rate=None))
    assert payments.release_to_pilot(booking_id=8, pilot_amount=80.0, currency="EUR",
                                     pilot_account_id="acct_p", source_payment_intent="pi_8") is None
    assert calls == []


def test_virement_sans_paiement_connu_reste_possible(monkeypatch):
    """Ancien dossier sans PaymentIntent : transfert simple, sans source."""
    import payments
    _live(monkeypatch, payments)
    calls = []
    monkeypatch.setattr(payments, "_stripe", lambda: _sdk_stub(calls, charge_status="pending"))
    payments.release_to_pilot(booking_id=9, pilot_amount=8.0, currency="CAD",
                              pilot_account_id="acct_p", source_payment_intent=None)
    assert "source_transaction" not in calls[0]
    calls.clear()
    # Charge pas encore reussie : pas de source non plus (Stripe la refuserait)
    payments.release_to_pilot(booking_id=9, pilot_amount=8.0, currency="CAD",
                              pilot_account_id="acct_p", source_payment_intent="pi_9")
    assert "source_transaction" not in calls[0]


def test_contribution_de_collecte_passe_le_paiement_d_origine(monkeypatch):
    """transfer_contribution transmet le PaymentIntent de la contribution."""
    import payments
    seen = {}

    def faux_release(**kw):
        seen.update(kw)
        return "tr_c"
    monkeypatch.setattr(payments, "release_to_pilot", faux_release)
    import services
    monkeypatch.setattr(services, "get_contribution", lambda cid: {
        "id": cid, "status": "paid", "campaign_id": 1, "amount": 50.0, "platform_fee": 4.0,
        "currency": "CAD", "stripe_payment_intent_id": "pi_contrib"})
    monkeypatch.setattr(services, "get_campaign", lambda cid: {"id": cid, "pilot_user_id": 42})
    monkeypatch.setattr(services, "get_pilot_stripe_account", lambda uid: "acct_p")
    monkeypatch.setattr(services.db, "execute", lambda *a, **k: None)
    assert services.transfer_contribution(5) == "tr_c"
    assert seen["source_payment_intent"] == "pi_contrib" and seen["kind"] == "contribution"
    assert seen["pilot_amount"] == 46.0 and seen["booking_id"] == 5


def test_webhook_connect_a_son_propre_secret(client, monkeypatch):
    import config
    import payments
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    secrets = []

    def faux_parse(payload, sig, secret=None):
        secrets.append(secret)
        return {"type": "ping", "data": {"object": {}}}
    monkeypatch.setattr(payments, "parse_webhook", faux_parse)

    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_plateforme")
    monkeypatch.setattr(config, "STRIPE_CONNECT_WEBHOOK_SECRET", "")
    assert client.post("/stripe/webhook", data=b"{}").status_code == 200
    assert client.post("/stripe/webhook/connect", data=b"{}").status_code == 404
    monkeypatch.setattr(config, "STRIPE_CONNECT_WEBHOOK_SECRET", "whsec_connect")
    assert client.post("/stripe/webhook/connect", data=b"{}").status_code == 200
    assert secrets == ["whsec_plateforme", "whsec_connect"]


def test_parse_webhook_refuse_sans_secret(monkeypatch):
    import payments
    _live(monkeypatch, payments)
    monkeypatch.setattr(payments, "_stripe", lambda: object())
    assert payments.parse_webhook(b"{}", "sig", secret="") is None


def test_resync_statut_pilote(app_ctx, make_user, monkeypatch):
    import payments
    import services
    u = make_user("resync_pilot", role="pilot")
    services.set_pilot_stripe_account(u["id"], "acct_live_x", charges_enabled=True,
                                      payouts_enabled=False)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    monkeypatch.setattr(payments, "get_pilot_status", lambda acc: {
        "charges_enabled": True, "payouts_enabled": True, "details_submitted": True})
    assert services.sync_pending_pilot_stripe_status() >= 1
    prof = services.get_pilot_profile(u["id"])
    assert prof["stripe_payouts_enabled"] == 1
    # Une fois tout actif, le cron ne relit plus ce pilote
    monkeypatch.setattr(payments, "get_pilot_status", lambda acc: pytest.fail("relu inutilement"))
    import db
    db.execute("UPDATE pilot_profiles SET stripe_charges_enabled=1, stripe_payouts_enabled=1 "
               "WHERE stripe_account_id LIKE 'acct_live_%'")
    assert services.sync_pending_pilot_stripe_status() == 0


def _age(table, col, where, days, params=()):
    import db
    db.execute(f"UPDATE {table} SET {col}=datetime('now', '-{days} days') WHERE {where}", params)


def test_auto_liberation_attend_la_fin_de_mission_et_le_dernier_livrable(
        app_ctx, funded_booking, open_mission, pilot_user):
    import db
    import services
    # Paye il y a 10 jours, mission datee dans 5 jours : pas de liberation
    _age("bookings", "paid_at", "id=?", 10, (funded_booking,))
    db.execute("UPDATE missions SET start_date=?, end_date=? WHERE id=?",
               ("2999-01-01T09:00", "2999-01-01T12:00", open_mission))
    assert funded_booking not in services.stale_funded_bookings(7)
    # Mission terminee il y a 8 jours : liberable
    db.execute("UPDATE missions SET start_date=datetime('now','-9 days'), "
               "end_date=datetime('now','-8 days') WHERE id=?", (open_mission,))
    assert funded_booking in services.stale_funded_bookings(7)
    # Un livrable depose il y a 2 jours relance l'horloge
    services.add_deliverable(booking_id=funded_booking, uploaded_by_user_id=pilot_user["id"],
                             label="Photos", original_filename="p.zip", stored_filename="p.zip",
                             mime_type="application/zip", size_bytes=10)
    assert funded_booking not in services.stale_funded_bookings(7)
    _age("booking_deliverables", "created_at", "booking_id=?", 8, (funded_booking,))
    assert funded_booking in services.stale_funded_bookings(7)
    # Sans aucune date de mission : le paiement seul fait foi (comme avant)
    db.execute("UPDATE missions SET start_date=NULL, end_date=NULL WHERE id=?", (open_mission,))
    assert funded_booking in services.stale_funded_bookings(7)
    _age("bookings", "paid_at", "id=?", 3, (funded_booking,))
    assert funded_booking not in services.stale_funded_bookings(7)


def test_contestation_carte_gele_la_reservation(client, app_ctx, funded_booking, monkeypatch):
    import payments
    import services
    import db
    db.execute("UPDATE bookings SET stripe_payment_intent_id='pi_dispute' WHERE id=?", (funded_booking,))
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    monkeypatch.setattr(payments, "parse_webhook", lambda *_a: {
        "type": "charge.dispute.created",
        "data": {"object": {"payment_intent": "pi_dispute", "reason": "fraudulent"}}})
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    b = services.get_booking(funded_booking)
    assert b["status"] == "disputed" and "Contestation bancaire" in (b.get("dispute_reason") or "")
    # En litige : ni auto-liberation ni validation
    _age("bookings", "paid_at", "id=?", 30, (funded_booking,))
    assert funded_booking not in services.stale_funded_bookings(7)
    assert services.confirm_completion(funded_booking, b["client_user_id"]) is False


def test_sequestre_et_retrait(client, auth_client, make_user, app_ctx, funded_booking, monkeypatch):
    import db
    import payments
    import services
    b = services.get_booking(funded_booking)
    reserve = services.escrow_reserve()
    assert reserve.get(b["currency"].upper(), 0) >= float(b["agreed_price"])

    u = make_user("admin_retrait", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    cur = b["currency"].upper()
    monkeypatch.setattr(payments, "diagnostics", lambda: {
        "mode": "LIVE", "connect_flag": True, "webhook_secret": True, "publishable": True,
        "connect_webhook_secret": False, "connect_webhook_present": False,
        "webhook_url": "u", "connect_webhook_url": "c", "expected_account": "", "account_mismatch": False,
        "account": {"id": "acct_x", "name": "AubePilot", "country": "CA", "currency": "cad",
                    "charges_enabled": True, "payouts_enabled": True, "details_submitted": True},
        "payout_schedule": {"interval": "daily", "delay_days": 3, "manual": False},
        "balance": {cur: {"available": reserve[cur] + 50.0, "pending": 12.0}},
        "connect": True, "connected": [], "webhooks": [], "errors": []})
    payouts = []
    monkeypatch.setattr(payments, "create_platform_payout",
                        lambda amount, currency, note="": payouts.append((amount, currency)) or "po_1")
    c = auth_client(u["id"])
    html = c.get("/admin/stripe").data.decode()
    assert "Sous séquestre" in html and "Manuel" in html and "50.00" in html
    # Retrait plafonne a ce qui n'est pas sous sequestre, meme si on demande plus
    r = c.post("/admin/stripe/retirer", data={"currency": cur, "amount": "9999"})
    assert r.status_code in (302, 303)
    assert payouts == [(50.0, cur)]
    # Un visiteur ordinaire ne peut pas retirer
    v = make_user("pas_admin_retrait", role="client")
    assert auth_client(v["id"]).post("/admin/stripe/retirer", data={"currency": cur}).status_code == 403


def test_webhook_connect_exempt_de_csrf():
    from security import CSRF_EXEMPT_ROUTES
    assert {"stripe_webhook", "stripe_webhook_connect"} <= CSRF_EXEMPT_ROUTES
