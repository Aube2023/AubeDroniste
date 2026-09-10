"""Portfolio pilote (/espace/pilote/portfolio).

Règle métier : les photos sont illimitées, les vidéos sont plafonnées à
config.MAX_PORTFOLIO_VIDEOS (hébergement/bande passante lourds). Avant, aucune
limite de nombre n'était appliquée — seulement la taille par fichier.
"""
from io import BytesIO

# IMPORTANT : pas d'import db/services/config/app au niveau module (cf conftest).


def _upload(c, filename, content=b"\x00" * 32):
    """POST multipart d'un fichier au portfolio. follow_redirects pour rendre
    la page (et donc les flashs) après le redirect PRG."""
    return c.post(
        "/espace/pilote/portfolio",
        data={"file": (BytesIO(content), filename)},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_portfolio_forbidden_for_client(make_user, auth_client):
    """Un client simple (non pilote) -> 403."""
    u = make_user("pfclient", role="client")
    c = auth_client(u["id"])
    assert c.get("/espace/pilote/portfolio").status_code == 403


def test_portfolio_photos_unlimited(make_user, auth_client, app_ctx):
    """Les photos n'ont AUCUNE limite de nombre (seulement la taille)."""
    import services

    u = make_user("pfphotos", role="pilot")
    c = auth_client(u["id"])
    for i in range(5):
        assert _upload(c, f"photo{i}.jpg").status_code == 200
    assert services.count_portfolio_items(u["id"], "image") == 5


def test_portfolio_videos_capped(make_user, auth_client, app_ctx):
    """Les vidéos sont plafonnées à MAX_PORTFOLIO_VIDEOS ; la suivante est
    refusée, mais les photos continuent de passer."""
    import config
    import services

    cap = config.MAX_PORTFOLIO_VIDEOS
    assert cap >= 1, "test calibré pour un plafond vidéo fini >= 1"

    u = make_user("pfvideos", role="pilot")
    c = auth_client(u["id"])

    # Les `cap` premières vidéos passent.
    for i in range(cap):
        assert _upload(c, f"clip{i}.mp4").status_code == 200
    assert services.count_portfolio_items(u["id"], "video") == cap

    # La (cap+1)-ème vidéo est refusée (redirige avec flash, compteur inchangé).
    r = _upload(c, "clip_over.mp4")
    assert r.status_code == 200
    assert "Maximum" in r.get_data(as_text=True)
    assert services.count_portfolio_items(u["id"], "video") == cap

    # ...mais une photo supplémentaire passe toujours (photos illimitées).
    assert _upload(c, "still.jpg").status_code == 200
    assert services.count_portfolio_items(u["id"], "image") == 1


# ---------------------------------------------------------------------------
# Partage d'une fiche + « être trouvé » (ce qui manque au pilote)
# ---------------------------------------------------------------------------

def test_partage_sur_la_fiche_publique(client, make_user, app_ctx):
    import services
    u = make_user("share_p", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1, headline="Inspections thermiques")
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert 'class="share-block"' in html
    cible = "https%3A%2F%2Fpilot.aubeetoilee.com%2Fpilotes%2F" + str(u["id"])
    for reseau in ("linkedin.com/sharing", "facebook.com/sharer", "twitter.com/intent",
                   "api.whatsapp.com", "t.me/share", "mailto:"):
        assert reseau in html
    assert cible in html                       # on partage l'URL canonique, pas /?x=
    # aucun script tiers : ce sont des liens, donc aucun mouchard
    for sdk in ("connect.facebook.net", "platform.linkedin.com", "platform.twitter.com"):
        assert sdk not in html


def test_partage_pointe_la_bonne_langue(client, make_user, app_ctx):
    import services
    u = make_user("share_lang", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    html = client.get(f"/ur/pilotes/{u['id']}").data.decode()
    assert "%2Fur%2Fpilotes%2F" + str(u["id"]) in html


def test_visibilite_liste_ce_qui_manque(auth_client, make_user, app_ctx):
    import services
    u = make_user("vis_p", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    v = services.pilot_visibility(u["id"])
    assert 0 < v["score"] < 100
    assert "specialties" in v["missing_blocking"]      # aucune specialite cochee
    # Stripe n'est reclame que si Connect est ouvert cote plateforme : sinon
    # on demanderait au pilote une demarche qui n'aboutit pas.
    import config
    assert ("payouts" in v["missing_blocking"]) == config.STRIPE_CONNECT_ENABLED

    html = auth_client(u["id"]).get("/espace/pilote").data.decode()
    assert 'class="visibility-block"' in html
    assert "Spécialités" in html and "bloquant" in html
    assert 'class="share-block"' in html               # sa fiche, a partager

    # cocher une specialite retire le point bloquant
    services.set_pilot_specialties(u["id"], ["toiture"])
    assert "specialties" not in services.pilot_visibility(u["id"])["missing_blocking"]


def test_visibilite_profil_absent(app_ctx):
    import services
    assert services.pilot_visibility(10_000_000)["items"] == []


# ---------------------------------------------------------------------------
# Livrables et présence professionnelle
# ---------------------------------------------------------------------------

def test_livrables_filtres_et_affiches(client, make_user, app_ctx):
    import services
    u = make_user("liv_p", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    services.set_pilot_deliverables(u["id"], ["panorama_360", "orthophoto", "inexistant"])
    assert services.list_pilot_deliverables(u["id"]) == ["panorama_360", "orthophoto"]
    ids = {p["id"] for p in services.search_pilots(deliverable="panorama_360", limit=500)}
    assert u["id"] in ids
    assert u["id"] not in {p["id"] for p in services.search_pilots(deliverable="ndvi", limit=500)}
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Panorama 360" in html and "Orthophoto" in html
    # traduit comme le reste
    assert "360° প্যানোরামা" in client.get(f"/bn/pilotes/{u['id']}").data.decode()


def test_liens_pro_refusent_les_schemas_dangereux(app_ctx, make_user):
    import services
    u = make_user("lien_p", role="pilot")
    n = services.set_pilot_links(u["id"], [
        ("google", "https://g.page/drone"),
        ("website", "drone-outaouais.ca"),        # sans schema -> https
        ("linkedin", "javascript:alert(1)"),      # refuse
        ("instagram", "data:text/html,x"),        # refuse
        ("inconnu", "https://x.com"),             # type inconnu -> refuse
    ])
    assert n == 2
    liens = services.list_pilot_links(u["id"])
    assert [l["kind"] for l in liens] == ["website", "google"]     # ordre de config
    assert liens[0]["url"] == "https://drone-outaouais.ca"
    assert all(l["url"].startswith("https://") for l in liens)


def test_liens_pro_plafonnes(app_ctx, make_user):
    import config, services
    u = make_user("lien_max", role="pilot")
    trop = [("other", f"https://exemple{i}.com") for i in range(config.MAX_PILOT_LINKS + 5)]
    assert services.set_pilot_links(u["id"], trop) == config.MAX_PILOT_LINKS


def test_liens_pro_masques_avant_paiement(client, make_user, app_ctx):
    import services
    u = make_user("lien_masq", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1, kind="pro")
    services.set_pilot_links(u["id"], [("google", "https://g.page/secret")])
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Avis Google" in html                 # la preuve existe, on le dit
    assert "g.page/secret" not in html           # mais pas l'adresse
    assert "référence(s) publique(s)" in html

    # une organisation s'affiche sous sa raison sociale : rien a masquer
    o = make_user("lien_org", role="pilot")
    services.upsert_pilot_profile(o["id"], is_available=1, kind="school",
                                  business_name="École Drone")
    services.set_pilot_links(o["id"], [("website", "https://ecole-drone.ca")])
    assert "ecole-drone.ca" in client.get(f"/pilotes/{o['id']}").data.decode()


def test_specialites_groupees_sur_la_fiche(client, make_user, app_ctx):
    import services
    u = make_user("spec_grp", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    services.set_pilot_specialties(u["id"], ["toiture", "eolienne", "mariage"])
    html = client.get(f"/pilotes/{u['id']}").data.decode()
    assert "Inspection technique" in html and "Médias &amp; cinéma" in html


def test_formulaire_enregistre_livrables_et_liens(auth_client, make_user, app_ctx):
    import services
    u = make_user("form_liv", role="pilot")
    services.upsert_pilot_profile(u["id"], is_available=1)
    c = auth_client(u["id"])
    assert 'id="livrables"' in c.get("/espace/pilote").data.decode()
    c.post("/espace/pilote", data={
        "headline": "x", "travel_radius_km": "50", "currency": "CAD",
        "deliverables": ["panorama_360", "modele_3d"],
        "link_kind": ["google", "website"],
        "link_url": ["https://g.page/x", ""],      # ligne vide ignoree
    })
    assert services.list_pilot_deliverables(u["id"]) == ["panorama_360", "modele_3d"]
    assert [l["kind"] for l in services.list_pilot_links(u["id"])] == ["google"]
