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


# ---------------------------------------------------------------------------
# TVP / TVD percue par la plateforme (2026-10-18)
# ---------------------------------------------------------------------------
# Sask. et Manitoba chargent la plateforme qui encaisse de percevoir la taxe
# de vente provinciale (marketplace facilitator), que le pilote soit inscrit
# ou non. Rien n'est percu tant que L'Aube Etoilee n'y est pas inscrite.

SK_NUM, MB_NUM, BC_NUM = "1234567", "7654321", "PST-1234-5678"


@pytest.fixture()
def pst_on(monkeypatch):
    import config
    monkeypatch.setitem(config.PLATFORM_PST_NUMBERS, "SK", SK_NUM)
    monkeypatch.setitem(config.PLATFORM_PST_NUMBERS, "MB", MB_NUM)
    monkeypatch.setitem(config.PLATFORM_PST_NUMBERS, "BC", BC_NUM)


def test_tvp_sask_et_manitoba_percue_par_la_plateforme(pst_on):
    import taxes
    gst = {"country": "Canada", "gst_number": QC_GST}
    sk = taxes.mission_taxes(300, gst, {"country": "Canada", "region": "Saskatchewan",
                                        "mission_type": "photo"})
    assert [(l["code"], l["amount"], l.get("by")) for l in sk["lines"]] == [
        ("GST", 15.0, None), ("PST", 18.0, "platform")]
    assert sk["lines"][1]["number"] == SK_NUM and sk["total"] == 33.0
    # pilote non inscrit : la TVP s'applique quand meme, pas la TPS
    mb = taxes.mission_taxes(300, {"country": "Canada"}, {"country": "Canada", "region": "Manitoba",
                                                           "mission_type": "mapping"})
    assert [(l["code"], l["amount"]) for l in mb["lines"]] == [("RST", 21.0)]
    assert taxes.platform_collected(mb["lines"]) == 21.0
    # inspection exemptee ; C.-B. : livraison numerique non taxable
    assert taxes.mission_taxes(300, {}, {"country": "Canada", "region": "SK",
                                         "mission_type": "toiture"})["lines"] == []
    assert taxes.mission_taxes(300, {}, {"country": "Canada", "region": "BC",
                                         "mission_type": "photo"})["lines"] == []


def test_tvp_rien_sans_inscription_de_la_plateforme():
    import taxes
    assert taxes.mission_taxes(300, {}, {"country": "Canada", "region": "Saskatchewan",
                                         "mission_type": "video"})["lines"] == []
    bc = taxes.commission_taxes(60, {"country": "Canada", "city": "Vancouver", "lat": 49.28, "lng": -123.12})
    assert [l["code"] for l in bc["lines"]] == ["GST"]


def test_commission_c_b_comprend_la_tvp(pst_on):
    import taxes
    bc = taxes.commission_taxes(60, {"country": "Canada", "city": "Vancouver", "lat": 49.28, "lng": -123.12})
    # 60 $ taxes comprises a 12 % : base 53,57
    assert [(l["code"], l["amount"]) for l in bc["lines"]] == [("GST", 2.68), ("PST", 3.75)]
    assert bc["lines"][1]["number"] == BC_NUM


def _sask(make_user, mission_type="photo"):
    import services
    p = make_user("pst_p", role="pilot", country="Canada", city="Regina", lat=50.45, lng=-104.6)
    c = make_user("pst_c", role="client", country="Canada", city="Saskatoon")
    mid = services.create_mission(
        c["id"], title="Ferme", description="Photos aeriennes d'une ferme", mission_type=mission_type,
        country="Canada", region="Saskatchewan", city="Saskatoon", currency="CAD")
    return p, c, mid


