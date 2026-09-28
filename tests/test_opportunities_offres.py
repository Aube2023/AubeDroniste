"""Offres d'emploi déposées sur AubePilot par les entreprises et les écoles.

Gratuit, avec le logo de l'entreprise, vérifié par l'équipe AVANT la mise en
ligne : formulaire, logo (type lu dans le fichier), file de validation,
fiche publique avec données JobPosting, liste /emplois, signalement,
modification qui repasse en vérification, suppression du compte.

Le fichier s'appelle test_opportunities_offres.py pour passer APRÈS
test_opportunities.py, qui compte les fiches publiées de la base partagée ;
chaque test retire en plus ses offres en sortant.
"""
import io

import pytest

import i18n
import job_posts

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FORM = {
    "title": "Pilote de drone, inspection de lignes",
    "org": "Aerium Test Inc.",
    "country": "Canada",
    "region": "AB",
    "city": "Calgary",
    "employment_type": "seasonal",
    "salary": "30 à 38 $ de l'heure",
    "description": ("Pilote RPAS en chef pour l'inspection de lignes électriques en Alberta. Brevet opérations "
                    "avancées exigé, déplacements en région, rotation de 14 jours."),
    "apply_url": "www.aerium-test.example/carrieres",
    "apply_email": "",
    "closes_at": "",
}


@pytest.fixture(autouse=True)
def _nettoyage(_app):
    yield
    import db
    with _app.app_context():
        db.execute("DELETE FROM opportunities WHERE source='aubepilot'")
        db.execute("DELETE FROM reports WHERE target_type='job'")


def test_traductions_des_offres_dans_les_31_langues():
    keys = [k for k in i18n._T if k.startswith("job.")]
    assert len(keys) >= 60, "translations/_jobs.json n'a pas été chargé"
    trous = [(k, code) for k in keys for code in i18n.SUPPORTED if not (i18n._T[k].get(code) or "").strip()]
    assert trous == []
    assert "60" in i18n.t("job.f_closes_hint", "de", days=60, max=90)


def test_validation_du_formulaire():
    fields, errors = job_posts.validate(FORM)
    assert errors == []
    assert fields["apply_url"] == "https://www.aerium-test.example/carrieres"
    assert fields["closes_at"] > "2026-01-01" and fields["employment_type"] == "seasonal"
    bad = dict(FORM, title="x", country="Atlantide", description="court", apply_url="javascript:alert(1)",
               closes_at="2020-01-01")
    _fields, errors = job_posts.validate(bad)
    assert set(errors) == {"job.err_title", "job.err_country", "job.err_description", "job.err_apply", "job.err_closes"}
    assert job_posts.clean_url("javascript:alert(1)") == "" and job_posts.clean_url("https://x.example/a b") == ""
    assert job_posts.clean_email("rh@entreprise.ca") == "rh@entreprise.ca" and job_posts.clean_email("pas un courriel") == ""
    assert job_posts.country_iso2("Canada") == "CA"


