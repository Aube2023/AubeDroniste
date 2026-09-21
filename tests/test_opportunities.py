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
    assert it["url_fr"].endswith("/appels-d-offres/cb-26-1") and "/en/" in it["url_en"]
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


AU_XML = b"""<rss><channel><item><title>DAF-2026-44: Aerial LiDAR survey of floodplains</title><link>https://www.tenders.gov.au/Atm/Show/ae88</link><description>&lt;p&gt;Agency: Department of Agriculture. Capture airborne LiDAR and orthophoto imagery by drone over New South Wales floodplains. Close Date: 15-Oct-2026 2:00 pm&lt;/p&gt;</description><pubDate>Tue, 25 Aug 2026 00:00:00 GMT</pubDate></item>
<item><title>DFAT-1111: Mid-Term Review</title><link>https://www.tenders.gov.au/Atm/Show/x</link><description>evaluation team</description></item></channel></rss>"""
NZ_XML = b"""<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>RFP 2026-77 Drone inspection of bridges</title><link>https://www.gets.govt.nz//NZTA/ExternalTenderDetails.htm?id=34580891</link><description>&lt;table&gt;&lt;tr&gt;&lt;td&gt;Organisation: &lt;/td&gt;&lt;td&gt;Waka Kotahi&lt;/td&gt;&lt;/tr&gt;&lt;tr&gt;&lt;td&gt;Close date: &lt;/td&gt;&lt;td&gt;Thursday, 15 October 2026 4:00 PM&lt;/td&gt;&lt;/tr&gt;&lt;tr&gt;&lt;td&gt;Region: &lt;/td&gt;&lt;td&gt;Canterbury&lt;/td&gt;&lt;/tr&gt;&lt;tr&gt;&lt;td&gt;Overview: &lt;/td&gt;&lt;td&gt;UAV-based inspection of 40 bridges with photogrammetry deliverables.&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</description><dc:date>2026-08-03T20:00:00Z</dc:date></item></channel></rss>"""


JB_EN = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en"><title><![CDATA[drone - Job Bank]]></title>
<entry><title type="html"><![CDATA[drone technician]]></title><link rel="alternate" type="text/html" href="https://www.jobbank.gc.ca/jobsearch/jobposting/50315643"/><id>https://www.jobbank.gc.ca/jobsearch/jobSearchRSSfeed?id=3670001</id><updated>2026-09-18T20:19:00Z</updated>
<summary type="html"><![CDATA[<strong>Job number:</strong> 3670001<br /><strong>Location:</strong> Saint-Hubert (QC)  <br /><strong>Employer:</strong> Drones Aube Inc.<br /><strong>Salary:</strong> $32.00 hourly]]></summary></entry>
<entry><title type="html"><![CDATA[land survey technician]]></title><link rel="alternate" type="text/html" href="https://www.jobbank.gc.ca/jobsearch/jobposting/50329244"/><updated>2026-09-20T20:06:00Z</updated>
<summary type="html"><![CDATA[<strong>Job number:</strong> 3677210<br /><strong>Location:</strong> Alma (QC)  <br /><strong>Employer:</strong> Arpentage GTG Inc<br /><strong>Salary:</strong> $1,851.60 weekly]]></summary></entry>
<entry><title type="html"><![CDATA[unmanned aerial vehicle (UAV) technician]]></title><link rel="alternate" type="text/html" href="https://www.jobbank.gc.ca/jobsearch/jobposting/50301957"/><updated>2026-09-17T10:00:00Z</updated>
<summary type="html"><![CDATA[<strong>Job number:</strong> 3660002<br /><strong>Location:</strong> Various locations<br /><strong>Employer:</strong> SkyOps Ltd.<br /><strong>Salary:</strong> $70,000.00 annually]]></summary></entry>
</feed>"""
JB_FR = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="fr">
<entry><title type="html"><![CDATA[technicien/technicienne de drones]]></title><link rel="alternate" type="text/html" href="https://www.guichetemplois.gc.ca/jobsearch/jobposting/50315643"/><updated>2026-09-18T20:19:00Z</updated>
<summary type="html"><![CDATA[<strong>Numéro de l’offre :</strong> 3670001<br /><strong>Emplacement :</strong> Saint-Hubert (QC)  <br /><strong>Employeur :</strong> Drones Aube Inc.<br /><strong>Salaire :</strong> 32,00 $ de l'heure]]></summary></entry>
</feed>""".encode("utf-8")


