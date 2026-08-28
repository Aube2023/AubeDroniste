"""Commission degressive + annulation anti-contournement + desistement pilote.

Regles testees (config.py) :
  PLATFORM_FEE_TIERS            20 % / 15 % / 10 % selon missions terminees entre les memes parties
  CANCELLATION_GRACE_HOURS      annulation client gratuite juste apres paiement
  CANCELLATION_SERVICE_FEE_PCT  frais de service retenus apres la grace (plafonnes)
  LATE_CANCELLATION_FEE_PCT     dedommagement pilote si preavis < LATE_CANCELLATION_HOURS
  cancel_booking_by_pilot       remboursement integral, mission remise en ligne

Conventions conftest : imports de modules projet DANS les tests uniquement.
"""
from datetime import datetime, timedelta, timezone

import pytest


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Commission degressive
# ---------------------------------------------------------------------------

def test_platform_fee_tiers():
    import services
    from config import PLATFORM_FEE_TIERS, PLATFORM_FEE_PCT
    assert services.platform_fee_pct_for(0) == PLATFORM_FEE_PCT == 20.0
    assert services.platform_fee_pct_for(2) == 20.0
    assert services.platform_fee_pct_for(3) == 15.0
    assert services.platform_fee_pct_for(8) == 15.0
    assert services.platform_fee_pct_for(9) == 10.0
    assert services.platform_fee_pct_for(50) == 10.0
    assert PLATFORM_FEE_TIERS[0][0] == 0


def test_booking_fee_pct_fallback_for_legacy_bookings():
    import services
    assert services.booking_fee_pct({"platform_fee_pct": 15.0, "agreed_price": 1000, "platform_fee": 999}) == 15.0
    # ancien booking (colonne NULL) : deduit du montant stocke, pas du taux courant
    assert services.booking_fee_pct({"platform_fee_pct": None, "agreed_price": 1000, "platform_fee": 300}) == 30.0
    assert services.booking_fee_pct({"agreed_price": 0, "platform_fee": 0}) == 20.0


def _seed_completed(client_id, pilot_id, n):
    """Insere n bookings 'completed' entre le couple (sans passer par Stripe)."""
    import db
    mission = db.execute(
        "INSERT INTO missions (client_user_id, title, description, mission_type, country, status) "
        "VALUES (?, 'fidelite', 'x', 'photo', 'France', 'done')", (client_id,)).lastrowid
    for i in range(n):
        bid = db.execute(
            "INSERT INTO bids (mission_id, pilot_user_id, price, currency, status) "
            "VALUES (?, ?, 100, 'EUR', 'accepted')", (mission, pilot_id)).lastrowid
        db.execute(
            "INSERT INTO bookings (mission_id, bid_id, client_user_id, pilot_user_id, "
            " agreed_price, currency, platform_fee, status, completed_at) "
            "VALUES (?, ?, ?, ?, 100, 'EUR', 20, 'completed', datetime('now'))",
            (mission, bid, client_id, pilot_id))
        # UNIQUE(mission_id, pilot_user_id) sur bids : une mission par booking
        mission = db.execute(
            "INSERT INTO missions (client_user_id, title, description, mission_type, country, status) "
            "VALUES (?, 'fidelite', 'x', 'photo', 'France', 'done')", (client_id,)).lastrowid


@pytest.mark.parametrize("prior, expected_pct", [(0, 20.0), (3, 15.0), (9, 10.0)])
def test_accept_bid_applies_degressive_fee(app_ctx, make_user, prior, expected_pct):
    import services
    client = make_user("fee_cli", role="client")
    pilot = make_user("fee_pil", role="pilot", lat=48.85, lng=2.35)
    _seed_completed(client["id"], pilot["id"], prior)
    assert services.completed_missions_between(client["id"], pilot["id"]) == prior
    mid = services.create_mission(client["id"], title="Suivante", description="x" * 20,
                                  mission_type="photo", country="France")
    bid = services.place_bid(mid, pilot["id"], price=1000, description="y" * 40)
    booking = services.get_booking(services.accept_bid(mid, bid, client["id"]))
    assert booking["platform_fee_pct"] == expected_pct
    assert booking["platform_fee"] == round(1000 * expected_pct / 100, 2)
    assert services.booking_fee_pct(booking) == expected_pct


