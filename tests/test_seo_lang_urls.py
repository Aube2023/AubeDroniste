"""Une URL par langue + pages d'atterrissage + sitemap multilingue + IndexNow.

Ce que Google doit voir : la langue par defaut a la racine, les autres sous
/<lang>/, une canonique par version, toutes les versions en hreflang, et des
pages d'annuaire (pays, ville, specialite) indexables seulement si elles
listent au moins un pilote.
"""
import re

import i18n
import seo


def _alternates(html):
    return dict(re.findall(r'hreflang="([\w-]+)" href="([^"]+)"', html))


# ---------------------------------------------------------------------------
# Routage par prefixe
# ---------------------------------------------------------------------------

def test_prefixe_de_langue_sert_la_langue(client):
    for code in ("en", "ur", "bn"):
        r = client.get(f"/{code}/")
        assert r.status_code == 200
        assert f'<html lang="{code}"' in r.data.decode()


def test_racine_reste_francaise_sans_cookie(client):
    html = client.get("/").data.decode()
    assert '<html lang="fr"' in html
    assert 'rel="canonical" href="https://pilot.aubeetoilee.com/"' in html


def test_prefixe_inconnu_404(client):
    assert client.get("/xx/").status_code == 404
    assert client.get("/xx/pilotes").status_code == 404


def test_liens_internes_gardent_le_prefixe(client):
    html = client.get("/en/").data.decode()
    assert 'href="/en/pilotes"' in html and 'href="/en/faq"' in html
    assert not re.search(r'href="/pilotes"', html)


def test_page_privee_sans_alternates(client, auth_client, pilot_user):
    html = auth_client(pilot_user["id"]).get("/espace").data.decode()
    assert "hreflang" not in html


# ---------------------------------------------------------------------------
# Canonical, hreflang, og:locale
# ---------------------------------------------------------------------------

def test_hreflang_liste_toutes_les_langues(client):
    html = client.get("/ur/pilotes").data.decode()
    alts = _alternates(html)
    assert set(alts) == set(i18n.SUPPORTED) | {"x-default"}
    assert alts["fr"] == "https://pilot.aubeetoilee.com/pilotes"
    assert alts["x-default"] == alts["fr"]
    assert alts["ur"] == "https://pilot.aubeetoilee.com/ur/pilotes"
    assert 'rel="canonical" href="https://pilot.aubeetoilee.com/ur/pilotes"' in html


def test_og_locale_par_langue(client):
    html = client.get("/bn/").data.decode()
    assert '<meta property="og:locale" content="bn_BD">' in html
    assert html.count('property="og:locale:alternate"') == len(i18n.SUPPORTED) - 1


def test_titre_et_description_dans_la_langue(client):
    html = client.get("/es/").data.decode()
    assert "<title>" + seo._S["home.title"]["es"] in html
    assert "Pilote" not in re.search(r"<title>(.*?)</title>", html).group(1)


# ---------------------------------------------------------------------------
# Selecteur et cookie
# ---------------------------------------------------------------------------

def test_selecteur_redirige_vers_l_url_de_la_langue(client):
    r = client.get("/lang/ur?next=/pilotes/5")
    assert r.headers["Location"] == "/ur/pilotes/5"
    r = client.get("/lang/fr?next=/en/pilotes/5")
    assert r.headers["Location"] == "/pilotes/5"
    # espace prive : pas de version par langue, le cookie suffit
    r = client.get("/lang/ur?next=/espace")
    assert r.headers["Location"] == "/espace"
    # la chaine de requete survit
    r = client.get("/lang/bn?next=/contact?x=1")
    assert r.headers["Location"] == "/bn/contact?x=1"


def test_url_prefixee_pose_le_cookie(client):
    r = client.get("/bn/contact")
    assert "aube_lang=bn" in r.headers.get("Set-Cookie", "")