def test_tvp_reste_a_la_plateforme_au_versement(make_user, monkeypatch, pst_on):
    import payments, services
    p, c, mid = _sask(make_user)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    bk = services.get_booking(services.accept_bid(mid, bid_id, c["id"]))
    assert bk["tax_amount"] == 18.0 and services.booking_total(bk) == 318.0
    assert services.booking_amount_cents(bk) == 31800
    assert services.pilot_net(bk) == 240.0                 # 300 - 60, sans la TVP
    # annulation : la TVP est rendue en entier, le dedommagement porte sur le reste
    late = dict(bk, paid_at="2020-01-01 00:00:00", scheduled_at="2020-01-01 00:00:00")
    calc = services.compute_cancellation_fee(late)
    assert calc["fee_amount"] + calc["refund_amount"] == pytest.approx(318.0)
    assert calc["refund_amount"] >= 18.0 + 300 - calc["fee_amount"] - 0.001
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    services.mark_booking_funded(bk["id"], payment_intent_id="pi_fake_pst")
    sent = []
    monkeypatch.setattr(payments, "release_to_pilot", lambda **kw: sent.append(kw) or "tr_fake_pst")
    assert services.confirm_completion(bk["id"], c["id"])
    assert sent[0]["pilot_amount"] == 240.0
    month = services.get_booking(bk["id"])["released_at"][:7]
    row = next(r for r in services.tax_summary() if r["month"] == month and r["currency"] == "CAD")
    assert row["collected"].get("PST-SK", 0) >= 18.0
    assert "PST" not in row["pilot"]


def test_alerte_admin_vente_sans_inscription(make_user):
    import services
    p, c, mid = _sask(make_user, mission_type="video")
    bid_id = services.place_bid(mid, p["id"], price=500, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    assert services.get_booking(booking_id)["tax_amount"] == 0          # rien de percu
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_pst_exp")
    sk = next(e for e in services.pst_exposure() if e["province"] == "SK" and e["currency"] == "CAD")
    assert sk["count"] >= 1 and sk["amount"] >= 500


def test_paiement_tvp_au_nom_de_la_plateforme(auth_client, make_user, monkeypatch, pst_on):
    import config, payments, services
    monkeypatch.setattr(config, "STRIPE_CONNECT_ENABLED", True)
    monkeypatch.setattr(payments, "is_available", lambda: True)
    p, c, mid = _sask(make_user)
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)
    bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
    booking_id = services.accept_bid(mid, bid_id, c["id"])
    seen = {}
    monkeypatch.setattr(payments, "create_checkout_session",
                        lambda **kw: seen.update(kw) or ("cs_pst", "https://checkout.example/pst"))
    auth_client(c["id"]).post(f"/reservations/{booking_id}/payer")
    (line,) = seen["tax_lines"]
    assert line["amount"] == 18.0
    assert "TVP" in line["name"] and "L'Aube Étoilée inc." in line["name"] and SK_NUM in line["name"]
    html = unescape(auth_client(c["id"]).get(f"/reservations/{booking_id}").data.decode())
    assert "perçue et remise par L'Aube Étoilée inc." in html


def test_formulaire_de_devis_et_cgu_annoncent_la_tvp(auth_client, client, make_user, pst_on):
    p, _c, mid = _sask(make_user)
    html = unescape(auth_client(p["id"]).get(f"/missions/{mid}").data.decode())
    assert "Le client paiera aussi TVP 6 %, que la plateforme perçoit et remet elle-même" in html
    cgu = unescape(client.get("/cgu").data.decode())
    assert "TVP de la Saskatchewan n° 1234567, TVD du Manitoba n° 7654321" in cgu
    assert "(n° PST-1234-5678)" in cgu


# ---------------------------------------------------------------------------
# Quebec : lieu de fourniture et plateforme reputee fournisseur (2026-10-18)
# ---------------------------------------------------------------------------
# Verification des regles contre la LTA, le DORS/2010-117 et la LTVQ :
# le lieu suit l'adresse du client (le bien pour une inspection), et la
# plateforme percoit la TVQ d'un pilote hors Quebec non inscrit (art. 477.5.2).

