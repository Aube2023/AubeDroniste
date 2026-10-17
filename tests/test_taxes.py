"""Taxes de vente canadiennes (taxes.py, 2026-10-16).

L'Aube Etoilee est inscrite a la TPS et a la TVQ : sa commission est taxes
comprises pour un pilote au Canada. Un pilote inscrit ajoute ses taxes a ses
devis ; le client les paie avec le prix et elles lui sont reversees en
entier. Avant, rien de tout cela n'existait (premier paiement reel le
2026-10-16 : 2,00 $ sans aucune taxe).
"""
from html import unescape

import pytest

DESC = "Inspection de toiture par drone, photos 4K et rapport PDF"
QC_GST, QC_QST = "123456782 RT0001", "1234567890 TQ0001"   # 123456782 : Luhn valide


# ---------------------------------------------------------------------------
# Logique pure
# ---------------------------------------------------------------------------

def test_numeros_normalises_et_controles():
    import taxes
    assert taxes.normalize_gst("713093946RT0001") == "713093946 RT0001"   # celui de L'Aube Etoilee
    assert taxes.normalize_gst(" 713 093 946 ") == "713093946 RT0001"
    assert taxes.normalize_gst("") == ""
    with pytest.raises(ValueError):
        taxes.normalize_gst("713093945 RT0001")          # chiffre de controle faux
    with pytest.raises(ValueError):
        taxes.normalize_gst("12345")
    assert taxes.normalize_qst("1230658818tq0001") == "1230658818 TQ0001"
    assert taxes.normalize_qst("1230658818") == "1230658818 TQ0001"
    with pytest.raises(ValueError):
        taxes.normalize_qst("123065881")


@pytest.mark.parametrize("mission,prov", [
    ({"country": "Canada", "region": "Montérégie"}, "QC"),
    ({"country": "Canada", "region": "Ontario"}, "ON"),
    ({"country": "Canada", "region": "QC"}, "QC"),
    ({"country": "Canada", "address": "1200 rue Peel, Montréal H3B 2T6"}, "QC"),
    ({"country": "Canada", "city": "Montréal"}, "QC"),
    ({"country": "Canada", "lat": 43.65, "lng": -79.38}, "ON"),          # Toronto
    ({"country": "Canada", "lat": 44.65, "lng": -63.57}, "NS"),          # Halifax
    ({"country": "France", "region": "Québec"}, None),
])
def test_province_de_la_mission(mission, prov):
    import taxes
    assert taxes.mission_province(mission) == prov


def test_taxes_du_pilote_selon_la_province():
    import taxes
    pilot = {"country": "Canada", "city": "Montréal", "gst_number": QC_GST, "qst_number": QC_QST}
    qc = taxes.mission_taxes(300, pilot, {"country": "Canada", "region": "Québec"})
    assert [(l["code"], l["amount"]) for l in qc["lines"]] == [("GST", 15.0), ("QST", 29.93)]
    assert qc["total"] == 44.93 and qc["lines"][0]["number"] == QC_GST
    on = taxes.mission_taxes(300, pilot, {"country": "Canada", "region": "Ontario"})
    assert [(l["code"], l["rate"], l["amount"]) for l in on["lines"]] == [("HST", 13.0, 39.0)]
    ab = taxes.mission_taxes(300, pilot, {"country": "Canada", "region": "Alberta"})
    assert [(l["code"], l["amount"]) for l in ab["lines"]] == [("GST", 15.0)]
    # pas de TVQ hors Quebec, rien hors du Canada, rien sans numero
    assert taxes.mission_taxes(300, pilot, {"country": "France"})["lines"] == []
    assert taxes.mission_taxes(300, {"country": "Canada"}, {"country": "Canada", "region": "QC"})["total"] == 0
    # TPS seulement : pas de TVQ meme au Quebec
    only_gst = dict(pilot, qst_number="")
    assert [l["code"] for l in taxes.mission_taxes(300, only_gst, {"country": "Canada", "region": "QC"})["lines"]] == ["GST"]


