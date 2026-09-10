"""Coeur monetaire : devis -> acceptation -> escrow -> liberation (prix - commission) -> refund.

Tout tourne en mode STRIPE_FAKE (aucune cle en test) : release_to_pilot et
refund_payment sont simules et deterministes. On assert les transitions de
statut et la repartition de l'argent, pas l'envoi d'email (best-effort).
"""
import pytest


def test_split_amounts_base_rate():
    import payments
    from config import PLATFORM_FEE_PCT
    s = payments.split_amounts(100.0)
    assert s == {"total": 100.0, "platform": PLATFORM_FEE_PCT, "pilot": 100.0 - PLATFORM_FEE_PCT}
    # taux explicite (commission degressive)
    assert payments.split_amounts(100.0, fee_pct=10) == {"total": 100.0, "platform": 10.0, "pilot": 90.0}


def test_accept_creates_pending_payment_booking(app_ctx, open_mission,
                                                pending_bid, client_user):
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    b = services.get_booking(booking_id)
    assert b["status"] == "pending_payment"
    from config import PLATFORM_FEE_PCT
    assert b["platform_fee"] == round(1000 * PLATFORM_FEE_PCT / 100, 2)   # taux de base (1re mission)
    assert b["platform_fee_pct"] == PLATFORM_FEE_PCT
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
    # prix - commission au pilote
    from config import PLATFORM_FEE_PCT
    assert b["agreed_price"] - b["platform_fee"] == 1000 - round(1000 * PLATFORM_FEE_PCT / 100, 2)


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


def test_partial_admin_refund_is_rejected_without_changing_state(funded_booking):
    import services
    assert services.refund_booking(funded_booking, amount=10) is False
    assert services.get_booking(funded_booking)["status"] == "funded"


def test_financial_claim_blocks_second_action(funded_booking, client_user):
    import db
    import services
    db.execute(
        "UPDATE bookings SET payment_action='refund', "
        "payment_action_started_at=datetime('now') WHERE id=?",
        (funded_booking,),
    )
    assert services.confirm_completion(funded_booking, client_user["id"]) is False
    assert services.refund_booking(funded_booking) is False
    assert services.get_booking(funded_booking)["status"] == "funded"


def test_review_requires_completed_booking(funded_booking, client_user, pilot_user):
    import services
    with pytest.raises(ValueError):
        services.add_review(
            booking_id=funded_booking, author_user_id=client_user["id"],
            target_user_id=pilot_user["id"], rating=5,
        )
    assert services.confirm_completion(funded_booking, client_user["id"])
    services.add_review(
        booking_id=funded_booking, author_user_id=client_user["id"],
        target_user_id=pilot_user["id"], rating=5,
    )
    assert services.reviewable_booking_for(client_user["id"], pilot_user["id"])["id"] == funded_booking


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


def test_connect_account_kwargs_same_country_vs_cross_border(monkeypatch):
    """Meme pays que la plateforme : compte Express standard ; autre pays :
    accord « recipient » (cross-border payouts) et capacite transfers seule."""
    import payments
    import config
    monkeypatch.setattr(config, "STRIPE_PLATFORM_COUNTRY", "CA")
    user = {"id": 7, "email": "p@x.org", "username": "p"}
    same = payments.account_create_kwargs(user, "CA")
    assert same["type"] == "express" and same["country"] == "CA"
    assert same["capabilities"] == {"transfers": {"requested": True}}
    assert "card_payments" not in same["capabilities"] and "business_type" not in same
    assert "tos_acceptance" not in same
    abroad = payments.account_create_kwargs(user, "FR")
    assert abroad["tos_acceptance"] == {"service_agreement": "recipient"}
    assert abroad["capabilities"] == {"transfers": {"requested": True}}
    assert abroad["metadata"]["user_id"] == "7"


# ---------------------------------------------------------------------------
# Connect fermé : ne promettre ni séquestre ni paiement en ligne
# ---------------------------------------------------------------------------

def test_sans_connect_pas_de_bouton_payer_ni_de_blame(client, auth_client, make_user,
                                                      funded_booking, monkeypatch):
    """Le bouton « Payer » se heurtait a un mur qui accusait le pilote d'une
    inscription Stripe que la plateforme lui interdit de faire."""
    import config, db, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", False)
    bid = funded_booking
    bk = services.get_booking(bid) if isinstance(bid, int) else bid
    booking_id = bk["id"] if isinstance(bk, dict) else bid
    db.execute("UPDATE bookings SET status='pending_payment' WHERE id=?", (booking_id,))
    bk = services.get_booking(booking_id)

    c = auth_client(bk["client_user_id"])
    html = c.get(f"/reservations/{booking_id}").data.decode()
    assert "pas encore ouvert" in html
    assert "Payer" not in html.split("Le paiement en ligne")[1][:600]
    assert "séquestre" not in html.split("Le paiement en ligne")[1][:600]

    # la route elle-meme n'accuse plus personne
    r = c.get(f"/reservations/{booking_id}/payer", follow_redirects=True)
    page = r.data.decode()
    assert "pas encore ouvert" in page
    assert "n'a pas finalisé son inscription Stripe" not in page

    p = auth_client(bk["pilot_user_id"])
    vue_pilote = p.get(f"/reservations/{booking_id}").data.decode()
    assert "Votre offre est acceptée" in vue_pilote
    assert "En attente du paiement client" not in vue_pilote
