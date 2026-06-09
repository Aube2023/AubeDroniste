"""Coeur monetaire : devis -> acceptation -> escrow -> liberation 70/30 -> refund.

Tout tourne en mode STRIPE_FAKE (aucune cle en test) : release_to_pilot et
refund_payment sont simules et deterministes. On assert les transitions de
statut et la repartition de l'argent, pas l'envoi d'email (best-effort).
"""
import pytest


def test_split_amounts_70_30():
    import payments
    s = payments.split_amounts(100.0)
    assert s == {"total": 100.0, "platform": 30.0, "pilot": 70.0}


def test_accept_creates_pending_payment_booking(app_ctx, open_mission,
                                                pending_bid, client_user):
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    b = services.get_booking(booking_id)
    assert b["status"] == "pending_payment"
    assert b["platform_fee"] == 300.0          # 30 % de 1000
    assert b["agreed_price"] == 1000
    # le devis accepte passe 'accepted', la mission 'assigned'
    bid = services.list_bids(open_mission)
    accepted = [x for x in bid if x["id"] == pending_bid][0]
    assert accepted["status"] == "accepted"


def test_double_accept_is_blocked(app_ctx, open_mission, pending_bid, client_user):
    import services
    services.accept_bid(open_mission, pending_bid, client_user["id"])
    with pytest.raises(ValueError):
        services.accept_bid(open_mission, pending_bid, client_user["id"])


def test_accept_by_non_owner_raises(app_ctx, open_mission, pending_bid, make_user):
    import services
    stranger = make_user("escrow_stranger", role="client")
    with pytest.raises(LookupError):
        services.accept_bid(open_mission, pending_bid, stranger["id"])


def test_cannot_accept_own_bid(app_ctx, make_user):
    """Un user role 'both' ne peut pas creer une mission, soumissionner et
    accepter son propre devis."""
    import services
    u = make_user("escrow_self", role="both")
    mid = services.create_mission(u["id"], title="Solo", description="x",
                                  mission_type="autre", country="France")
    # il ne peut deja pas soumissionner sur sa propre mission
    with pytest.raises(ValueError):
        services.place_bid(mid, u["id"], price=500,
                           description="x" * 40)


def test_mark_funded_is_idempotent(app_ctx, open_mission, pending_bid, client_user):
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    assert services.mark_booking_funded(booking_id, "pi_fake_1") is True
    assert services.get_booking(booking_id)["status"] == "funded"
    # rejeu (double webhook / double clic) -> pas de seconde application
    assert services.mark_booking_funded(booking_id, "pi_fake_1") is False


def test_confirm_completion_pays_pilot(funded_booking):
    import services
    b_before = services.get_booking(funded_booking)
    assert b_before["status"] == "funded"
    ok = services.confirm_completion(funded_booking, b_before["client_user_id"])
    assert ok is True
    b = services.get_booking(funded_booking)
    assert b["status"] == "completed"
    assert b["stripe_transfer_id"]                       # transfer (fake) pose
    assert str(b["stripe_transfer_id"]).startswith("tr_fake_")
    # 70 % au pilote
    assert b["agreed_price"] - b["platform_fee"] == 700.0


def test_confirm_completion_by_pilot_refused(funded_booking):
    import services
    b = services.get_booking(funded_booking)
    assert services.confirm_completion(funded_booking, b["pilot_user_id"]) is False
    # reste finance, rejouable
    assert services.get_booking(funded_booking)["status"] == "funded"


def test_pilot_cannot_force_status(funded_booking):
    """La machine a etats n'autorise que pilote funded->in_progress."""
    import services
    b = services.get_booking(funded_booking)
    for bad in ("completed", "cancelled", "refunded", "funded"):
        with pytest.raises(ValueError):
            services.update_booking_status(funded_booking, bad, b["pilot_user_id"])
    # transition legitime
    services.update_booking_status(funded_booking, "in_progress", b["pilot_user_id"])
    assert services.get_booking(funded_booking)["status"] == "in_progress"


def test_refund_booking(funded_booking):
    import services
    assert services.refund_booking(funded_booking) is True
    assert services.get_booking(funded_booking)["status"] == "refunded"


def test_confirm_completion_without_stripe_account_returns_false(
        app_ctx, open_mission, pending_bid, client_user, pilot_user):
    """Si le pilote n'a pas de compte Stripe, on ne libere rien (pas de perte)."""
    import services
    import db
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    services.mark_booking_funded(booking_id, "pi_fake_x")
    # on retire le compte stripe du pilote
    db.execute("UPDATE pilot_profiles SET stripe_account_id=NULL WHERE user_id=?",
               (pilot_user["id"],))
    assert services.confirm_completion(booking_id, client_user["id"]) is False
    # le booking reste finance (fonds non perdus, rejouable)
    assert services.get_booking(booking_id)["status"] == "funded"