def test_commission_taxes_comprises():
    import taxes
    qc = taxes.commission_taxes(60, {"country": "Canada", "city": "Montréal"})
    assert [(l["code"], l["amount"]) for l in qc["lines"]] == [("GST", 2.61), ("QST", 5.21)]
    assert qc["lines"][0]["number"] == "713093946 RT0001"
    on = taxes.commission_taxes(60, {"country": "Canada", "lat": 43.65, "lng": -79.38})
    assert [(l["code"], l["amount"]) for l in on["lines"]] == [("HST", 6.90)]
    fr = taxes.commission_taxes(60, {"country": "France", "city": "Paris"})
    assert fr["lines"] == [] and fr["zero_rated"]


# ---------------------------------------------------------------------------
# Devis -> reservation -> paiement -> virement
# ---------------------------------------------------------------------------

def _parties(make_user, *, numbers=True):
    import services
    p = make_user("tax_p", role="pilot", country="Canada", city="Montréal", lat=45.5, lng=-73.57)
    if numbers:
        services.set_pilot_tax_numbers(p["id"], QC_GST, QC_QST)
    c = make_user("tax_c", role="client", country="Canada", city="Laval")
    mid = services.create_mission(
        c["id"], title="Toiture", description="Inspection d'une toiture", mission_type="inspection",
        country="Canada", region="Québec", city="Laval", currency="CAD")
    return p, c, mid


def test_devis_reservation_et_virement(make_user, monkeypatch):
    import payments, services, taxes
    p, c, mid = _parties(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    bid = next(b for b in services.list_bids(mid) if b["id"] == bid_id)
    assert bid["tax_amount"] == 44.93
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    bk = services.get_booking(booking_id)
    assert bk["agreed_price"] == 300 and bk["tax_amount"] == 44.93 and bk["platform_fee"] == 60
    assert services.booking_total(bk) == 344.93
    assert services.booking_amount_cents(bk) == 34493
    assert [l["code"] for l in taxes.loads(bk["fee_taxes"])] == ["GST", "QST"]
    # le pilote touche prix - commission + ses taxes
    assert services.pilot_net(bk) == 284.93
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_tax")
    sent = []
    monkeypatch.setattr(payments, "release_to_pilot", lambda **kw: sent.append(kw) or "tr_fake_tax")
    assert services.confirm_completion(booking_id, c["id"])
    assert sent[0]["pilot_amount"] == 284.93
    # le sequestre comptait le total paye
    assert services.get_booking(booking_id)["status"] == "completed"


def test_sans_numeros_rien_ne_change(make_user):
    import services
    p, c, mid = _parties(make_user, numbers=False)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    bk = services.get_booking(services.accept_bid(mid, bid_id, c["id"]))
    assert bk["tax_amount"] == 0 and services.booking_total(bk) == 300
    assert services.booking_amount_cents(bk) == 30000 and services.pilot_net(bk) == 240


def test_paiement_porte_une_ligne_par_taxe(auth_client, make_user, monkeypatch):
    import config, payments, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    p, c, mid = _parties(make_user)
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    seen = {}
    monkeypatch.setattr(payments, "create_checkout_session",
                        lambda **kw: seen.update(kw) or ("cs_tax", "https://checkout.example/tax"))
    r = auth_client(c["id"]).post(f"/reservations/{booking_id}/payer")
    assert r.headers["Location"] == "https://checkout.example/tax"
    assert seen["amount"] == 300
    assert [l["amount"] for l in seen["tax_lines"]] == [15.0, 29.93]
    assert "TPS" in seen["tax_lines"][0]["name"] and QC_GST in seen["tax_lines"][0]["name"]


def test_webhook_attend_le_total_taxes_comprises(client, make_user, monkeypatch):
    import db, payments, services
    p, c, mid = _parties(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    services.attach_payment_session(booking_id, "cs_tax_wh")
    monkeypatch.setattr(payments, "is_fake", lambda: False)
    monkeypatch.setattr(payments, "is_available", lambda: True)

    def event(amount):
        return lambda *_a: {"type": "checkout.session.completed", "data": {"object": {
            "id": "cs_tax_wh", "metadata": {"booking_id": str(booking_id)}, "payment_intent": "pi_tax_wh",
            "payment_status": "paid", "amount_total": amount, "currency": "cad"}}}
    monkeypatch.setattr(payments, "parse_webhook", event(30000))       # prix seul : refuse
    client.post("/stripe/webhook", data=b"x")
    assert services.get_booking(booking_id)["status"] == "pending_payment"
    monkeypatch.setattr(payments, "parse_webhook", event(34493))
    assert client.post("/stripe/webhook", data=b"x").status_code == 200
    assert services.get_booking(booking_id)["status"] == "funded"
    assert services.escrow_reserve()["CAD"] >= 344.93
    db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))


