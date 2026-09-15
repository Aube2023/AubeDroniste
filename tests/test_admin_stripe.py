"""Page admin « État de Stripe » : lecture seule, jamais de clé affichée."""


def test_diagnostic_en_mode_simule():
    import payments
    d = payments.diagnostics()
    assert d["mode"] in ("FAKE", "TEST", "LIVE", "DISABLED")
    assert d["webhook_url"].endswith("/stripe/webhook")
    assert isinstance(d["errors"], list) and isinstance(d["webhooks"], list)


def test_page_reservee_aux_admins_sans_secret(client, auth_client, make_user, app_ctx):
    import db
    from config import STRIPE_SECRET_KEY
    u = make_user("admin_stripe", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    html = auth_client(u["id"]).get("/admin/stripe").data.decode()
    assert "Où en est Stripe." in html and "go-live-stripe.sh" in html
    assert 'name="robots" content="noindex' in html
    assert "sk_" not in html and "whsec_" not in html
    if STRIPE_SECRET_KEY:
        assert STRIPE_SECRET_KEY not in html
    v = make_user("pas_admin_stripe", role="client")
    assert auth_client(v["id"]).get("/admin/stripe").status_code == 403
    assert client.application.test_client().get("/admin/stripe").status_code in (302, 403)


def _sdk():
    """Le SDK stripe n'est pas obligatoire pour la suite : on saute sinon."""
    import pytest
    return pytest.importorskip("stripe")


def test_plain_convertit_les_objets_du_sdk():
    """stripe-python 15 : StripeObject n'est plus un dict (plus de .get()).
    _plain doit rendre des dicts imbriques ordinaires, SDK ancien ou recent."""
    import payments
    stripe = _sdk()
    ev = stripe.Event.construct_from({
        "id": "evt_1", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {"id": "cs_1", "object": "checkout.session",
                            "metadata": {"booking_id": "7"}, "payment_intent": "pi_1",
                            "payment_status": "paid", "amount_total": 1000, "currency": "cad"}},
    }, "sk_test_x")
    d = payments._plain(ev)
    assert type(d) is dict and type(d["data"]["object"]) is dict
    assert d["data"]["object"]["metadata"].get("booking_id") == "7"
    assert d["data"]["object"].get("customer_email") is None
    lst = stripe.ListObject.construct_from(
        {"object": "list", "data": [{"id": "we_1", "object": "webhook_endpoint",
                                     "enabled_events": ["a", "b"]}], "has_more": False}, "sk")
    p = payments._plain(lst)
    assert p["data"][0]["enabled_events"] == ["a", "b"]
    assert payments._plain(None) is None and payments._plain({"a": [1, {"b": 2}]}) == {"a": [1, {"b": 2}]}


def test_webhook_accepte_un_event_du_sdk(client, open_mission, pending_bid, client_user, monkeypatch):
    """Regression : l'evenement signe passe par _plain, donc le webhook lit un
    dict et marque la reservation payee (avant : AttributeError 'get' -> 500)."""
    import payments
    import services
    stripe = _sdk()
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    services.attach_payment_session(booking_id, "cs_sdk")
    booking = services.get_booking(booking_id)
    ev = stripe.Event.construct_from({
        "id": "evt_2", "object": "event", "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_sdk", "object": "checkout.session", "payment_status": "paid",
            "amount_total": int(round(float(booking["agreed_price"]) * 100)),
            "currency": booking["currency"].lower(), "payment_intent": "pi_sdk",
            "metadata": {"booking_id": str(booking_id)},
        }},
    }, "sk_test_x")
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    monkeypatch.setattr(payments, "parse_webhook", lambda *_a: payments._plain(ev))
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    assert services.get_booking(booking_id)["status"] == "funded"


def test_diagnostic_lit_les_objets_du_sdk(monkeypatch):
    """Le diagnostic ne doit produire aucune erreur avec de vrais objets du
    SDK, et ne conclure « Connect : oui » qu'avec un compte connecte."""
    import payments
    stripe = _sdk()
    acc = stripe.Account.construct_from({
        "id": "acct_plat", "object": "account", "country": "CA", "default_currency": "cad",
        "settings": {"dashboard": {"display_name": "AubePilot"}},
        "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}, "sk")
    connected = [stripe.Account.construct_from({
        "id": "acct_pilote", "object": "account", "type": "express", "country": "CA",
        "charges_enabled": False, "payouts_enabled": False, "details_submitted": False}, "sk")]
    hooks = stripe.ListObject.construct_from({
        "object": "list", "has_more": False, "url": "/v1/webhook_endpoints",
        "data": [{"id": "we_1", "object": "webhook_endpoint", "status": "enabled",
                  "url": payments.SITE_URL + "/stripe/webhook",
                  "enabled_events": ["checkout.session.completed"]}]}, "sk")

    class _Accounts:
        @staticmethod
        def retrieve():
            return acc

        @staticmethod
        def list(limit=100):
            class _L:
                @staticmethod
                def auto_paging_iter():
                    return iter(connected)
            return _L()

    class _Hooks:
        @staticmethod
        def list(limit=20):
            return hooks

    class _SDK:
        Account = _Accounts
        WebhookEndpoint = _Hooks

    monkeypatch.setattr(payments, "_stripe", lambda: _SDK())
    monkeypatch.setattr(payments, "STRIPE_ACCOUNT_ID", "acct_autre")
    d = payments.diagnostics()
    assert d["errors"] == []
    assert d["account"]["id"] == "acct_plat" and d["account"]["name"] == "AubePilot"
    assert d["account_mismatch"] is True
    assert d["connect"] is True and d["connected"][0]["id"] == "acct_pilote"
    assert d["webhooks"][0]["ours"] is True
    assert d["webhooks"][0]["missing"] == ["charge.refunded"]
    assert d["connect_webhook_present"] is False

    connected.clear()
    monkeypatch.setattr(payments, "STRIPE_ACCOUNT_ID", "acct_plat")
    d = payments.diagnostics()
    assert d["connect"] is None and d["connected"] == [] and d["account_mismatch"] is False
