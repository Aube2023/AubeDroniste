"""Page « Nouveautés » (/nouveautes) : ce qui a changé pour les pilotes et
les clients, en trois langues, sans un mot de cuisine interne."""
import re

import content


def test_page_en_trois_langues(client):
    fr = client.get("/nouveautes").data.decode()
    assert "Ce qui a changé pour vous." in fr and "Septembre 2026" in fr and "31 langues" in fr
    assert 'hreflang="en"' in fr and 'href="https://pilot.aubeetoilee.com/en/nouveautes"' in fr
    assert 'hreflang="ru"' not in fr                   # la page n'existe pas en russe
    en = client.get("/en/nouveautes").data.decode()
    assert "What changed for you." in en and "September 2026" in en
    es = client.get("/es/nouveautes").data.decode()
    assert "Lo que cambió para usted." in es and "Septiembre de 2026" in es
    assert client.get("/ru/nouveautes").status_code == 404
    client.get("/lang/fr")


def test_pas_de_cuisine_interne():
    interdits = re.compile(r"\b(cl[eé]s?|secret|token|jeton|donn[ée]es?|data|base de donn|SQL|serveur|"
                           r"nginx|CSP|SRI|captcha|cookie|IP|journal|admin|backup|sauvegarde|migration|"
                           r"key|database|server|password hash|encrypt)\b", re.I)
    for lang in content.UPDATES_LANGS:
        for r in content.updates(lang):
            assert r["items"], r["title"]
            for item in r["items"]:
                assert not interdits.search(item), (lang, item)
    # le taux de commission vient de la configuration, jamais d'un chiffre en dur
    assert "{fee}" not in "".join(i for r in content.updates("fr") for i in r["items"])
    assert any("20 %" in i for r in content.updates("fr") for i in r["items"])


def test_langue_inconnue_retombe_en_francais():
    assert content.updates("ru")[0]["title"] == content.updates("fr")[0]["title"]


def test_dans_le_pied_de_page_et_le_sitemap(client):
    html = client.get("/missions").data.decode()
    assert 'href="/nouveautes"' in html and "Nouveautés" in html
    assert 'href="/en/nouveautes"' in client.get("/en/missions").data.decode()
    xml = client.get("/sitemap-fr.xml").data.decode()
    assert "/nouveautes</loc>" in xml
    assert "/nouveautes</loc>" in client.get("/sitemap-es.xml").data.decode()
    assert "/nouveautes</loc>" not in client.get("/sitemap-ru.xml").data.decode()
    client.get("/lang/fr")