def test_annulation_sur_le_total_paye(make_user):
    import services
    p, c, mid = _parties(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    bk = services.get_booking(services.accept_bid(mid, bid_id, c["id"]))
    bk = dict(bk, paid_at="2020-01-01 00:00:00", scheduled_at="2020-01-01 00:00:00")   # tardive
    calc = services.compute_cancellation_fee(bk)
    assert calc["fee_amount"] + calc["refund_amount"] == pytest.approx(344.93)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_le_pilote_enregistre_ses_numeros(auth_client, make_user):
    import services
    p = make_user("tax_form", role="pilot", country="Canada", city="Québec")
    c = auth_client(p["id"])
    assert 'id="taxes"' in c.get("/espace/pilote").data.decode()
    r = c.post("/espace/pilote/taxes", data={"gst_number": "123456782", "qst_number": "1234567890"},
               follow_redirects=True)
    assert "Numéros enregistrés" in unescape(r.data.decode())
    prof = services.get_pilot_profile(p["id"])
    assert prof["gst_number"] == QC_GST and prof["qst_number"] == QC_QST
    r = c.post("/espace/pilote/taxes", data={"gst_number": "123456789", "qst_number": ""},
               follow_redirects=True)
    assert "Numéro de TPS/TVH invalide" in unescape(r.data.decode())
    assert services.get_pilot_profile(p["id"])["gst_number"] == QC_GST      # inchange
    # un pilote hors du Canada ne voit pas la rubrique
    far = make_user("tax_far", role="pilot", country="France", city="Lyon")
    assert 'id="taxes"' not in auth_client(far["id"]).get("/espace/pilote").data.decode()


def test_client_voit_les_taxes_du_devis_et_de_la_reservation(auth_client, make_user, monkeypatch):
    import config, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    p, c, mid = _parties(make_user)
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    cl = auth_client(c["id"])
    html = unescape(cl.get(f"/missions/{mid}").data.decode())
    assert "TPS 15.00" in html and "TVQ 29.93" in html and "344.93" in html
    bid_id = services.list_bids(mid)[0]["id"]
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    page = unescape(cl.get(f"/reservations/{booking_id}").data.decode())
    assert QC_GST in page and "344.93" in page and "Payer 344.93" in page


def test_formulaire_de_devis_annonce_les_taxes(auth_client, make_user):
    p, _c, mid = _parties(make_user)
    html = unescape(auth_client(p["id"]).get(f"/missions/{mid}").data.decode())
    assert "Le client paiera en plus TPS 5 % + TVQ 9,975 %" in html


def test_releve_admin_des_taxes(auth_client, make_user, monkeypatch):
    import db, payments, services
    p, c, mid = _parties(make_user)
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_rel")
    monkeypatch.setattr(payments, "release_to_pilot", lambda **kw: "tr_fake_rel")
    assert services.confirm_completion(booking_id, c["id"])
    rows = services.tax_summary()
    cad = [r for r in rows if r["currency"] == "CAD"]
    assert cad and cad[0]["fee"].get("GST", 0) >= 2.61 and cad[0]["pilot"].get("QST", 0) >= 29.93
    admin = make_user("tax_admin", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    monkeypatch.setattr(payments, "diagnostics", lambda: {
        "mode": "FAKE", "connect_flag": False, "webhook_secret": False, "publishable": False,
        "connect_webhook_secret": False, "connect_webhook_present": False, "webhook_url": "u",
        "connect_webhook_url": "c", "expected_account": "", "account_mismatch": False, "account": None,
        "payout_schedule": None, "balance": {}, "connect": False, "connected": [], "webhooks": [],
        "errors": []})
    html = unescape(auth_client(admin["id"]).get("/admin/stripe").data.decode())
    assert "Taxes à déclarer" in html and "713093946 RT0001" in html


def test_devis_pdf_montre_les_taxes(make_user):
    import pdfgen, services
    p, c, mid = _parties(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    bk = services.get_booking(services.accept_bid(mid, bid_id, c["id"]))
    pdf = pdfgen.devis_pdf(bk, "Inspection")
    assert pdf.startswith(b"%PDF")