def test_cookie_redirige_l_url_nue(client):
    client.set_cookie("aube_lang", "tr", domain="localhost.localdomain")
    r = client.get("/pilotes?q=x")
    assert r.status_code == 302 and r.headers["Location"].endswith("/tr/pilotes?q=x")
    # cookie francais : la racine reste la racine
    client.set_cookie("aube_lang", "fr", domain="localhost.localdomain")
    assert client.get("/pilotes").status_code == 200


def test_accept_language_ne_change_pas_la_page_nue(client):
    # L'URL nue est la version francaise, quoi qu'annonce le navigateur : le
    # contenu concorde avec la canonique et un robot voit toujours la meme
    # page. La langue du navigateur est proposee dans un bandeau.
    r = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    html = r.data.decode()
    assert r.status_code == 200 and '<html lang="fr"' in html
    assert 'class="lang-suggest"' in html and 'href="/en/"' in html
    assert "This site is also available in English." in html
    assert 'href="/lang/fr?next=%2F"' in html or 'href="/lang/fr?next=/"' in html
    # rien a proposer quand le navigateur est deja en francais, ou hors pages publiques
    assert 'class="lang-suggest"' not in client.get("/", headers={"Accept-Language": "fr-CA"}).data.decode()
    assert 'class="lang-suggest"' not in client.get("/api/stats", headers={"Accept-Language": "en"}).data.decode()


def test_pages_legales_une_seule_url(client):
    # Contenu francais uniquement : pas de fausses traductions publiees.
    assert client.get("/en/cgu").status_code == 404
    html = client.get("/cgu").data.decode()
    assert "hreflang" not in html
    assert 'rel="canonical" href="https://pilot.aubeetoilee.com/cgu"' in html
    # description propre a la page, pas celle de l'accueil
    assert "de la plateforme AubePilot : compte, missions, devis" in html   # l'apostrophe est echappee
    assert seo._S["home.description"]["fr"] not in html


def test_faq_seulement_fr_et_en(client):
    alts = _alternates(client.get("/faq").data.decode())
    assert set(alts) == {"fr", "en", "x-default"}
    assert client.get("/en/faq").status_code == 200
    assert client.get("/es/faq").status_code == 404
    client.get("/lang/fr")          # la visite de /en/faq a pose le cookie
    # depuis une page espagnole, le lien FAQ pointe vers l'URL francaise
    assert 'href="/faq"' in client.get("/es/").data.decode()
    # et le selecteur envoie vers /faq (pas /es/faq qui n'existe pas)
    assert client.get("/lang/es?next=/faq").headers["Location"] == "/faq"
    # pas de FAQPage en donnees structurees hors fr/en
    assert '"FAQPage"' not in client.get("/es/").data.decode()
    assert '"FAQPage"' in client.get("/en/").data.decode()


def test_connexion_noindex_inscription_decrite(client):
    html = client.get("/ur/connexion").data.decode()
    assert 'name="robots" content="noindex' in html
    html = client.get("/bn/inscription").data.decode()
    assert 'name="robots" content="index' in html
    assert i18n.t("register.lead", "bn")[:30] in html


def test_formulaire_contact_poste_sur_l_url_de_la_langue(client):
    html = client.get("/en/contact").data.decode()
    assert 'action="/en/contact"' in html


# ---------------------------------------------------------------------------
# Pages d'atterrissage
# ---------------------------------------------------------------------------

def test_pages_pays_et_ville_listent_le_pilote(client, app_ctx, make_user):
    import services
    u = make_user("landing_p", role="pilot", country="Maroc", city="Casablanca")
    services.upsert_pilot_profile(u["id"], is_available=1)
    html = client.get("/pilotes/pays/maroc").data.decode()
    assert "Pilotes de drone au Maroc" in html            # preposition francaise
    assert f'href="/pilotes/{u["id"]}"' in html
    assert '"@type": "ItemList"' in html
    assert 'name="robots" content="index' in html
    html = client.get("/en/pilotes/pays/maroc/casablanca").data.decode()
    assert "Drone pilots in Casablanca" in html
    assert "Morocco" in html                              # nom de pays traduit (fil d'Ariane)
    assert client.get("/pilotes/pays/nulle-part").status_code == 404
    assert client.get("/pilotes/pays/maroc/nulle-part").status_code == 404