def _adzuna_payload():
    return {"count": 3, "results": [
        {"id": "5001", "title": "Drone Pilot / Surveyor", "description": "Fly RPAS for LiDAR and photogrammetry surveys across Alberta. " * 3,
         "created": "2026-09-19T08:00:00Z", "redirect_url": "https://www.adzuna.ca/land/ad/5001?se=x",
         "company": {"display_name": "Prairie Geomatics"}, "location": {"display_name": "Calgary, Alberta", "area": ["Canada", "Alberta", "Calgary"]},
         "contract_type": "permanent", "contract_time": "full_time", "salary_min": 65000, "salary_max": 80000, "salary_is_predicted": "0"},
        {"id": "5002", "title": "Counter-drone systems engineer", "description": "C-UAS radar", "created": "2026-09-19T08:00:00Z",
         "redirect_url": "https://www.adzuna.ca/land/ad/5002", "company": {"display_name": "X"}, "location": {"area": ["Canada"]}},
        {"id": "5003", "title": "Warehouse associate", "description": "Pack drones for shipping. Drones drones.", "created": "2026-09-19T08:00:00Z",
         "redirect_url": "https://www.adzuna.ca/land/ad/5003", "company": {"display_name": "Y"}, "location": {"area": ["Canada", "Ontario"]}},
    ]}


def test_parse_emplois_jobbank_et_adzuna():
    items = opp.parse_jobbank(opp._atom_entries(JB_EN), opp._atom_entries(JB_FR))
    assert [i["source_ref"] for i in items] == ["50301957", "50315643"]   # l'arpenteur sans drone est écarté
    it = items[1]
    assert it["kind"] == "job" and it["title_fr"] == "Technicien/technicienne de drones" and it["title_en"] == "Drone technician"
    assert it["region"] == "Québec" and it["city"] == "Saint-Hubert" and it["org"] == "Drones Aube Inc."
    assert it["summary_fr"] == "Salaire : 32,00 $ de l'heure" and it["summary_en"] == "Salary: $32.00 hourly"
    assert it["url_fr"].startswith("https://www.guichetemplois.gc.ca/") and it["url_en"].startswith("https://www.jobbank.gc.ca/")
    assert it["published_at"] == "2026-09-18" and it["closes_at"] == "2026-10-18"
    # sans entrée française : titre anglais des deux côtés, lien FR déduit du numéro
    assert items[0]["title_fr"] == items[0]["title_en"] and items[0]["region"] == "Canada" and "guichetemplois" in items[0]["url_fr"]

    jobs = opp.parse_adzuna(_adzuna_payload(), "Canada")
    assert [j["source_ref"] for j in jobs] == ["5001"]   # anti-drone et « drones » dans le corps seulement : écartés
    j = jobs[0]
    assert j["region"] == "Alberta" and j["city"] == "Calgary" and j["org"] == "Prairie Geomatics"
    assert j["notice_type"] == "permanent, full time" and j["summary_fr"].endswith("(65,000–80,000)")
    assert "topographie" in j["specialties"] and j["closes_at"] == "2026-10-19"


def _secop_rows():
    return [{"nombre_del_procedimiento": "LEVANTAMIENTO TOPOGRAFICO CON DRON Y FOTOGRAMETRIA", "descripci_n_del_procedimiento": "Levantamiento aereo con dron RTK",
             "id_del_proceso": "CO1.REQ.1", "entidad": "ALCALDIA DE MEDELLIN", "departamento_entidad": "Antioquia", "ciudad_entidad": "Medellín",
             "urlproceso": {"url": "https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID=CO1.NTC.1"},
             "modalidad_de_contratacion": "Mínima cuantía", "fecha_de_publicacion_del": "2026-09-10T00:00:00.000", "fecha_de_recepcion_de": "2026-09-25T00:00:00.000"},
            {"nombre_del_procedimiento": "COMPRA DE SILLAS", "descripci_n_del_procedimiento": "sillas", "id_del_proceso": "CO1.REQ.2", "urlproceso": {"url": "x"}}]


