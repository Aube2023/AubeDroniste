"""Collectes « Soutenez ce pilote » : ouverture, contribution, versement.

Financement souple : chaque contribution payee est versee au pilote (moins
la part plateforme), pas de tout-ou-rien. Ouverture reservee aux pilotes
verifies avec des versements actifs, et seulement si Connect est ouvert.
"""
import pytest


def _verifie_payable(make_user, services, db, nom="camp_p"):
    u = make_user(nom, role="pilot", country="Canada", city="Montréal")
    services.upsert_pilot_profile(u["id"], is_available=1)
    db.execute("UPDATE users SET is_verified=1 WHERE id=?", (u["id"],))
    services.set_pilot_stripe_account(u["id"], f"acct_fake_{u['id']}",
                                      charges_enabled=True, payouts_enabled=True)
    return u


CHAMPS = dict(title="Un drone thermique pour les toitures", equipment="DJI Mavic 3 Thermal + batteries",
              reason="Les inspections de toiture demandent une caméra thermique pour repérer les infiltrations "
                     "et les défauts d'isolation ; sans elle, je refuse la moitié des demandes.",
              goal_amount=6500)


def test_validation_exige_le_materiel_et_le_pourquoi(app_ctx):
    import services
    assert services.validate_campaign(title="x", equipment="", reason="court", goal_amount=10) == \
        ["title", "equipment", "reason", "goal"]
    assert services.validate_campaign(**CHAMPS) == []


def test_ouverture_reservee_aux_verifies_et_payables(client, auth_client, make_user, app_ctx, monkeypatch):
    import config, db, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    u = make_user("camp_nonverif", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    c = auth_client(u["id"])
    html = c.get("/espace/pilote/collecte").data.decode()
    assert "brevet vérifié" in html                      # explique ce qui manque
    c.post("/espace/pilote/collecte", data=CHAMPS)
    assert services.active_campaign_for(u["id"]) is None


def test_fermee_tant_que_connect_est_ferme(auth_client, make_user, app_ctx, monkeypatch):
    import config, db, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", False)
    u = _verifie_payable(make_user, services, db, "camp_noconnect")
    c = auth_client(u["id"])
    assert "pas encore ouvert" in c.get("/espace/pilote/collecte").data.decode()
    c.post("/espace/pilote/collecte", data=CHAMPS)
    assert services.active_campaign_for(u["id"]) is None


def test_parcours_complet(client, auth_client, make_user, app_ctx, monkeypatch):
    import config, db, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    u = _verifie_payable(make_user, services, db)
    c = auth_client(u["id"])
    r = c.post("/espace/pilote/collecte", data=CHAMPS)
    assert r.status_code in (302, 303)
    camp = services.active_campaign_for(u["id"])
    assert camp and camp["goal_amount"] == 6500 and camp["progress_pct"] == 0
    # une seule collecte active par pilote
    assert services.create_campaign(u["id"], currency="CAD", **CHAMPS) is None

    # la fiche publique montre la collecte, le materiel et le pourquoi
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Soutenez ce pilote" in html and "Mavic 3 Thermal" in html and "infiltrations" in html
    assert 'action="/pilotes/%d/soutenir"' % u["id"] in html

    # un soutien anonyme contribue 50 (mode fake : page simulee)
    r = client.post(f"/pilotes/{u['id']}/soutenir", data={"amount": "50", "supporter_name": "Marie",
                                                           "message": "Merci pour les toitures", "anonymous": "1"})
    assert r.status_code in (302, 303) and "/stripe/fake-contribution/" in r.headers["Location"]
    cid = int(r.headers["Location"].rsplit("/", 1)[1])
    contrib = services.get_contribution(cid)
    assert contrib["status"] == "pending" and contrib["platform_fee"] == 0.5     # 1 %

    # paiement simule : payee, versee, total mis a jour
    client.post(f"/stripe/fake-contribution/{cid}")
    contrib = services.get_contribution(cid)
    assert contrib["status"] == "transferred" and contrib["stripe_transfer_id"]
    camp = services.active_campaign_for(u["id"])
    assert camp["raised_amount"] == 50 and camp["contributors"] == 1
    # anonyme : ni nom ni message sur la fiche
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Marie" not in html and "Merci pour les toitures" not in html and "Anonyme" in html
    # idempotent : un second webhook/clic ne compte pas deux fois
    assert services.mark_contribution_paid(cid) is False
    assert services.active_campaign_for(u["id"])["raised_amount"] == 50

    # montant libre et bornes
    r = client.post(f"/pilotes/{u['id']}/soutenir", data={"amount": "20", "amount_other": "3"})
    assert "fake-contribution" not in r.headers.get("Location", "")          # 3 < minimum
    # cloture
    c.post("/espace/pilote/collecte", data={"action": "close"})
    assert services.active_campaign_for(u["id"]) is None
    assert client.post(f"/pilotes/{u['id']}/soutenir", data={"amount": "50"}).status_code == 404


def test_versement_differe_sans_compte_connect(app_ctx, make_user, monkeypatch):
    """Contribution payee alors que le pilote n'a pas (encore) de compte : elle
    reste 'paid' et le rattrapage la verse plus tard."""
    import config, db, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    u = make_user("camp_late", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    db.execute("UPDATE users SET is_verified=1 WHERE id=?", (u["id"],))
    camp_id = services.create_campaign(u["id"], currency="CAD", **CHAMPS)
    cid = services.create_contribution(camp_id, amount=100, currency="CAD")
    services.mark_contribution_paid(cid, "pi_x")
    assert services.get_contribution(cid)["status"] == "paid"                 # pas de compte
    services.set_pilot_stripe_account(u["id"], f"acct_fake_{u['id']}", charges_enabled=True, payouts_enabled=True)
    assert services.transfer_pending_contributions() == 1
    assert services.get_contribution(cid)["status"] == "transferred"


def test_webhook_contribution_incoherent_ignore(client, app_ctx, make_user, monkeypatch):
    import config, db, payments, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    u = _verifie_payable(make_user, services, db, "camp_wh")
    camp_id = services.create_campaign(u["id"], currency="CAD", **CHAMPS)
    cid = services.create_contribution(camp_id, amount=100, currency="CAD")
    services.attach_contribution_session(cid, "cs_test_1")
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    def faux_event(payload, sig):
        return {"type": "checkout.session.completed", "data": {"object": {
            "id": "cs_test_1", "payment_status": "paid", "amount_total": 999, "currency": "cad",
            "payment_intent": "pi_1", "metadata": {"contribution_id": str(cid)}}}}
    monkeypatch.setattr(payments, "parse_webhook", faux_event)
    client.post("/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "x"})
    assert services.get_contribution(cid)["status"] == "pending"               # montant faux : ignore
    def bon_event(payload, sig):
        e = faux_event(payload, sig); e["data"]["object"]["amount_total"] = 10000; return e
    monkeypatch.setattr(payments, "parse_webhook", bon_event)
    client.post("/stripe/webhook", data=b"{}", headers={"Stripe-Signature": "x"})
    assert services.get_contribution(cid)["status"] in ("paid", "transferred")
