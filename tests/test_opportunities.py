"""Opportunités : appels d'offres publics repris de données ouvertes."""
import json

import opportunities as opp

CSV = '﻿"title-titre-eng","title-titre-fra","referenceNumber-numeroReference","publicationDate-datePublication","tenderClosingDate-appelOffresDateCloture","regionsOfDelivery-regionsLivraison-fra","contractingEntityName-nomEntitContractante-fra","contractingEntityAddressCity-entiteContractanteAdresseVille-fra","noticeType-avisType-fra","tenderDescription-descriptionAppelOffres-eng","tenderDescription-descriptionAppelOffres-fra","gsinDescription-nibsDescription-fra","unspscDescription-eng"\n' \
      '"Aerial LiDAR survey","Levé LiDAR aéroporté","cb-26-1","2026-09-10","2026-10-15T14:00:00","*Canada\n*Québec (sauf RCN)","Ressources naturelles Canada","Sherbrooke","Demande de propositions","Acquisition of airborne lidar data over the region. ' + "x" * 600 + '","Acquisition de données lidar aéroportées sur la région.","",""\n' \
      '"Office chairs","Chaises de bureau","cb-26-2","2026-09-10","2026-10-15T14:00:00","*Ontario","SPAC","Ottawa","Demande de propositions","Chairs","Chaises","",""\n'


def test_parse_canadabuys_filtre_et_resume():
    items = opp.parse_canadabuys(CSV)
    assert [i["source_ref"] for i in items] == ["cb-26-1"]
    it = items[0]
    assert it["region"] == "Québec" and it["regions_raw"] == "Canada, Québec (sauf RCN)"
    assert it["closes_at"] == "2026-10-15" and it["published_at"] == "2026-09-10"
    assert it["url_fr"].endswith("/avis-dappel-doffres/cb-26-1") and "/en/" in it["url_en"]
    assert it["specialties"] == "topographie"
    assert len(it["summary_en"]) <= opp.SUMMARY_CHARS + 1 and it["summary_en"].endswith("…")


def test_normalize_region():
    assert opp.normalize_region("*Canada\n*Ontario (sauf RCN)")[0] == "Ontario"
    assert opp.normalize_region("*Agassiz\n*Colombie-Britannique")[0] == "Colombie-Britannique"
    assert opp.normalize_region("*Wainwright")[0] == "Alberta"
    assert opp.normalize_region("*Canada")[0] == "Canada"
    assert opp.normalize_region("QC")[0] == "Québec"


def _seao_payload():
    return {"releases": [
        {"ocid": "ocds-1", "tag": ["tender"], "date": "2026-09-08T14:00:00-04:00",
         "buyer": {"name": "Ministère des Ressources naturelles"},
         "parties": [{"name": "MRNF", "roles": ["buyer"], "address": {"locality": "Québec", "region": "QC"}}],
         "tender": {"title": "Levés laser aéroporté (Lidar) et photogrammétrie", "status": "active",
                    "tenderPeriod": {"startDate": "2026-09-08", "endDate": "2026-09-24T12:00:00-04:00"},
                    "documents": [{"url": "https://seao.gouv.qc.ca/avis/1"}], "items": [{"description": "Services de levés"}]}},
        {"ocid": "ocds-2", "tag": ["tenderUpdate"], "tender": {"title": "Location drones", "status": "complete", "items": []}},
        {"ocid": "ocds-3", "tag": ["tender"], "tender": {"title": "Implants orthopédiques", "status": "active", "items": []}},
    ]}


def test_parse_seao():
    items, closed = opp.parse_seao(_seao_payload())
    assert [i["source_ref"] for i in items] == ["ocds-1"] and closed == ["ocds-2"]
    it = items[0]
    assert it["region"] == "Québec" and it["city"] == "Québec" and it["closes_at"] == "2026-09-24"
    assert it["url_fr"] == "https://seao.gouv.qc.ca/avis/1" and "3d" in it["specialties"]


