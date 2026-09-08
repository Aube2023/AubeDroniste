"""Garde-fous qui doivent rester actifs avant un deploiement avec vrais comptes."""
import uuid


def test_existing_aubemail_identity_is_not_reprovisioned(
    client, app_ctx, monkeypatch
):
    import auth
    import aubemail_client
    import config
    import db

    username = f"pam_{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(config, "ALLOW_LOCAL_ACCOUNTS", False)
    monkeypatch.setattr(auth, "password_managed_by_aubemail", lambda _u: True)
    monkeypatch.setattr(auth, "authenticate", lambda _u, _p: True)

    def forbidden_provision(**_kwargs):
        raise AssertionError("un compte AubeMail existant ne doit pas etre reprovisionne")

    monkeypatch.setattr(aubemail_client, "provision_account", forbidden_provision)
    response = client.post("/inscription", data={
        "username": username,
        "password": "mot-de-passe-solide",
        "confirm": "mot-de-passe-solide",
        "full_name": "Compte PAM Existant",
        "role": "client",
        "country": "Canada",
    })
    assert response.status_code == 302
    assert db.fetchone("SELECT id FROM users WHERE username=?", (username,)) is not None


def test_fake_payments_are_never_implicit(monkeypatch):
    import payments
    monkeypatch.setattr(payments, "STRIPE_FAKE_MODE", False)
    monkeypatch.setattr(payments, "STRIPE_PAYMENTS_ENABLED", False)
    monkeypatch.setattr(payments, "STRIPE_SECRET_KEY", "")
    assert payments.is_fake() is False
    assert payments.is_available() is False
    assert payments.refund_payment("pi_real") is False
    try:
        payments.create_checkout_session(
            booking_id=1, amount=10, currency="EUR",
            mission_title="Test", client_email="client@example.test",
        )
    except payments.PaymentUnavailableError:
        pass
    else:
        raise AssertionError("une installation sans Stripe ne doit pas simuler un paiement")


def test_checkout_webhook_validates_session_amount_and_currency(
    client, open_mission, pending_bid, client_user, monkeypatch
):
    import payments
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    services.attach_payment_session(booking_id, "cs_expected")
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)

    event = {
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_wrong", "payment_status": "paid",
            "amount_total": 100000, "currency": "eur",
            "payment_intent": "pi_test",
            "metadata": {"booking_id": str(booking_id)},
        }},
    }
    monkeypatch.setattr(payments, "parse_webhook", lambda *_args: event)
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    booking = services.get_booking(booking_id)
    assert booking is not None and booking["status"] == "pending_payment"

    event["data"]["object"]["id"] = "cs_expected"
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    booking = services.get_booking(booking_id)
    assert booking is not None and booking["status"] == "funded"

    # Un event charge.refunded partiel ne doit pas fermer tout le dossier.
    event.clear()
    event.update({
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_test", "refunded": False}},
    })
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    booking = services.get_booking(booking_id)
    assert booking is not None and booking["status"] == "funded"

    event["data"]["object"]["refunded"] = True
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    booking = services.get_booking(booking_id)
    assert booking is not None and booking["status"] == "refunded"
    mission = services.get_mission(open_mission)
    assert mission is not None and mission["status"] == "cancelled"


def test_payment_route_keeps_booking_pending_when_provider_is_disabled(
    client, auth_client, open_mission, pending_bid, client_user, monkeypatch
):
    import payments
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    monkeypatch.setattr(payments, "is_available", lambda: False)
    response = auth_client(client_user["id"]).get(
        f"/reservations/{booking_id}/payer",
    )
    assert response.status_code == 302
    booking = services.get_booking(booking_id)
    assert booking is not None and booking["status"] == "pending_payment"


def test_assigned_mission_cannot_be_closed_through_public_route(
    client, auth_client, open_mission, pending_bid, client_user
):
    import services
    services.accept_bid(open_mission, pending_bid, client_user["id"])
    response = auth_client(client_user["id"]).post(
        f"/missions/{open_mission}/cloturer",
    )
    assert response.status_code == 302
    mission = services.get_mission(open_mission)
    assert mission is not None and mission["status"] == "assigned"


def test_public_mission_masks_client_name(client, make_user):
    import services
    owner = make_user(
        "privacy_owner", role="client", full_name="Alexandre Confidentiel",
    )
    mission_id = services.create_mission(
        owner["id"], title="Mission publique", description="Description publique",
        mission_type="photo", country="Canada",
    )
    html = client.get(f"/missions/{mission_id}").data.decode()
    assert "Alexandre Confidentiel" not in html
    assert services.mask_full_name("Alexandre Confidentiel") in html


def test_language_redirect_rejects_external_target(client):
    response = client.get("/lang/fr?next=https://evil.example/phishing")
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_rate_limit_ip_does_not_trust_forwarded_for(app):
    import security
    with app.test_request_context(
        "/", headers={"X-Forwarded-For": "198.51.100.1"},
        environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
    ):
        assert security._client_ip() == "203.0.113.7"
    with app.test_request_context(
        "/", headers={"X-Real-IP": "203.0.113.9"},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert security._client_ip() == "203.0.113.9"


def test_mission_validation_rejects_incoherent_money_and_coordinates(
    app_ctx, make_user
):
    import pytest
    import services
    owner = make_user("mission_validation", role="client")
    base = {
        "title": "Mission", "description": "Description",
        "mission_type": "photo", "country": "Canada",
    }
    with pytest.raises(ValueError):
        services.create_mission(owner["id"], **base, budget_min=500, budget_max=100)
    with pytest.raises(ValueError):
        services.create_mission(owner["id"], **base, lat=91, lng=0)
    with pytest.raises(ValueError):
        services.create_mission(owner["id"], **base, currency="ZZZ")
