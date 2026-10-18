"""Verrou du compte de paiement : un pilote dont Stripe ne peut pas encore
verser l'argent a sa banque ne s'engage sur aucune mission (2026-10-16).

Cas reel : premier paiement de bout en bout, transfert recu par le pilote
mais bloque chez Stripe faute de piece d'identite et de selfie, sans que le
site le lui ait dit.
"""
from html import unescape

import pytest

DESC = "Prestation drone 4K complete avec livrables et montage soigne"


@pytest.fixture()
def connect_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)


def _pilot(make_user, state):
    import services
    u = make_user("lock_p", role="pilot", country="Canada", city="Montréal")
    if state == "pending":
        services.set_pilot_stripe_account(u["id"], f"acct_fake_{u['id']}",
                                          charges_enabled=True, payouts_enabled=False)
    elif state == "ok":
        services.set_pilot_stripe_account(u["id"], f"acct_fake_{u['id']}",
                                          charges_enabled=True, payouts_enabled=True)
    return u


def _mission(make_user):
    import services
    c = make_user("lock_c", role="client", country="Canada", city="Montréal")
    mid = services.create_mission(
        c["id"], title="Inspection de toiture", description="Toiture d'un duplex",
        mission_type="inspection", country="Canada", city="Montréal",
        budget_min=200, budget_max=400, currency="CAD")
    return mid, c


@pytest.mark.parametrize("state", ["none", "pending"])
def test_devis_refuse_tant_que_stripe_ne_peut_pas_payer(make_user, connect_on, state):
    import db, services
    p = _pilot(make_user, state)
    mid, _c = _mission(make_user)
    assert services.pilot_payment_state(services.get_pilot_profile(p["id"])) == state
    with pytest.raises(ValueError) as exc:
        services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    assert "Stripe" in str(exc.value)
    assert db.fetchone("SELECT 1 FROM bids WHERE mission_id=? AND pilot_user_id=?",
                       (mid, p["id"])) is None


def test_devis_accepte_une_fois_verifie(make_user, connect_on):
    import services
    p = _pilot(make_user, "ok")
    mid, _c = _mission(make_user)
    assert services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)


def test_aucun_verrou_si_connect_ferme_ou_pays_non_desservi(make_user, monkeypatch):
    import config, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", False)
    p = _pilot(make_user, "none")
    mid, _c = _mission(make_user)
    assert services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    # Connect ouvert, mais Stripe ne dessert pas le pays : rien a verifier
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    far = make_user("lock_far", role="pilot", country="Iran", city="Téhéran")
    assert services.pilot_payment_state(services.get_pilot_profile(far["id"])) == "na"
    assert services.place_bid(mid, far["id"], price=300, currency="CAD", description=DESC)


def test_page_mission_remplace_le_formulaire_et_bandeau(auth_client, make_user, connect_on):
    p = _pilot(make_user, "pending")
    mid, _c = _mission(make_user)
    c = auth_client(p["id"])
    html = unescape(c.get(f"/missions/{mid}").data.decode())
    assert "Compte de paiement à finaliser avant d'envoyer un devis" in html
    assert "Terminer la vérification" in html
    assert 'name="price"' not in html
    # bandeau en haut de chaque page : la verification, pas « activez »
    assert "Vérification Stripe à terminer." in html
    assert "Activez vos paiements" not in html

    r = c.post(f"/missions/{mid}/enchere", data={"price": "300", "currency": "CAD",
                                                 "description": DESC}, follow_redirects=True)
    assert "Terminez d'abord la vérification" in unescape(r.data.decode())


def test_bandeau_sans_compte_dit_le_verrou(auth_client, make_user, connect_on):
    p = _pilot(make_user, "none")
    html = unescape(auth_client(p["id"]).get("/espace").data.decode())
    assert "Activez vos paiements" in html
    assert "vous ne pouvez pas envoyer de devis" in html


def test_bandeau_disparait_une_fois_verifie(auth_client, make_user, connect_on):
    p = _pilot(make_user, "ok")
    html = unescape(auth_client(p["id"]).get("/espace").data.decode())
    assert "Activez vos paiements" not in html
    assert "Vérification Stripe à terminer." not in html


