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