# ---------------------------------------------------------------------------
# compute_cancellation_fee : frais de service + grace + plafond + tardif
# ---------------------------------------------------------------------------

def test_service_fee_after_grace_window():
    import services
    from config import CANCELLATION_SERVICE_FEE_PCT, CANCELLATION_GRACE_HOURS
    res = services.compute_cancellation_fee({
        "agreed_price": 1000,
        "scheduled_at": _iso(_now() + timedelta(days=10)),      # preavis large
        "paid_at": _iso(_now() - timedelta(hours=CANCELLATION_GRACE_HOURS + 1)),
    })
    assert res["is_late"] is False and res["fee_amount"] == 0.0
    assert res["within_grace"] is False
    assert res["service_fee_pct"] == CANCELLATION_SERVICE_FEE_PCT
    assert res["service_fee_amount"] == 100.0
    assert res["refund_amount"] == 900.0


def test_no_service_fee_within_grace_window():
    import services
    res = services.compute_cancellation_fee({
        "agreed_price": 1000,
        "scheduled_at": _iso(_now() + timedelta(days=10)),
        "paid_at": _iso(_now() - timedelta(minutes=20)),
    })
    assert res["within_grace"] is True
    assert res["grace_hours_left"] is not None and res["grace_hours_left"] > 1.0
    assert res["service_fee_amount"] == 0.0
    assert res["refund_amount"] == 1000.0


def test_service_fee_is_capped():
    import services
    from config import CANCELLATION_SERVICE_FEE_CAP
    res = services.compute_cancellation_fee({
        "agreed_price": 5000,
        "scheduled_at": _iso(_now() + timedelta(days=10)),
        "paid_at": _iso(_now() - timedelta(days=1)),
    })
    assert res["service_fee_amount"] == CANCELLATION_SERVICE_FEE_CAP == 150.0
    assert res["refund_amount"] == 4850.0


def test_late_and_service_fee_stack():
    import services
    from config import LATE_CANCELLATION_FEE_PCT
    res = services.compute_cancellation_fee({
        "agreed_price": 1000,
        "scheduled_at": _iso(_now() + timedelta(hours=1)),      # tardif
        "paid_at": _iso(_now() - timedelta(days=2)),            # hors grace
    })
    assert res["is_late"] is True
    assert res["fee_amount"] == round(1000 * LATE_CANCELLATION_FEE_PCT / 100, 2) == 250.0
    assert res["service_fee_amount"] == 100.0
    assert res["refund_amount"] == 650.0
    assert res["fee_amount"] + res["service_fee_amount"] + res["refund_amount"] == 1000.0


def test_no_service_fee_when_not_paid():
    import services
    res = services.compute_cancellation_fee({
        "agreed_price": 800, "scheduled_at": _iso(_now() + timedelta(days=3)),
    })
    assert res["service_fee_amount"] == 0.0 and res["refund_amount"] == 800.0


# ---------------------------------------------------------------------------
# cancel_booking_by_client : bout en bout (Stripe fake)
# ---------------------------------------------------------------------------

def _backdate_payment(booking_id, hours):
    import db
    db.execute("UPDATE bookings SET paid_at=? WHERE id=?",
               (_iso(_now() - timedelta(hours=hours)), booking_id))


def test_client_cancel_in_grace_is_free(funded_booking, client_user):
    import services
    b = services.get_booking(funded_booking)
    assert b["status"] == "funded" and b["paid_at"]
    res = services.cancel_booking_by_client(funded_booking, client_user["id"], reason="erreur")
    assert res["ok"] and res["paid"] and res["within_grace"]
    assert res["service_fee_amount"] == 0.0 and res["refund_amount"] == 1000.0
    b = services.get_booking(funded_booking)
    assert b["status"] == "cancelled" and b["cancelled_by"] == "client"
    assert b["cancellation_service_fee"] == 0.0


def test_client_cancel_after_grace_keeps_service_fee(funded_booking, client_user):
    import services
    _backdate_payment(funded_booking, hours=5)
    res = services.cancel_booking_by_client(funded_booking, client_user["id"])
    assert res["ok"] and not res["within_grace"]
    assert res["service_fee_amount"] == 100.0 and res["refund_amount"] == 900.0
    b = services.get_booking(funded_booking)
    assert b["cancellation_service_fee"] == 100.0
    assert b["cancellation_fee"] == 0.0            # preavis suffisant : pilote non dedommage


