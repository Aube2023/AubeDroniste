"""« Mes statistiques » du pilote dans son espace (2026-10-11) : visites de la
fiche, demandes reçues, taux visite -> demande, devis, mandats payés et leur
valeur, types de travaux, courbe des 12 dernières semaines. Tout est tiré de
l'activité réelle ; un bloc à zéro laisse la place à un conseil."""


def test_textes_stats_dans_les_31_langues():
    import i18n
    keys = [k for k in i18n._T if k.startswith("stats.")]
    assert len(keys) >= 27, "translations/_stats.json n'a pas été chargé"
    trous = [(k, code) for k in keys for code in i18n.SUPPORTED if not (i18n._T[k].get(code) or "").strip()]
    assert trous == []
    for code in i18n.SUPPORTED:
        assert "42" in i18n.t("stats.conversion", code, pct="42")
        assert "2026-09-28" in i18n.t("stats.week_tip", code, date="2026-09-28", n=7)


def _activite(client_user, pilot_user):
    """3 vues aujourd'hui + 5 il y a 60 jours, une demande ciblée, une mission
    créée par le pilote depuis un fil (pas une demande de plus), un fil de
    deux messages (une seule demande)."""
    import db
    import services
    for _ in range(3):
        services.bump_profile_view(pilot_user["id"])
    db.execute("INSERT INTO profile_views(day, pilot_user_id, views) VALUES(date('now', '-60 days'), ?, 5)",
               (pilot_user["id"],))
    for from_contact in (0, 1):
        mid = services.create_mission(client_user["id"], title="Toiture", description="Inspection d'une toiture",
                                      mission_type="immobilier", country="France", city="Paris")
        db.execute("UPDATE missions SET targeted_pilot_id=?, is_private=1, from_contact=? WHERE id=?",
                   (pilot_user["id"], from_contact, mid))
    for body in ("Bonjour, êtes-vous libre samedi ?", "Et pour une vidéo aussi ?"):
        assert services.send_contact_message(client_id=client_user["id"], pilot_id=pilot_user["id"],
                                             sender_id=client_user["id"], body=body)


def test_chiffres_tires_de_l_activite(app_ctx, client_user, pilot_user, funded_booking):
    import services
    _activite(client_user, pilot_user)
    s = services.pilot_stats(pilot_user["id"], "all")
    assert s["views"] == 8
    assert (s["req_missions"], s["req_messages"], s["requests"]) == (1, 1, 2)
    assert s["conversion"] == "25"                       # 2 demandes / 8 visites
    assert (s["bids"], s["bids_accepted"]) == (1, 1)
    assert (s["contracts"], s["contracts_completed"]) == (1, 0)
    booking = services.get_booking(funded_booking)
    assert s["money"][0]["currency"] == booking["currency"]
    assert s["money"][0]["gross"].startswith("1 000 ")
    net = booking["agreed_price"] - booking["platform_fee"]
    assert s["money"][0]["net"] == f"{net:,.0f}".replace(",", " ") + f" {booking['currency']}"
    assert s["types"] == [{"code": "immobilier", "n": 1}]
    # Courbe : 12 semaines, la plus récente en dernier ; les vues d'il y a
    # 60 jours tombent dans la semaine 8 (60 // 7).
    assert len(s["weeks"]) == 12 and s["weeks"][-1]["n"] == 3 and s["weeks"][-9]["n"] == 5
    assert s["weeks_total"] == 8 and s["weeks"][-9]["pct"] == 100

    s30 = services.pilot_stats(pilot_user["id"], "30")
    assert s30["views"] == 3 and s30["conversion"] == "67"
    assert services.pilot_stats(pilot_user["id"], "n'importe quoi")["period"] == "all"


def test_reservation_annulee_ou_impayee_pas_comptee(app_ctx, client_user, pilot_user, open_mission, pending_bid):
    import db
    import services
    booking_id = services.accept_bid(open_mission, pending_bid, client_user["id"])
    assert services.pilot_stats(pilot_user["id"])["contracts"] == 0      # pas encore payée
    services.mark_booking_funded(booking_id, payment_intent_id="pi_fake_stats")
    assert services.pilot_stats(pilot_user["id"])["contracts"] == 1
    db.execute("UPDATE bookings SET status='refunded' WHERE id=?", (booking_id,))
    assert services.pilot_stats(pilot_user["id"])["contracts"] == 0