def test_devis_depuis_conversation_ne_cree_pas_de_demande_orpheline(make_user, connect_on):
    import db, services
    p = _pilot(make_user, "pending")
    c = make_user("lock_cc", role="client", country="Canada", city="Montréal")
    db.execute("INSERT INTO direct_messages (client_user_id, pilot_user_id, sender_user_id, body) "
               "VALUES (?, ?, ?, ?)", (c["id"], p["id"], c["id"], "Bonjour, une toiture a inspecter"))
    before = db.fetchone("SELECT COUNT(*) AS n FROM missions WHERE client_user_id=?", (c["id"],))["n"]
    with pytest.raises(ValueError):
        services.propose_quote_from_contact(
            c["id"], p["id"],
            mission={"title": "Toiture", "description": "Inspection", "mission_type": "inspection",
                     "country": "Canada", "city": "Montréal", "currency": "CAD"},
            bid={"price": 300, "currency": "CAD", "description": DESC})
    after = db.fetchone("SELECT COUNT(*) AS n FROM missions WHERE client_user_id=?", (c["id"],))["n"]
    assert after == before


def test_paiement_client_bloque_si_verification_en_attente(auth_client, make_user,
                                                          connect_on, monkeypatch):
    """Devis d'avant le verrou, ou compte que Stripe a bloque depuis : on
    n'encaisse pas un argent qu'on ne pourrait pas verser au pilote."""
    import db, payments, services
    monkeypatch.setattr(payments, "is_available", lambda: True)
    p = _pilot(make_user, "ok")
    mid, c = _mission(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    services.update_pilot_stripe_status(p["id"], True, False)   # Stripe redemande des pieces
    r = auth_client(c["id"]).post(f"/reservations/{booking_id}/payer")
    assert r.status_code == 302
    assert "/stripe/fake-checkout" not in r.headers["Location"]
    assert db.fetchone("SELECT stripe_session_id FROM bookings WHERE id=?",
                       (booking_id,))["stripe_session_id"] is None


# ---------------------------------------------------------------------------
# Paiements en pause (config.PAYMENTS_PAUSED, 2026-10-18) : le temps de
# valider les taxes du Quebec, ni devis, ni acceptation, ni paiement, ni
# collecte. Le reste du site reste ouvert.
# ---------------------------------------------------------------------------

def test_pause_bloque_devis_acceptation_et_paiement(make_user, auth_client, monkeypatch, connect_on):
    import config, db, services
    p = _pilot(make_user, "ok")
    mid, c = _mission(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    other = _pilot(make_user, "ok")
    bid2 = services.place_bid(mid, other["id"], price=320, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])

    monkeypatch.setattr(config, "PAYMENTS_PAUSED", True)
    # le pilote ne peut plus envoyer de devis : message a la place du formulaire
    mid2, _c2 = _mission(make_user)
    with pytest.raises(ValueError) as exc:
        services.place_bid(mid2, p["id"], price=300, currency="CAD", description=DESC)
    assert "en pause" in str(exc.value)
    html = unescape(auth_client(p["id"]).get(f"/missions/{mid2}").data.decode())
    assert "Paiements en pause" in html and 'name="price"' not in html
    # le client ne peut pas payer la reservation deja creee
    cc = auth_client(c["id"])
    r = cc.get(f"/reservations/{booking_id}/payer", follow_redirects=True)
    assert "Les devis et les paiements sont en pause" in unescape(r.data.decode())
    assert services.get_booking(booking_id)["status"] == "pending_payment"
    page = unescape(cc.get(f"/reservations/{booking_id}").data.decode())
    assert f"/reservations/{booking_id}/payer" not in page
    # ni accepter un autre devis
    db.execute("UPDATE missions SET status='open' WHERE id=?", (mid,))
    db.execute("UPDATE bids SET status='pending' WHERE id=?", (bid2,))
    with pytest.raises(ValueError):
        services.accept_bid(mid, bid2, c["id"])