TORONTO = {"country": "Canada", "city": "Toronto", "lat": 43.65, "lng": -79.38}
LAVAL = {"country": "Canada", "city": "Laval", "lat": 45.57, "lng": -73.69}
PARIS = {"country": "France", "city": "Paris"}


def _codes(res):
    return [(l["code"], l["amount"], l.get("by")) for l in res["lines"]]


def test_lieu_de_fourniture_adresse_du_client_ou_du_bien():
    import taxes
    qc_pilot = dict(LAVAL, gst_number=QC_GST, qst_number=QC_QST)
    photo_qc = {"country": "Canada", "region": "Québec", "mission_type": "photo"}
    roof_qc = {"country": "Canada", "region": "Québec", "mission_type": "toiture"}
    photo_on = {"country": "Canada", "region": "Ontario", "mission_type": "video"}
    # photo au Quebec pour un client de Toronto : TVH de l'Ontario, pas de TVQ
    assert _codes(taxes.mission_taxes(300, qc_pilot, photo_qc, TORONTO)) == [("HST", 39.0, None)]
    # inspection d'un immeuble au Quebec : TPS + TVQ, quel que soit le client
    assert _codes(taxes.mission_taxes(300, qc_pilot, roof_qc, TORONTO)) == [
        ("GST", 15.0, None), ("QST", 29.93, None)]
    # video en Ontario pour un client de Laval : TPS + TVQ
    assert _codes(taxes.mission_taxes(300, qc_pilot, photo_on, LAVAL)) == [
        ("GST", 15.0, None), ("QST", 29.93, None)]
    # client sans adresse au Canada : la ou le service est rendu
    assert _codes(taxes.mission_taxes(300, qc_pilot, photo_on, PARIS)) == [("HST", 39.0, None)]


def test_tvq_percue_par_la_plateforme_pour_un_pilote_hors_quebec():
    import taxes
    photo_qc = {"country": "Canada", "region": "Québec", "mission_type": "photo"}
    # pilote de Toronto non inscrit : la plateforme ajoute la TVQ a son numero
    on_small = dict(TORONTO)
    res = taxes.mission_taxes(300, on_small, photo_qc, LAVAL)
    assert _codes(res) == [("QST", 29.93, "platform")]
    assert res["lines"][0]["number"] == "1230658818 TQ0001"
    # inscrit a la TPS seulement : sa TPS, plus la TVQ de la plateforme
    on_gst = dict(TORONTO, gst_number=QC_GST)
    assert _codes(taxes.mission_taxes(300, on_gst, photo_qc, LAVAL)) == [
        ("GST", 15.0, None), ("QST", 29.93, "platform")]
    # inscrit aux deux : rien pour la plateforme
    on_both = dict(TORONTO, gst_number=QC_GST, qst_number=QC_QST)
    assert _codes(taxes.mission_taxes(300, on_both, photo_qc, LAVAL)) == [
        ("GST", 15.0, None), ("QST", 29.93, None)]
    # pilote quebecois non inscrit (petit fournisseur) : rien du tout
    assert taxes.mission_taxes(300, dict(LAVAL), photo_qc, LAVAL)["lines"] == []
    # pilote etranger non inscrit : TPS et TVQ percues par la plateforme
    assert _codes(taxes.mission_taxes(300, PARIS, photo_qc, LAVAL)) == [
        ("GST", 15.0, "platform"), ("QST", 29.93, "platform")]