def test_espace_pilote_sans_activite_montre_des_conseils(auth_client, make_user):
    pilot = make_user("stats_vide", role="pilot")
    html = auth_client(pilot["id"]).get("/espace").get_data(as_text=True)
    assert 'id="stats"' in html and "Mes statistiques" in html
    assert "encore ouvert votre fiche" in html
    assert "Aucune demande sur cette période" in html
    assert "Aucun mandat payé sur cette période" in html and "/opportunites" in html
    assert "Visite → demande" not in html and 'class="stats-weeks"' not in html
    assert "Types de travaux" not in html


def test_espace_pilote_avec_activite(auth_client, app_ctx, client_user, pilot_user, funded_booking):
    _activite(client_user, pilot_user)
    c = auth_client(pilot_user["id"])
    html = c.get("/espace").get_data(as_text=True)
    assert "Visite → demande : 25 %" in html
    assert "Demandes de devis : 1 · Premiers messages : 1" in html
    assert "Mandats conclus" in html and "Votre part après commission" in html
    assert "Types de travaux" in html and 'class="stats-weeks"' in html
    assert "encore ouvert votre fiche" not in html
    html = c.get("/espace?stats=30").get_data(as_text=True)
    assert 'href="/espace?stats=30#stats" class="on" aria-current="page"' in html
    assert "Visite → demande : 67 %" in html


def test_client_sans_section_stats(auth_client, client_user):
    html = auth_client(client_user["id"]).get("/espace").get_data(as_text=True)
    assert 'id="stats"' not in html


def test_pays_des_visites(app_ctx, make_user):
    """5 premiers pays, puis « autres pays » et « non localisé » regroupés ;
    les vues d'avant le relevé des pays n'y figurent pas."""
    import db
    import services
    pilot = make_user("stats_pays", role="pilot")
    for code, n in (("CA", 6), ("FR", 3), ("US", 2), ("BE", 1), ("CH", 1), ("MA", 1), ("SN", 1), (None, 2)):
        for _ in range(n):
            services.bump_profile_view(pilot["id"], code)
    db.execute("INSERT INTO profile_views(day, pilot_user_id, views) VALUES(date('now', '-90 days'), ?, 4)",
               (pilot["id"],))
    s = services.pilot_stats(pilot["id"], "all", "fr")
    assert s["views"] == 21
    rows = [(c["code"], c["n"]) for c in s["countries"]]
    assert rows == [("CA", 6), ("FR", 3), ("US", 2), ("BE", 1), ("CH", 1), ("*", 2), ("??", 2)]
    assert s["countries"][0]["name"] == "Canada" and s["countries"][0]["flag"] == "🇨🇦"
    assert s["countries"][0]["pct"] == 35                      # 6 / 17 vues localisées ou non
    assert s["countries"][-2]["name"] == "Autres pays" and s["countries"][-1]["name"] == "Non localisé"
    assert s["countries_since"] == services.PROFILE_COUNTRIES_SINCE
    assert services.pilot_stats(pilot["id"], "all", "en")["countries"][-1]["name"] == "Not located"


def test_espace_affiche_les_pays(auth_client, make_user, app_ctx):
    import services
    pilot = make_user("stats_pays_page", role="pilot")
    services.bump_profile_view(pilot["id"], "CA")
    html = auth_client(pilot["id"]).get("/espace").get_data(as_text=True)
    assert "Pays des visiteurs" in html and "Canada" in html
    since = services.PROFILE_COUNTRIES_SINCE.replace("-", "\u2011")    # tiret insecable
    assert f"Pays relevés depuis le {since}" in html