def test_parse_sources_monde():
    au = opp.parse_austender(opp._rss_items(AU_XML))
    assert len(au) == 1 and au[0]["closes_at"] == "2026-10-15" and au[0]["region"] == "New South Wales" and au[0]["org"] == "Department of Agriculture"
    nz = opp.parse_gets(opp._rss_items(NZ_XML))
    assert len(nz) == 1 and nz[0]["closes_at"] == "2026-10-15" and nz[0]["region"] == "Canterbury" and nz[0]["url_fr"].startswith("https://www.gets.govt.nz/NZTA/")
    co = opp.parse_secop(_secop_rows())
    assert len(co) == 1 and co[0]["region"] == "Antioquia" and co[0]["closes_at"] == "2026-09-25" and "topographie" in co[0]["specialties"]
    us = opp.parse_samgov({"opportunitiesData": [{"noticeId": "abc", "title": "UAS LiDAR mapping services", "active": "Yes", "responseDeadLine": "2026-10-01",
                                                 "uiLink": "https://sam.gov/opp/abc/view", "fullParentPathName": "DEPT OF THE INTERIOR.BLM", "placeOfPerformance": {"state": {"code": "CO"}}}]})
    assert len(us) == 1 and us[0]["region"] == "Colorado" and us[0]["country"] == "États-Unis"


def _ted_notices():
    html = {k: f"https://ted.europa.eu/{v}/notice/-/detail/604138-2026" for k, v in
            (("ENG", "en"), ("FRA", "fr"), ("DEU", "de"))}
    return [
        {"publication-number": "604138-2026", "buyer-country": ["DEU"], "publication-date": "2026-09-02+02:00",
         "deadline-receipt-tender-date-lot": ["2026-10-07+02:00"], "notice-type": "cn-standard",
         "notice-title": {"eng": "Germany – Surveying instruments – LiDAR-Sensorpaket", "fra": "Allemagne – Instruments de géodésie – LiDAR-Sensorpaket", "deu": "Deutschland – Vermessungsinstrumente – LiDAR-Sensorpaket"},
         "buyer-name": {"deu": ["Christian-Albrechts-Universität zu Kiel"]}, "description-lot": {"deu": ["LiDAR-Sensorpaket für Drohnenbefliegung"]},
         "links": {"html": html}},
        {"publication-number": "600001-2026", "buyer-country": ["ESP"], "notice-title": {"eng": "Spain – Office chairs – Sillas"}, "buyer-name": {"spa": ["X"]}, "links": {"html": {}}},
    ]


def _boamp_records():
    return [
        {"idweb": "26-90001", "objet": "Relevé photogrammétrique du littoral par drone", "nomacheteur": "Métropole de Lyon",
         "dateparution": "2026-09-10", "datelimitereponse": "2026-10-20T12:00:00+00:00", "code_departement": ["69"],
         "nature_libelle": "Avis de marché", "type_marche": ["SERVICES"], "url_avis": "https://www.boamp.fr/pages/avis/?q=idweb:26-90001",
         "descripteur_libelle": ["Topographie"], "resume": "Acquisition d'orthophotos par drone sur le littoral."},
        {"idweb": "26-90002", "objet": "Spectacle pyrotechnique", "nomacheteur": "Mairie", "code_departement": ["06"], "resume": "Feu d'artifice"},
    ]


