"""Reperes de confiance sur la fiche (membre depuis, reactivite, livraisons),
compteur de vues de fiche pour le pilote, et provenance detaillee (pages,
sites d'origine) : toujours sans IP, sans cookie, sans visiteur unique."""


def _insert_msg(db, mission_id, sender, recipient, created_at):
    db.execute("INSERT INTO messages (mission_id, sender_user_id, recipient_user_id, body, created_at) "
               "VALUES (?, ?, ?, 'x', ?)", (mission_id, sender, recipient, created_at))


def _mission(services, client_id, i):
    return services.create_mission(client_id, title=f"Mission reactivite {i}", description="d" * 30,
                                   mission_type="photo", country="Canada", city="Laval", lat=45.6, lng=-73.7)


def test_temps_de_reponse_median_et_taux(app_ctx, make_user):
    import db, services
    p = make_user("react_p", role="pilot")
    c1 = make_user("react_c1", role="client")
    c2 = make_user("react_c2", role="client")
    # moins de 3 conversations repondues : pas de badge
    assert services.pilot_response_time(p["id"]) is None
    m = [_mission(services, c1["id"], i) for i in range(4)]
    # 3 conversations repondues en 0,5 h, 2 h et 20 h -> mediane 2 h -> « moins de 3 h »
    _insert_msg(db, m[0], c1["id"], p["id"], "2026-09-01 10:00:00"); _insert_msg(db, m[0], p["id"], c1["id"], "2026-09-01 10:30:00")
    _insert_msg(db, m[1], c1["id"], p["id"], "2026-09-02 10:00:00"); _insert_msg(db, m[1], p["id"], c1["id"], "2026-09-02 12:00:00")
    _insert_msg(db, m[2], c2["id"], p["id"], "2026-09-03 10:00:00"); _insert_msg(db, m[2], p["id"], c2["id"], "2026-09-04 06:00:00")
    # le pilote ecrit le premier sur m[3] : ce n'est pas une reponse
    _insert_msg(db, m[3], p["id"], c2["id"], "2026-09-05 10:00:00")
    r = services.pilot_response_time(p["id"])
    assert r and r["under_h"] == 3 and r["median_h"] == 2.0 and r["answered"] == 3 and r["rate"] == 1.0
    # 2 conversations laissees sans reponse depuis plus de 48 h : taux 3/5 < 70 % -> plus de badge
    m2 = [_mission(services, c2["id"], 10 + i) for i in range(2)]
    _insert_msg(db, m2[0], c2["id"], p["id"], "2026-09-01 09:00:00")
    _insert_msg(db, m2[1], c2["id"], p["id"], "2026-09-01 09:00:00")
    assert services.pilot_response_time(p["id"]) is None


def test_fiche_affiche_les_reperes(client, app_ctx, make_user):
    import db, services
    p = make_user("repere_p", role="pilot", country="Canada", city="Québec")
    services.upsert_pilot_profile(p["id"], is_available=1)
    db.execute("UPDATE users SET created_at='2026-05-06 10:00:00' WHERE id=?", (p["id"],))
    html = client.get(f"/pilotes/{p['id']}").data.decode()
    assert "Membre depuis mai 2026" in html
    assert "Répond en général" not in html            # pas de donnees, pas de badge
    assert "livrée" not in html
    # en anglais, le mois suit la langue
    assert "Member since May 2026" in client.get(f"/en/pilotes/{p['id']}").data.decode()
    client.get("/lang/fr")


def test_mois_et_annee_par_langue():
    import i18n
    assert i18n.month_year("2026-05-06 10:00:00", "fr") == "mai 2026"
    assert i18n.month_year("2026-12-01", "ur") == "دسمبر 2026"
    assert i18n.month_year("", "fr") == "" and i18n.month_year("2026-13-01", "fr") == ""


def test_vues_de_fiche_hors_robots_et_hors_pilote(client, auth_client, make_user, app_ctx):
    import services
    p = make_user("vues_p", role="pilot", country="Canada", city="Laval")
    services.upsert_pilot_profile(p["id"], is_available=1)
    anon = client.application.test_client()
    assert services.profile_view_counts(p["id"])["d30"] == 0
    anon.get(f"/pilotes/{p['id']}")
    anon.get(f"/en/pilotes/{p['id']}")
    anon.get(f"/pilotes/{p['id']}", headers={"User-Agent": "Mozilla/5.0 (compatible; bingbot/2.0)"})
    c = auth_client(p["id"])
    c.get(f"/pilotes/{p['id']}")                     # le pilote lui-meme
    counts = services.profile_view_counts(p["id"])
    assert counts == {"d7": 2, "d30": 2, "total": 2}
    # le pilote voit son compteur dans son espace
    assert "2 fois sur 30 jours" in c.get("/espace/pilote").data.decode()
    assert "Vues de fiche" in c.get("/espace").data.decode()
    assert any(x["user_id"] == p["id"] and x["views"] == 2 for x in services.most_viewed_profiles(30, 100))


def test_provenance_pages_et_sites_origine(client, auth_client, make_user, app_ctx):
    import db, services
    anon = client.application.test_client()
    anon.get("/pilotes", headers={"Referer": "https://www.google.ca/search?q=pilote+drone+laval"})
    anon.get("/pilotes", headers={"Referer": "https://www.linkedin.com/feed/update/123"})
    anon.get("/pilotes")                                                          # direct
    anon.get("/pilotes", headers={"Referer": "http://localhost.localdomain/"})     # interne : pas une arrivee
    anon.get("/pilotes", headers={"Referer": "https://www.google.com/",
                                  "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"})
    pages = {r["path"]: r["views"] for r in services.visits_by_page(1, limit=100)}
    assert pages.get("/pilotes", 0) >= 4
    ref = services.visits_by_referrer(1)
    hosts = {r["host"]: r for r in ref["rows"]}
    assert hosts["google.ca"]["family"] == "search" and hosts["linkedin.com"]["family"] == "social"
    assert "direct" in hosts and "localhost.localdomain" not in hosts
    assert ref["families"]["search"] >= 1 and ref["families"]["social"] >= 1
    # jamais le chemin ni la requete du referent, jamais d'IP
    assert not any("search" in h or "?" in h or "/" in h for h in hosts)
    for table, cols in (("visit_pages", {"day", "path", "kind", "views"}),
                        ("visit_referrers", {"day", "host", "views"}),
                        ("profile_views", {"day", "pilot_user_id", "views"})):
        assert {r[1] for r in db.fetchall(f"PRAGMA table_info({table})")} == cols
    # page admin
    u = make_user("admin_prov", role="both")
    db.execute("UPDATE users SET is_admin=1 WHERE id=?", (u["id"],))
    html = auth_client(u["id"]).get("/admin/visites?jours=7").data.decode()
    assert "D'où ils arrivent" in html and "google.ca" in html and "Pages les plus vues" in html


def test_compteur_public_ignore_les_comptes_supprimes(app_ctx, make_user):
    import db, services
    before = services.public_stats()["pilots"]
    u = make_user("stat_del", role="pilot")
    assert services.public_stats()["pilots"] == before + 1
    db.execute("UPDATE users SET deleted_at=datetime('now') WHERE id=?", (u["id"],))
    assert services.public_stats()["pilots"] == before