def test_visite_de_fiche_compte_le_pays(client, make_user, app_ctx, monkeypatch):
    import db
    import geoip
    pilot = make_user("stats_pays_visite", role="pilot")
    monkeypatch.setattr(geoip, "country_code", lambda ip: "FR")
    client.get(f"/pilotes/{pilot['id']}")
    row = db.fetchone("SELECT country, views FROM profile_view_countries WHERE pilot_user_id=?", (pilot["id"],))
    assert (row["country"], row["views"]) == ("FR", 1)


def test_politique_mentionne_pays_et_provenance_des_vues_de_fiche(client):
    html = client.get("/confidentialite").get_data(as_text=True)
    assert "compteur de vues par jour, par pays et par provenance" in html
    assert "jamais l&#39;adresse de la page" in html or "jamais l'adresse de la page" in html


def test_provenance_d_une_vue():
    import services
    own = "pilot.aubeetoilee.com"
    cas = {
        None: "direct",
        "": "direct",
        "https://www.google.ca/": "google.ca",
        "https://l.facebook.com/l.php?u=x": "l.facebook.com",
        f"https://{own}/": "aubepilot:search",
        f"https://{own}/en/": "aubepilot:search",
        f"https://{own}/pilotes?q=toiture": "aubepilot:search",
        f"https://{own}/es/pilotes/pays/canada/quebec": "aubepilot:search",
        f"https://{own}/pilotes/specialite/immobilier": "aubepilot:search",
        f"https://{own}/part-107": "aubepilot:search",
        f"https://{own}/ecoles": "aubepilot:search",
        f"https://{own}/pilotes/12": "aubepilot:site",
        f"https://{own}/missions/3": "aubepilot:site",
    }
    for ref, attendu in cas.items():
        assert services.profile_view_source(ref, own) == attendu, ref
    assert services.source_label("google.ca", "fr") == "Google"
    assert services.source_label("m.facebook.com", "fr") == "Facebook"
    assert services.source_label("lnkd.in", "fr") == "LinkedIn"
    assert services.source_label("aubeetoilee.com", "fr") == "aubeetoilee.com"
    assert services.source_label("direct", "en") == "Direct (bookmark, email, app)"


def test_provenance_regroupee(app_ctx, make_user):
    import services
    pilot = make_user("stats_sources", role="pilot")
    for source, n in (("google.com", 3), ("google.ca", 2), ("l.facebook.com", 2), ("facebook.com", 1),
                      ("aubepilot:search", 4), ("direct", 1), ("lnkd.in", 1), ("aubeetoilee.com", 1),
                      ("aubepilot:site", 1)):
        for _ in range(n):
            services.bump_profile_view(pilot["id"], "CA", source)
    s = services.pilot_stats(pilot["id"], "all", "fr")
    rows = [(c["name"], c["n"]) for c in s["sources"]]
    assert rows[:3] == [("Google", 5), ("Recherche AubePilot (accueil, carte, annuaire)", 4), ("Facebook", 3)]
    assert len(rows) == 6 and rows[-1] == ("Autres sites", 2)
    assert sum(n for _name, n in rows) == 16 == s["views"]
    assert s["sources_since"] == services.PROFILE_SOURCES_SINCE


def test_visite_de_fiche_compte_la_provenance(client, make_user, app_ctx):
    import db
    pilot = make_user("stats_sources_visite", role="pilot")
    client.get(f"/pilotes/{pilot['id']}", headers={"Referer": "https://www.facebook.com/"})
    client.get(f"/en/pilotes/{pilot['id']}", headers={"Referer": "http://localhost.localdomain/en/pilotes?q=x"})
    rows = {r["source"]: r["views"] for r in db.fetchall(
        "SELECT source, views FROM profile_view_sources WHERE pilot_user_id=?", (pilot["id"],))}
    assert rows == {"facebook.com": 1, "aubepilot:search": 1}


def test_espace_affiche_la_provenance(auth_client, make_user, app_ctx):
    import services
    pilot = make_user("stats_sources_page", role="pilot")
    services.bump_profile_view(pilot["id"], "CA", "instagram.com")
    html = auth_client(pilot["id"]).get("/espace").get_data(as_text=True)
    assert "Provenance des visites" in html and "Instagram" in html