def _uk_releases():
    return [
        {"ocid": "ocds-b5fd17-uk1", "date": "2026-09-05T10:00:00Z", "buyer": {"name": "Environment Agency"},
         "tender": {"title": "Drone survey of coastal defences", "description": "Aerial survey by UAV with LiDAR and photogrammetry outputs.",
                    "status": "active", "tenderPeriod": {"endDate": "2026-10-15T12:00:00Z"}, "mainProcurementCategory": "services",
                    "items": [{"deliveryAddresses": [{"region": "UKK South West", "locality": "Exeter"}]}],
                    "documents": [{"url": "https://www.contractsfinder.service.gov.uk/Notice/uk1"}]}},
        {"ocid": "ocds-b5fd17-uk2", "buyer": {"name": "Council"}, "tender": {"title": "Taxi services", "description": "Cars", "status": "active"}},
    ]


def test_parse_sources_europe():
    ted = opp.parse_ted(_ted_notices())
    assert len(ted) == 1 and ted[0]["country"] == "Allemagne" and ted[0]["title_fr"] == "LiDAR-Sensorpaket"
    assert ted[0]["titles"]["de"] == "LiDAR-Sensorpaket" and ted[0]["urls"]["de"].endswith("/de/notice/-/detail/604138-2026")
    assert ted[0]["closes_at"] == "2026-10-07" and "topographie" in ted[0]["specialties"]
    bo = opp.parse_boamp(_boamp_records())
    assert len(bo) == 1 and bo[0]["region"] == "Auvergne-Rhône-Alpes" and bo[0]["closes_at"] == "2026-10-20" and bo[0]["category"] == "services"
    uk = opp.parse_contractsfinder(_uk_releases())
    assert len(uk) == 1 and uk[0]["region"] == "South West" and uk[0]["city"] == "Exeter" and uk[0]["country"] == "Royaume-Uni"


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
        if url.startswith("https://boamp-datadila.opendatasoft.com/"):
            return json.dumps({"results": _boamp_records()}).encode("utf-8")
        if url.startswith("https://www.contractsfinder.service.gov.uk/"):
            return json.dumps({"releases": _uk_releases(), "links": {}}).encode("utf-8")
        if url == opp.AUSTENDER_RSS:
            return AU_XML
        if url == opp.GETS_RSS:
            return NZ_XML
        if url.startswith("https://www.datos.gov.co/"):
            return json.dumps(_secop_rows()).encode("utf-8")
        if url.startswith("https://www.jobbank.gc.ca/jobsearch/feed/"):
            return JB_EN if "searchstring=drone&" in url else b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        if url.startswith("https://www.guichetemplois.gc.ca/jobsearch/feed/"):
            return JB_FR if "searchstring=drone&" in url else b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        if url.startswith("https://api.adzuna.com/"):
            return json.dumps(_adzuna_payload() if "/jobs/ca/" in url else {"results": []}).encode("utf-8")
        raise AssertionError(url)
    monkeypatch.setattr(opp, "_fetch", fake_fetch)
    monkeypatch.setattr(opp, "ADZUNA_APP_ID", "id"); monkeypatch.setattr(opp, "ADZUNA_APP_KEY", "key")
    monkeypatch.setattr(opp, "_post_json", lambda url, payload, timeout=120: {"notices": _ted_notices(), "totalNoticeCount": 2})
    # Liens : tout répond sauf l'avis SEAO (mort) -> retiré du site.
    monkeypatch.setattr(opp, "link_status", lambda url: 404 if "seao.gouv.qc.ca/avis/1" in url else 200)
    report = opp.collect()
    assert report["canadabuys"]["items"] == 1 and report["seao"]["items"] == 1
    assert report["ted"]["items"] == 1 and report["boamp"]["items"] == 1 and report["contractsfinder"]["items"] == 1
    assert report["austender"]["items"] == 1 and report["gets"]["items"] == 1 and report["secop"]["items"] == 1
    assert "skipped" in report["samgov"]   # pas de clé SAM.gov en test
    assert report["jobbank"] == {"items": 2, "seen": 4, "errors": 0} and report["adzuna"]["items"] == 1
    assert report["published"] == 10   # 8 avis + 3 emplois, moins l'avis au lien mort
    # Emplois : page à part, onglet vers les appels d'offres, jamais mélangés
    jobs_html = client.get("/emplois").get_data(as_text=True)
    assert "Technicien/technicienne de drones" in jobs_html and "Drone Pilot / Surveyor" in jobs_html
    assert "Levé LiDAR" not in jobs_html and "guichetemplois.gc.ca/jobsearch/jobposting/50315643" in jobs_html
    assert "Salaire : 32,00 $ de l&#39;heure" in jobs_html and "Publiée le 2026-09-18" in jobs_html
    assert 'href="/opportunites"' in jobs_html and 'href="/emplois" class="on"' in jobs_html
    assert "Technicien/technicienne de drones" not in client.get("/opportunites").get_data(as_text=True)
    en_jobs = client.get("/en/emplois?region=Québec").get_data(as_text=True)
    assert "Drone technician" in en_jobs and "jobbank.gc.ca/jobsearch/jobposting/50315643" in en_jobs and "Surveyor" not in en_jobs
    client.set_cookie("aube_lang", "fr", domain="localhost.localdomain")
    assert "/emplois" in client.get("/sitemap-fr.xml").get_data(as_text=True)
    # Pays : filtre et titre dans la langue du visiteur (TED)
    fr_page = client.get("/opportunites?country=Allemagne").get_data(as_text=True)
    assert "LiDAR-Sensorpaket" in fr_page and "Levé LiDAR" not in fr_page and "ted.europa.eu/fr/" in fr_page
    de_page = client.get("/de/opportunites?country=Allemagne").get_data(as_text=True)
    assert "ted.europa.eu/de/" in de_page and "Deutschland" in de_page
    client.set_cookie("aube_lang", "fr", domain="localhost.localdomain")
    assert "Relevé photogrammétrique du littoral" in client.get("/opportunites?country=France").get_data(as_text=True)
    assert "Drone survey of coastal defences" in client.get("/en/opportunites?country=Royaume-Uni").get_data(as_text=True)
    client.set_cookie("aube_lang", "fr", domain="localhost.localdomain")
    # deuxième passe : rien ne casse, le fichier SEAO déjà lu n'est pas relu
    report2 = opp.collect()
    assert report2["seao"]["files"] == 0 and report2["published"] == report["published"]

    assert report["links"]["broken"] == 1 and report["links"]["ok"] >= 4
    html = client.get("/opportunites").get_data(as_text=True)
    assert "Levé LiDAR aéroporté" in html and "Levés laser aéroporté" not in html   # lien SEAO mort : fiche retirée
    with client.application.app_context():
        assert db.fetchone("SELECT status, link_status FROM opportunities WHERE source='seao'")["status"] == "broken"
    assert "canadabuys.canada.ca/fr/occasions-de-marche/appels-d-offres/cb-26-1" in html
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