def test_collecte_page_dashboard_admin_et_digest(client, auth_client, make_user, monkeypatch):
    import db, services, mailer
    from config import DATA_DIR
    monkeypatch.setattr(opp, "STATE_FILE", DATA_DIR + "/opp_state_test.json")
    resources = {"result": {"resources": [{"name": "hebdo_20260907_20260913.json", "url": "seao://hebdo"}]}}
    def fake_fetch(url, timeout=120):
        if url == opp.CANADABUYS_CSV:
            return CSV.encode("utf-8")
        if url == opp.SEAO_PACKAGE:
            return json.dumps(resources).encode("utf-8")
        if url == "seao://hebdo":
            return json.dumps(_seao_payload()).encode("utf-8")
        raise AssertionError(url)
    monkeypatch.setattr(opp, "_fetch", fake_fetch)
    report = opp.collect()
    assert report["canadabuys"]["items"] == 1 and report["seao"]["items"] == 1 and report["published"] >= 2
    # deuxième passe : rien ne casse, le fichier SEAO déjà lu n'est pas relu
    report2 = opp.collect()
    assert report2["seao"]["files"] == 0 and report2["published"] == report["published"]

    html = client.get("/opportunites").get_data(as_text=True)
    assert "Levé LiDAR aéroporté" in html and "Levés laser aéroporté" in html
    assert "canadabuys.canada.ca/fr/appels-doffres/avis-dappel-doffres/cb-26-1" in html
    assert "Chaises de bureau" not in html
    assert "Aerial LiDAR survey" in client.get("/en/opportunites?region=Québec").get_data(as_text=True)
    client.set_cookie("aube_lang", "fr", domain="localhost.localdomain")   # la visite de /en/ a pose le cookie
    assert "Levé LiDAR" not in client.get("/opportunites?region=Ontario").get_data(as_text=True)
    assert "Levé LiDAR" in client.get("/opportunites?mission_type=topographie").get_data(as_text=True)
    assert "/opportunites" in client.get("/sitemap-fr.xml").get_data(as_text=True)

    # Tableau de bord : un pilote canadien voit le bloc, un pilote français non.
    with client.application.app_context():
        ca = make_user("opp_ca", role="pilot", country="Canada"); services.upsert_pilot_profile(ca["id"], is_available=1)
        fr = make_user("opp_fr", role="pilot", country="France"); services.upsert_pilot_profile(fr["id"], is_available=1)
    assert "Levé LiDAR" in auth_client(ca["id"]).get("/espace").get_data(as_text=True)
    assert "Levé LiDAR" not in auth_client(fr["id"]).get("/espace").get_data(as_text=True)

    # Admin : masquer retire la fiche du site, et la collecte suivante ne la republie pas.
    with client.application.app_context():
        admin = make_user("opp_admin", role="client"); db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
        opp_id = db.fetchone("SELECT id FROM opportunities WHERE source_ref='cb-26-1'")["id"]
    a = auth_client(admin["id"])
    assert "Levé LiDAR" in a.get("/admin/opportunites").get_data(as_text=True)
    a.post(f"/admin/opportunites/{opp_id}/statut", data={"status": "hidden"})
    assert "Levé LiDAR aéroporté" not in client.get("/opportunites").get_data(as_text=True)
    opp.collect()
    with client.application.app_context():
        assert db.fetchone("SELECT status FROM opportunities WHERE id=?", (opp_id,))["status"] == "hidden"
        # Digest : destinataires = pilotes du pays avec alertes ; le courriel se rend.
        pilots = services.pilots_for_opportunity_digest("Canada")
        assert any(p["id"] == ca["id"] for p in pilots) and not any(p["id"] == fr["id"] for p in pilots)
        items = opp.recent_for_digest(7)
        assert items and all(i["status"] == "published" for i in items)
        assert mailer.send_opportunity_digest([{"email": "opp_ca@aubemail.com", "full_name": "Test Pilote"}], items) == 1


def test_expiration_des_avis_clos(app_ctx):
    import db
    db.execute("INSERT OR REPLACE INTO opportunities (source, source_ref, kind, title_fr, title_en, url_fr, closes_at, status, first_seen_at, last_seen_at) "
               "VALUES ('seao','old','tender','Vieil avis','Old notice','https://x/','2020-01-01','published','2020-01-01 00:00:00','2020-01-01 00:00:00')")
    with db.standalone() as conn:
        assert opp.expire(conn) >= 1
    assert db.fetchone("SELECT status FROM opportunities WHERE source_ref='old'")["status"] == "closed"