def test_page_specialite_vide_noindex(client):
    html = client.get("/pilotes/specialite/feux_foret").data.decode()
    assert 'name="robots" content="noindex' in html
    assert client.get("/pilotes/specialite/inexistante").status_code == 404


def test_page_specialite_avec_pilote_indexable(client, app_ctx, make_user):
    import db, services
    u = make_user("landing_s", role="pilot", country="France", city="Lyon")
    services.upsert_pilot_profile(u["id"], is_available=1)
    db.execute("INSERT OR IGNORE INTO pilot_specialties (pilot_user_id, mission_type) VALUES (?, ?)",
               (u["id"], "eolienne"))
    html = client.get("/bn/pilotes/specialite/eolienne").data.decode()
    assert 'name="robots" content="index' in html
    assert f'href="/bn/pilotes/{u["id"]}"' in html
    assert "hub-list" in html                              # bloc « explorer l'annuaire »


def test_pages_erreur_noindex(client):
    html = client.get("/pilotes/999999").data.decode()
    assert 'name="robots" content="noindex' in html


# ---------------------------------------------------------------------------
# Sitemap + IndexNow
# ---------------------------------------------------------------------------

def test_sitemap_index_et_un_fichier_par_langue(client, app_ctx, make_user):
    import services
    u = make_user("sitemap_p", role="pilot", country="Canada", city="Montréal")
    services.upsert_pilot_profile(u["id"], is_available=1)
    index = client.get("/sitemap.xml").data.decode()
    assert "<sitemapindex" in index
    for code in i18n.SUPPORTED:
        assert f"<loc>https://pilot.aubeetoilee.com/sitemap-{code}.xml</loc>" in index
    assert client.get("/sitemap-xx.xml").status_code == 404
    xml = client.get("/sitemap-ur.xml").data.decode()
    assert f"<loc>https://pilot.aubeetoilee.com/ur/pilotes/{u['id']}</loc>" in xml
    assert 'hreflang="x-default" href="https://pilot.aubeetoilee.com/pilotes/' in xml
    assert "/ur/pilotes/pays/canada</loc>" in xml
    assert "/ur/pilotes/pays/canada/montreal</loc>" in xml
    # pages qui n'existent pas en ourdou : absentes de ce fichier
    assert "/faq</loc>" not in xml and "/cgu</loc>" not in xml
    fr = client.get("/sitemap-fr.xml").data.decode()
    assert "<loc>https://pilot.aubeetoilee.com/cgu</loc>" in fr
    assert "<loc>https://pilot.aubeetoilee.com/faq</loc>" in fr
    # une page monolingue ne porte pas d'alternates
    assert fr.count("/cgu</loc>") == 1 and 'href="https://pilot.aubeetoilee.com/cgu"' not in fr


def test_villes_regroupees_par_slug(client, app_ctx, make_user):
    import services
    a = make_user("slug_a", role="pilot", country="Canada", city="Montréal")
    b = make_user("slug_b", role="pilot", country="Canada", city="Montreal")
    c = make_user("slug_c", role="pilot", country="Canada", city="Laval")
    d = make_user("slug_d", role="pilot", country="Canada", city="Lavaltrie")
    for u in (a, b, c, d):
        services.upsert_pilot_profile(u["id"], is_available=1)
    cities = {x["slug"]: x for x in services.landing_cities() if x["country_slug"] == "canada"}
    assert cities["montreal"]["n"] >= 2 and set(cities["montreal"]["names"]) >= {"Montréal", "Montreal"}
    html = client.get("/pilotes/pays/canada/montreal").data.decode()
    assert f'href="/pilotes/{a["id"]}"' in html and f'href="/pilotes/{b["id"]}"' in html
    # filtre exact : « laval » ne prend pas « Lavaltrie »
    html = client.get("/pilotes/pays/canada/laval").data.decode()
    assert f'href="/pilotes/{c["id"]}"' in html and f'href="/pilotes/{d["id"]}"' not in html
    xml = client.get("/sitemap-fr.xml").data.decode()
    assert xml.count("/pilotes/pays/canada/montreal</loc>") == 1