def test_la_collecte_ne_bloque_pas_le_site(client, monkeypatch):
    """Les téléchargements se font hors transaction : pendant une collecte
    lente, une écriture du site (compteur de visite) passe sans attendre."""
    import threading, time, db
    from config import DATA_DIR
    monkeypatch.setattr(opp, "STATE_FILE", DATA_DIR + "/opp_state_slow.json")
    started, waited = threading.Event(), {"t": None}
    def slow_fetch(url, timeout=120):
        started.set(); time.sleep(1.5)
        if url == opp.CANADABUYS_CSV:
            return CSV.encode("utf-8")
        return json.dumps({"result": {"resources": []}, "results": [], "releases": [], "links": {}}).encode("utf-8")
    monkeypatch.setattr(opp, "_fetch", slow_fetch)
    monkeypatch.setattr(opp, "_post_json", lambda *a, **k: {"notices": []})
    monkeypatch.setattr(opp, "link_status", lambda url: 200)
    th = threading.Thread(target=lambda: opp.collect()); th.start()
    started.wait(5); time.sleep(0.2)
    t0 = time.time()
    with client.application.app_context():
        db.execute("INSERT INTO visit_countries(day, country, kind, views) VALUES(date('now'), 'ZZ', 'human', 1) "
                   "ON CONFLICT(day, country, kind) DO UPDATE SET views = views + 1")
    waited["t"] = time.time() - t0
    th.join(30)
    assert waited["t"] < 1.0, f"écriture bloquée {waited['t']:.1f}s pendant la collecte"
