"""Parite de la table de traductions.

Chaque cle de `i18n._T` doit exister dans toutes les langues de SUPPORTED,
avec les memes variables {…} que le francais : une cle manquante retombe
silencieusement en francais, et un placeholder oublie casse le .format().
"""
import re

import i18n

PLACEHOLDER = re.compile(r"{(\w+)}")


def test_toutes_les_langues_couvrent_toutes_les_cles():
    manquants = [
        (key, lang)
        for key, entry in i18n._T.items()
        for lang in i18n.SUPPORTED
        if not (entry.get(lang) or "").strip()
    ]
    assert manquants == []


def _placeholders(text):
    # {in_place} (francais, preposition comprise) vaut {place} ailleurs.
    return {"place" if p == "in_place" else p for p in PLACEHOLDER.findall(text)}


def test_les_placeholders_suivent_le_francais():
    ecarts = [
        (key, lang)
        for key, entry in i18n._T.items()
        for lang in i18n.SUPPORTED
        if _placeholders(entry[lang]) != _placeholders(entry["fr"])
    ]
    assert ecarts == []


def test_selecteur_de_langue_complet():
    # Le menu de la topbar lit LANGUAGE_META : sans entree, on afficherait
    # le code brut et un globe a la place du drapeau.
    assert all(code in i18n.LANGUAGE_META for code in i18n.SUPPORTED)


def test_sens_decriture():
    assert i18n.lang_dir("ur") == "rtl"
    assert i18n.lang_dir("bn") == "ltr"
    assert i18n.lang_dir("fr") == "ltr"
    assert all(code in i18n.LANGUAGE_META for code in i18n.RTL)


def test_filipino_annonce_en_fil_par_le_navigateur(client):
    # Chrome/Android envoient « fil », pas « tl » : le bandeau doit mener à /tl/.
    html = client.get("/", headers={"Accept-Language": "fil-PH,fil;q=0.9,en;q=0.5"}).get_data(as_text=True)
    assert 'href="/tl/"' in html
    html = client.get("/", headers={"Accept-Language": "zh-CN,zh;q=0.9"}).get_data(as_text=True)
    assert 'href="/zh/"' in html


def test_plus_de_textes_en_dur_fr_sinon_anglais():
    # Les gabarits passaient par « 'texte' if fr else 'text' » : les 30 autres
    # langues voyaient l'anglais. Tout passe par t('tpl.…') depuis 2026-09-18.
    import pathlib
    restes = [p.name for p in pathlib.Path("templates").rglob("*.html")
              if any(m in p.read_text(encoding="utf-8") for m in ("if fr else", "{% if fr %}", "if lang == 'fr' else"))]
    assert restes == []
