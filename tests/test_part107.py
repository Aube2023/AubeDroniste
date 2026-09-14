"""Côté États-Unis : page /part-107 en trois langues, menus pays dans la
langue de la page, puce « FAA Part 107 », section « référence mondiale »."""
import re

import content


def test_page_part_107(client):
    fr = client.get("/part-107").data.decode()
    assert "Les pilotes Part 107, visibles du monde entier." in fr
    en = client.get("/en/part-107").data.decode()
    assert "Part 107 drone pilots, visible to the whole world." in en
    assert "FAA Airmen registry" in en and "ZIP code" in en
    assert 'href="/en/pilotes?country=%C3%89tats-Unis"' in en           # le filtre garde la valeur en base
    assert 'hreflang="es"' in en and 'hreflang="ru"' not in en
    es = client.get("/es/part-107").data.decode()
    assert "Pilotos Part 107, visibles en todo el mundo." in es
    assert client.get("/ru/part-107").status_code == 404
    assert "/part-107</loc>" in client.get("/sitemap-en.xml").data.decode()
    client.get("/lang/fr")
    # pas de lien Part 107 au pied de page ni sur l'accueil : un pays parmi les autres.
    assert 'href="/part-107"' not in client.get("/missions").data.decode()
    assert 'href="/part-107"' not in client.get("/").data.decode()
    # la page tient lieu de page « États-Unis » dans l'annuaire, et sous le filtre États-Unis
    assert 'href="/part-107">États-Unis · Part 107' in client.get("/pilotes").data.decode()
    assert 'href="/part-107">Pilotes Part 107' in client.get("/pilotes?country=%C3%89tats-Unis").data.decode()
    assert 'href="/part-107">Pilotes Part 107' not in client.get("/pilotes?country=Canada").data.decode()


def test_pas_de_cuisine_interne_dans_part_107():
    interdits = re.compile(r"\b(cl[eé]s?|secret|token|donn[ée]es|data|SQL|serveur|server|nginx|admin|cookie)\b", re.I)
    for lang in content.PART107_LANGS:
        page = content.part107(lang)
        textes = [page["h1"], page["lead"], page["empty"], page["note"]] + [h + p for h, p in page["blocks"]]
        for t in textes:
            assert not interdits.search(t), (lang, t)


def test_menus_pays_dans_la_langue_de_la_page(client):
    fr = client.get("/").data.decode()
    assert '<option value="États-Unis">États-Unis</option>' in fr
    assert '>Allemagne<' in fr and fr.index('>Allemagne<') < fr.index('>Andorre<')
    en = client.get("/en/").data.decode()
    assert '<option value="États-Unis">United States</option>' in en
    assert '<option value="États-Unis">États-Unis</option>' not in en
    # l'ordre suit le libellé affiché : Germany avant Greece, pas Allemagne en tête
    assert en.index('>Germany<') < en.index('>Greece<') < en.index('>Zimbabwe<')
    en_reg = client.get("/en/inscription").data.decode()
    assert 'value="États-Unis">United States<' in en_reg
    client.get("/lang/fr")


def test_section_reference_mondiale_et_puce_part_107(client, app_ctx, make_user):
    import db, services
    html = client.get("/en/").data.decode()
    assert "One platform for every pilot in the world." in html
    assert "Wherever you are, pilots are visible here." in html
    assert "✓ FAA Part 107" in html and "more authorities" in html
    assert "See pilots from all over the world" in html and "Part 107 pilots in the United States" not in html
    # puce sur la carte pilote : « FAA Part 107 », pas le code nu
    p = make_user("faa_p", role="pilot", country="États-Unis", city="Austin")
    services.upsert_pilot_profile(p["id"], is_available=1)
    services.add_certification(p["id"], authority="FAA", title="Remote Pilot Certificate", reference="1234567",
                               issued_at="2025-01-01", expires_at="2027-01-01")
    db.execute("UPDATE pilot_certifications SET is_verified=1, review_status='verified' WHERE pilot_user_id=?",
               (p["id"],))
    page = client.get("/en/part-107").data.decode()
    assert "✓ FAA Part 107" in page and "Austin" in page
    client.get("/lang/fr")
