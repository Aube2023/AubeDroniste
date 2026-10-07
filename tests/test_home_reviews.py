"""Accueil : derniers avis réels (audit du 2026-10-07, « aucun avis visible »).

Un avis n'existe que sur une réservation payée ; la section n'affiche que des
avis écrits, auteur client masqué, sans tri des meilleures notes."""


def test_dernier_avis_ecrit_sur_l_accueil(client, app_ctx, funded_booking, client_user, pilot_user):
    import db
    import services
    db.execute("INSERT INTO reviews (booking_id, author_user_id, target_user_id, rating, comment) "
               "VALUES (?, ?, ?, 3, ?)",
               (funded_booking, client_user["id"], pilot_user["id"], "Photos livrées en retard mais nettes."))
    html = client.get("/").data.decode()
    assert 'id="avis"' in html and "Photos livrées en retard mais nettes." in html
    section = html.split('id="avis"')[1].split("</section>")[0]
    assert services.mask_full_name(client_user["full_name"]) in section
    assert f'href="/pilotes/{pilot_user["id"]}"' in section
    assert section.count('class="off"') >= 2          # 3 étoiles sur 5, pas de note gonflée


def test_avis_sans_commentaire_absent(app_ctx, funded_booking, client_user, pilot_user):
    import db
    import services
    db.execute("INSERT INTO reviews (booking_id, author_user_id, target_user_id, rating, comment) "
               "VALUES (?, ?, ?, 5, '  ')", (funded_booking, client_user["id"], pilot_user["id"]))
    assert all((r["comment"] or "").strip() for r in services.latest_reviews(50))


def test_afficher_l_inscription_ne_consomme_pas_le_quota(client):
    """Le quota de /inscription (6 par minute) vise les envois du formulaire :
    un visiteur qui passe de « client » à « pilote », ou un robot qui lit les
    variantes, ne doit pas tomber sur une erreur 429."""
    app = client.application
    app.config["TESTING"] = False
    try:
        codes = [client.get(f"/inscription?role={r}").status_code
                 for r in ("client", "pilot", "both") * 3]
    finally:
        app.config["TESTING"] = True
    assert codes == [200] * 9
