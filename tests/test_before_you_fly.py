"""Page « Avant de faire voler » (2026-10-12) : deux repères généraux pour le
client, valables dans tous les pays, reliés depuis le pied de page et le
formulaire de demande."""


def test_page_en_francais_et_traduite(client):
    html = client.get("/avant-de-faire-voler").get_data(as_text=True)
    assert "<h1>Avant de faire voler</h1>" in html
    assert "1. La réglementation locale" in html and "2. Le pilote" in html
    assert 'href="/pilotes?only_verified=1"' in html
    en = client.get("/en/avant-de-faire-voler").get_data(as_text=True)
    assert "<h1>Before you fly</h1>" in en


def test_lie_depuis_le_pied_de_page_et_le_plan_du_site(client):
    assert 'href="/avant-de-faire-voler"' in client.get("/faq").get_data(as_text=True)
    assert "/avant-de-faire-voler" in client.get("/sitemap-fr.xml").get_data(as_text=True)


def test_lie_depuis_le_formulaire_de_demande(make_user, auth_client):
    u = make_user("avant_vol_client", role="client")
    html = auth_client(u["id"]).get("/missions/nouvelle").get_data(as_text=True)
    assert 'href="/avant-de-faire-voler"' in html