def test_client_late_cancel_transfers_compensation_to_pilot(app_ctx, funded_booking, client_user):
    import db
    import services
    _backdate_payment(funded_booking, hours=5)
    db.execute("UPDATE bookings SET scheduled_at=? WHERE id=?",
               (_iso(_now() + timedelta(hours=2)), funded_booking))
    res = services.cancel_booking_by_client(funded_booking, client_user["id"])
    assert res["ok"] and res["is_late"]
    assert res["fee_amount"] == 250.0 and res["service_fee_amount"] == 100.0
    assert res["refund_amount"] == 650.0
    assert res["compensation_transfer"] and "cancel-compensation" in res["compensation_transfer"]
    b = services.get_booking(funded_booking)
    assert b["cancellation_fee"] == 250.0
    assert b["stripe_transfer_id"] == res["compensation_transfer"]


def test_client_cancel_unpaid_booking_no_amounts(app_ctx, open_mission, pending_bid, client_user):
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    res = services.cancel_booking_by_client(booking_id, client_user["id"])
    assert res["ok"] and res["paid"] is False
    assert res["refund_amount"] == 0.0 and res["service_fee_amount"] == 0.0
    assert services.get_booking(booking_id)["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Desistement pilote
# ---------------------------------------------------------------------------

def test_pilot_cancel_refunds_client_and_reopens_mission(funded_booking, pilot_user, client_user):
    import services
    b = services.get_booking(funded_booking)
    _backdate_payment(funded_booking, hours=48)    # meme hors grace : jamais de frais
    res = services.cancel_booking_by_pilot(funded_booking, pilot_user["id"], reason="panne")
    assert res["ok"] and res["paid"] and res["refund_amount"] == 1000.0
    b2 = services.get_booking(funded_booking)
    assert b2["status"] == "cancelled" and b2["cancelled_by"] == "pilot"
    assert b2["cancellation_fee"] == 0.0 and b2["cancellation_service_fee"] == 0.0
    mission = services.get_mission(b["mission_id"])
    assert mission["status"] == "open"
    bids = {x["id"]: x for x in services.list_bids(b["mission_id"])}
    assert bids[b["bid_id"]]["status"] == "withdrawn"
    # plus de relation financee -> identite du pilote de nouveau masquee
    assert services.has_funded_relation(client_user["id"], pilot_user["id"]) is False


def test_pilot_cancel_refused_for_client_or_closed(funded_booking, client_user, pilot_user):
    import services
    assert services.cancel_booking_by_pilot(funded_booking, client_user["id"])["ok"] is False
    assert services.confirm_completion(funded_booking, client_user["id"]) is True
    assert services.cancel_booking_by_pilot(funded_booking, pilot_user["id"])["ok"] is False


def test_pilot_cancel_route(client, auth_client, funded_booking, pilot_user, client_user):
    import services
    c = auth_client(client_user["id"])
    r = c.post(f"/reservations/{funded_booking}/annuler-pilote", data={"reason": "x"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        assert services.get_booking(funded_booking)["status"] == "funded"   # client refuse
    c = auth_client(pilot_user["id"])
    r = c.post(f"/reservations/{funded_booking}/annuler-pilote", data={"reason": "météo"})
    assert r.status_code in (302, 303)
    with client.application.app_context():
        assert services.get_booking(funded_booking)["status"] == "cancelled"


def test_booking_page_shows_policy_and_pilot_withdraw(client, auth_client, funded_booking,
                                                      pilot_user, client_user):
    html = auth_client(client_user["id"]).get(f"/reservations/{funded_booking}").data.decode()
    assert "Fenêtre de grâce" in html or "fenêtre de grâce" in html
    assert "frais de service" in html
    assert "Commission Aube (20%)" in html
    html = auth_client(pilot_user["id"]).get(f"/reservations/{funded_booking}").data.decode()
    assert "Me désister de la mission" in html


def test_cgu_and_faq_reflect_new_rules(client):
    html = client.get("/cgu").data.decode()
    assert "20 %" in html and "15 %" in html and "10 %" in html
    assert "Fenêtre de grâce" in html and "7.2 Par le pilote" in html
    faq = client.get("/faq").data.decode()
    assert "frais de service de 10 %" in faq
    assert "remboursé à 100 %" in faq
    assert "30 %" not in faq