def test_depot_verification_publication_et_fiche(app, client, auth_client, make_user):
    import db
    boss = make_user("jobs_boss", role="client", country="Canada", city="Calgary")
    admin = make_user("jobs_admin", role="client")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (admin["id"],))
    anon = app.test_client()

    c = auth_client(boss["id"])
    html = c.get("/emplois/publier").get_data(as_text=True)
    assert "Publier une offre d&#39;emploi" in html and 'name="logo"' in html

    # Dépôt avec logo : en vérification, invisible du public.
    r = c.post("/emplois/publier", data=dict(FORM, logo=(io.BytesIO(PNG), "logo.png")),
               content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/espace/offres")
    post = dict(db.fetchone("SELECT * FROM opportunities WHERE source='aubepilot' AND posted_by=?", (boss["id"],)))
    logo = db.fetchone("SELECT org_logo_path FROM users WHERE id=?", (boss["id"],))["org_logo_path"]
    assert post["status"] == "pending" and post["kind"] == "job" and post["region"] == "Alberta"
    assert post["url_fr"].endswith(f"/emplois/{post['id']}") and "inspection" in post["specialties"]
    assert logo.startswith("uploads/orglogo_u")
    assert anon.get("/media/" + logo[8:]).status_code == 200
    assert "Aerium Test Inc." not in anon.get("/emplois").get_data(as_text=True)
    assert anon.get(f"/emplois/{post['id']}").status_code == 404
    assert c.get(f"/emplois/{post['id']}").status_code == 200          # l'auteur la voit
    assert "En vérification" in c.get("/espace/offres").get_data(as_text=True)

    # L'équipe la voit dans sa file, la publie.
    a = auth_client(admin["id"])
    admin_html = a.get("/admin/opportunites").get_data(as_text=True)
    assert "Offres d'emploi à valider (1)" in admin_html and "Aerium Test Inc." in admin_html
    assert a.post(f"/admin/opportunites/{post['id']}/valider").status_code == 302
    post = dict(db.fetchone("SELECT * FROM opportunities WHERE id=?", (post["id"],)))
    assert post["status"] == "published" and post["published_at"]

    jobs_html = anon.get("/emplois").get_data(as_text=True)
    assert "Pilote de drone, inspection de lignes" in jobs_html and "/media/orglogo_u" in jobs_html
    assert f'href="/emplois/{post["id"]}"' in jobs_html
    assert "Offre déposée sur AubePilot par l&#39;employeur" in jobs_html and "Publier une offre gratuitement" in jobs_html
    page = anon.get(f"/emplois/{post['id']}").get_data(as_text=True)
    assert "rotation de 14 jours" in page and 'href="https://www.aerium-test.example/carrieres"' in page
    assert '"JobPosting"' in page and '"addressCountry": "CA"' in page and "Aerium Test Inc." in page
    en = app.test_client().get(f"/en/emplois/{post['id']}").get_data(as_text=True)
    assert "Apply" in en and "Pilote de drone, inspection de lignes" in en
    assert f"/emplois/{post['id']}</loc>" in anon.get("/sitemap-fr.xml").get_data(as_text=True)

    # Signalement par un tiers : l'auteur de l'offre est la personne visée.
    pilot = make_user("jobs_reporter", role="pilot")
    r = auth_client(pilot["id"]).post("/signaler", data={"target_type": "job", "target_id": post["id"],
                                                         "reason": "scam", "details": "demande de l'argent"})
    assert r.status_code == 302
    rep = db.fetchone("SELECT * FROM reports WHERE target_type='job' AND target_id=?", (post["id"],))
    assert rep and rep["target_user_id"] == boss["id"]

    # Une modification repasse en vérification et quitte le site.
    c = auth_client(boss["id"])
    r = c.post(f"/espace/offres/{post['id']}/modifier", data=dict(FORM, salary="40 $ de l'heure"),
               content_type="multipart/form-data")
    assert r.status_code == 302
    row = db.fetchone("SELECT status, salary FROM opportunities WHERE id=?", (post["id"],))
    assert row["status"] == "pending" and row["salary"] == "40 $ de l'heure"
    assert app.test_client().get(f"/emplois/{post['id']}").status_code == 404

    # Refus motivé : l'auteur voit le motif.
    a = auth_client(admin["id"])
    assert a.post(f"/admin/opportunites/{post['id']}/refuser", data={"note": "Salaire à préciser"}).status_code == 302
    assert "Salaire à préciser" in auth_client(boss["id"]).get("/espace/offres").get_data(as_text=True)

    # Fermer puis supprimer.
    c = auth_client(boss["id"])
    c.post(f"/espace/offres/{post['id']}/supprimer")
    assert db.fetchone("SELECT 1 FROM opportunities WHERE id=?", (post["id"],)) is None


def test_logo_refuse_si_ce_n_est_pas_une_image(client, auth_client, make_user):
    import db
    u = make_user("jobs_svg", role="client", country="France")
    c = auth_client(u["id"])
    r = c.post("/emplois/publier", data=dict(FORM, logo=(io.BytesIO(b"<svg onload=alert(1)></svg>"), "logo.png")),
               content_type="multipart/form-data")
    assert r.status_code == 400 and "Logo refusé" in r.get_data(as_text=True)
    assert db.fetchone("SELECT 1 FROM opportunities WHERE posted_by=?", (u["id"],)) is None
    assert db.fetchone("SELECT org_logo_path FROM users WHERE id=?", (u["id"],))["org_logo_path"] is None


def test_fermeture_et_suppression_du_compte(client, auth_client, make_user):
    import db
    import services
    u = make_user("jobs_quit", role="client", country="Canada")
    fields, errors = job_posts.validate(FORM)
    assert errors == []
    oid = job_posts.create(u["id"], fields, publish=True)
    assert job_posts.is_public(job_posts.get(oid))
    auth_client(u["id"]).post(f"/espace/offres/{oid}/fermer")
    assert db.fetchone("SELECT status FROM opportunities WHERE id=?", (oid,))["status"] == "closed"
    other = job_posts.create(u["id"], fields, publish=True)
    assert services.delete_account(u["id"])["ok"]
    # effacées avec le compte (politique de confidentialité, section 5)
    assert db.fetchone("SELECT 1 FROM opportunities WHERE id IN (?, ?)", (oid, other)) is None


def test_une_fiche_collectee_n_a_pas_de_page_aubepilot(client, app_ctx):
    import db
    db.execute("INSERT INTO opportunities (source, source_ref, kind, title_fr, title_en, url_fr, status, "
               "first_seen_at, last_seen_at) VALUES ('jobbank', 'offres-test-1', 'job', 'X', 'X', 'https://x.example/', "
               "'published', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
    oid = db.fetchone("SELECT id FROM opportunities WHERE source_ref='offres-test-1'")["id"]
    try:
        assert client.get(f"/emplois/{oid}").status_code == 404
    finally:
        db.execute("DELETE FROM opportunities WHERE source_ref='offres-test-1'")