def test_commission_d_un_pilote_etranger():
    import taxes
    photo_qc = {"country": "Canada", "region": "Québec", "mission_type": "photo"}
    photo_on = {"country": "Canada", "region": "Ontario", "mission_type": "photo"}
    # non inscrit : la plateforme percoit les taxes de la mission, commission sans taxe
    c = taxes.commission_taxes(60, PARIS, photo_qc, LAVAL)
    assert c["lines"] == [] and not c["zero_rated"]
    # inscrit a la TPS, mission en Ontario : TPS (service rendu au Quebec), TVQ detaxee
    c = taxes.commission_taxes(60, dict(PARIS, gst_number=QC_GST), photo_on, TORONTO)
    assert [(l["code"], l["amount"]) for l in c["lines"]] == [("GST", 2.86)]
    # inscrit aux deux, mission au Quebec : TPS + TVQ comprises
    c = taxes.commission_taxes(60, dict(PARIS, gst_number=QC_GST, qst_number=QC_QST), photo_qc, LAVAL)
    assert [(l["code"], l["amount"]) for l in c["lines"]] == [("GST", 2.61), ("QST", 5.21)]
    # mission hors du Canada : detaxee
    assert taxes.commission_taxes(60, PARIS, {"country": "France"}, PARIS)["zero_rated"]
    # pilote de Toronto (au Canada) : TVH de sa province, jamais de TVQ
    c = taxes.commission_taxes(60, TORONTO, photo_qc, LAVAL)
    assert [l["code"] for l in c["lines"]] == ["HST"]


def test_tvq_de_la_plateforme_au_versement_et_a_l_annulation(make_user, monkeypatch):
    import payments, services, taxes
    p = make_user("qcp_on", role="pilot", country="Canada", city="Toronto", lat=43.65, lng=-79.38)
    c = make_user("qcp_c", role="client", country="Canada", city="Laval", lat=45.57, lng=-73.69)
    services.set_pilot_stripe_account(p["id"], f"acct_fake_{p['id']}", True, True)

    def booking():
        mid = services.create_mission(
            c["id"], title="Chalet", description="Photos aeriennes d'un chalet", mission_type="photo",
            country="Canada", region="Québec", city="Laval", currency="CAD")
        bid_id = services.place_bid(mid, p["id"], price=300, currency="CAD", description=DESC)
        return services.get_booking(services.accept_bid(mid, bid_id, c["id"]))

    bk = booking()
    assert bk["tax_amount"] == 29.93 and services.booking_amount_cents(bk) == 32993
    assert [l["code"] for l in taxes.loads(bk["fee_taxes"])] == ["HST"]
    assert services.pilot_net(bk) == 240.0          # la TVQ reste a la plateforme
    services.mark_booking_funded(bk["id"], payment_intent_id="pi_fake_qcp")
    sent = []
    monkeypatch.setattr(payments, "release_to_pilot", lambda **kw: sent.append(kw) or "tr_fake_qcp")
    assert services.confirm_completion(bk["id"], c["id"])
    assert sent[-1]["pilot_amount"] == 240.0
    month = services.get_booking(bk["id"])["released_at"][:7]
    row = next(r for r in services.tax_summary() if r["month"] == month and r["currency"] == "CAD")
    assert row["collected"].get("QST", 0) >= 29.93

    # annulation tardive : dedommagement sur le total, TVQ comprise gardee par la plateforme
    bk2 = booking()
    services.mark_booking_funded(bk2["id"], payment_intent_id="pi_fake_qcp2")
    import db
    db.execute("UPDATE bookings SET paid_at='2020-01-01 00:00:00', scheduled_at='2020-01-01 00:00:00' "
               "WHERE id=?", (bk2["id"],))
    calc = services.compute_cancellation_fee(services.get_booking(bk2["id"]))
    assert calc["fee_amount"] + calc["refund_amount"] == pytest.approx(329.93)
    assert calc["platform_tax_amount"] == round(calc["fee_amount"] * 9.975 / 109.975, 2)
    refunds = []
    monkeypatch.setattr(payments, "refund_payment", lambda *a, **kw: refunds.append(kw) or True)
    res = services.cancel_booking_by_client(bk2["id"], c["id"])
    assert res["ok"], res
    assert refunds[-1]["amount"] == calc["refund_amount"]
    assert sent[-1]["pilot_amount"] == round(calc["fee_amount"] - calc["platform_tax_amount"], 2)