def test_specialite_inconnue_ignoree(app_ctx, make_user):
    import db, services
    u = make_user("spec_x", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    services.set_pilot_specialties(u["id"], ["eolienne", "<script>", "inexistante"])
    rows = db.fetchall("SELECT mission_type FROM pilot_specialties WHERE pilot_user_id=?", (u["id"],))
    assert {r["mission_type"] for r in rows} == {"eolienne"}
    assert all(sp["code"] != "inexistante" for sp in services.landing_specialties())


def test_courriel_rendu_hors_requete(app):
    # Les alertes de mission sont rendues dans un fil de fond avec un simple
    # app_context : le processeur de contexte ne doit pas exiger une requete.
    from flask import render_template
    with app.app_context():
        html = render_template("emails/mission_alert.html", pilot={"full_name": "P"},
                               mission={"id": 1, "title": "T", "city": "Lyon", "country": "France",
                                        "mission_type": "photo", "budget_min": 100, "budget_max": 200,
                                        "currency": "EUR", "description": "d"},
                               distance_km=12)
    assert "T" in html


def test_jobposting_url_dans_la_langue(client, open_mission):
    html = client.get(f"/en/missions/{open_mission}").data.decode()
    assert f'"url": "https://pilot.aubeetoilee.com/en/missions/{open_mission}"' in html


def test_indexnow_cle_servie_et_inactif_hors_https(client):
    r = client.get(f"/{seo.INDEXNOW_KEY}.txt")
    assert r.status_code == 200 and r.data.decode() == seo.INDEXNOW_KEY
    assert seo.indexnow_ping(["/pilotes/1"]) == 0          # SITE_URL http en test
    assert seo.indexnow_ping([]) == 0


def test_chaines_seo_completes_dans_toutes_les_langues():
    trous = [(k, l) for k, e in seo._S.items() for l in i18n.SUPPORTED if not e.get(l)]
    assert trous == []
    # un exemple par langue se formate sans erreur
    for l in i18n.SUPPORTED:
        seo.landing_page(l, path="/x", specialty="X", city="Y", count=1, pilot_ids=[1])
        seo.pilot_profile(l, name="N", city="C", country="Maroc")


def test_prepositions_francaises():
    assert i18n.fr_in_country("Maroc") == "au Maroc"
    assert i18n.fr_in_country("France") == "en France"
    assert i18n.fr_in_country("Pays-Bas") == "aux Pays-Bas"
    assert i18n.fr_in_country("Corée du Sud") == "en Corée du Sud"
    assert i18n.fr_in_country("Iran") == "en Iran"
    assert i18n.fr_in_country("Cuba") == "à Cuba"
    assert i18n.fr_in_country("Guinée-Bissau") == "en Guinée-Bissau"
    assert i18n.fr_in_country("Royaume-Uni") == "au Royaume-Uni"
    assert i18n.fr_in_country("Haïti") == "en Haïti"
    assert i18n.fr_in_country("Yémen") == "au Yémen"
    assert i18n.fr_in_country("Cap-Vert") == "au Cap-Vert"
    assert i18n.fr_in_country("Bosnie-Herzégovine") == "en Bosnie-Herzégovine"
    assert i18n.fr_place("Casablanca", "Maroc") == "à Casablanca, Maroc"


def test_noms_de_pays_traduits():
    assert i18n.country_name("Allemagne", "en") == "Germany"
    assert i18n.country_name("Allemagne", "fr") == "Allemagne"
    assert i18n.country_name("Inconnu", "tr") == "Inconnu"
