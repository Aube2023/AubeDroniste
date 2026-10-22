"""Titres des réalisations traduits dans la langue du visiteur (2026-10-22).

Signalé : la galerie en swahili montrait « toiture », « pont Rem »… tels que
le pilote les avait écrits. autotranslate.py les fait traduire par MyMemory
dans un fil de fond et les garde en cache ; la page ne fait jamais d'appel
réseau. Ici MyMemory est simulé (autotranslate._fetch, ou urlopen pour le
décodage de sa réponse)."""
import io
import json

import pytest

# IMPORTANT : pas d'import db/services/config/app au niveau module (cf conftest).


@pytest.fixture()
def autotr(monkeypatch):
    """Traduction allumée, fil de fond remplacé par un appel explicite à
    _drain() (le test décide quand la file se vide), état du module remis à
    zéro avant et après."""
    import autotranslate as a

    def reset():
        a._queue.clear()
        a._pending.clear()
        a._paused_until = 0.0

    reset()
    monkeypatch.setattr(a, "ENABLED", True)
    monkeypatch.setattr(a, "MIN_INTERVAL_S", 0)
    monkeypatch.setattr(a, "_start", lambda: None)
    yield a
    reset()


def _piece(services, user_id, title):
    return services.add_portfolio_item(
        pilot_user_id=user_id, title=title, description="", kind="image",
        original_filename="x.jpg", stored_filename=f"portfolio_u{user_id}/1_x.jpg",
        mime_type="image/jpeg", size_bytes=10,
    )


def _fake_mymemory(calls):
    table = {("toiture-autotr", "sw"): "paa-autotr", ("pont Rem-autotr", "sw"): "daraja la Rem-autotr"}

    def fetch(text, src, lang):
        calls.append((text, src, lang))
        return table.get((text, lang), "")
    return fetch


def test_galerie_en_swahili_titre_traduit_apres_le_fil_de_fond(make_user, app_ctx, client, autotr, monkeypatch):
    import services
    calls = []
    monkeypatch.setattr(autotr, "_fetch", _fake_mymemory(calls))
    u = make_user("autotr_sw", role="pilot", full_name="Benoit Autotr")
    _piece(services, u["id"], "toiture-autotr")

    # Premier passage : rien en cache, l'original s'affiche, aucun appel réseau
    # pendant la page ; le titre attend dans la file.
    html = client.get("/sw/realisations").get_data(as_text=True)
    assert "toiture-autotr" in html and "paa-autotr" not in html
    assert calls == []
    assert ("fr", "toiture-autotr", "sw") in autotr._pending

    autotr._drain()
    assert ("toiture-autotr", "fr", "sw") in calls

    html = client.get("/sw/realisations").get_data(as_text=True)
    assert 'lang="sw" dir="auto" title="Jina asili: toiture-autotr">paa-autotr<' in html
    assert "Majina yametafsiriwa kiotomatiki" in html        # mention « traduit automatiquement »


def test_meme_langue_que_le_pilote_rien_n_est_demande(make_user, app_ctx, client, autotr, monkeypatch):
    import services
    calls = []
    monkeypatch.setattr(autotr, "_fetch", _fake_mymemory(calls))
    u = make_user("autotr_fr", role="pilot")
    _piece(services, u["id"], "facade-autotr-fr")
    html = client.get("/realisations").get_data(as_text=True)
    assert 'lang="fr" dir="auto" title="facade-autotr-fr">facade-autotr-fr<' in html
    assert not any(j[1] == "facade-autotr-fr" for j in autotr._pending)
    assert "Titres traduits automatiquement" not in html


def test_langue_du_compte_du_pilote_et_fiche(make_user, app_ctx, client, autotr, monkeypatch):
    """Un pilote qui écrit en anglais : sa fiche en anglais ne demande rien,
    sa fiche en swahili demande en|sw ; la description garde sa langue."""
    import db
    import services
    calls = []
    monkeypatch.setattr(autotr, "_fetch", _fake_mymemory(calls))
    u = make_user("autotr_en", role="pilot")
    db.execute("UPDATE users SET lang='en' WHERE id=?", (u["id"],))
    services.add_portfolio_item(
        pilot_user_id=u["id"], title="water-tower-autotr", description="Inspection details",
        kind="image", original_filename="x.jpg", stored_filename=f"portfolio_u{u['id']}/1_x.jpg",
        mime_type="image/jpeg", size_bytes=10,
    )
    html = client.get(f"/sw/pilotes/{u['id']}").get_data(as_text=True)
    assert ("en", "water-tower-autotr", "sw") in autotr._pending
    assert 'lang="en" dir="auto"' in html                    # titre et description en anglais
    assert 'lang="en" dir="auto" style="margin: 4px 0 0;">Inspection details' in html


def test_titre_supprime_traduction_oubliee(make_user, app_ctx, autotr):
    import db
    import services
    u = make_user("autotr_prune", role="pilot")
    item_id = _piece(services, u["id"], "pont Rem-autotr")
    autotr._store("fr", "pont Rem-autotr", "sw", "daraja la Rem-autotr")
    assert db.fetchone("SELECT 1 FROM text_translations WHERE src_text='pont Rem-autotr'")
    services.delete_portfolio_item(item_id, u["id"])
    assert not db.fetchone("SELECT 1 FROM text_translations WHERE src_text='pont Rem-autotr'")


def test_titre_modifie_ancienne_traduction_oubliee(make_user, app_ctx, autotr):
    import db
    import services
    u = make_user("autotr_edit", role="pilot")
    item_id = _piece(services, u["id"], "ancien-autotr")
    autotr._store("fr", "ancien-autotr", "sw", "zamani-autotr")
    services.update_portfolio_item(item_id, u["id"], "nouveau-autotr", "")
    assert not db.fetchone("SELECT 1 FROM text_translations WHERE src_text='ancien-autotr'")


def test_quota_epuise_le_service_se_tait(app_ctx, autotr, monkeypatch):
    def quota(text, src, lang):
        raise autotr._Unavailable(3600)
    monkeypatch.setattr(autotr, "_fetch", quota)
    autotr._schedule({("fr", "a-autotr"), ("fr", "b-autotr")}, "sw")
    autotr._drain()
    assert not autotr._queue and not autotr._pending
    assert autotr._paused_until > 0
    autotr._schedule({("fr", "c-autotr")}, "sw")             # en pause : rien n'est mis en file
    assert not autotr._queue


def test_sans_traduction_l_original_est_garde_en_cache(make_user, app_ctx, autotr, monkeypatch):
    """MyMemory n'a rien (« Crémazie ») : l'original est mis en cache, la page
    ne le redemande plus et n'affiche pas « traduit »."""
    import db
    import services
    monkeypatch.setattr(autotr, "_fetch", lambda text, src, lang: "")
    u = make_user("autotr_propre", role="pilot")
    _piece(services, u["id"], "Crémazie-autotr")
    autotr._schedule({("fr", "Crémazie-autotr")}, "sw")
    autotr._drain()
    row = db.fetchone("SELECT text FROM text_translations WHERE src_text='Crémazie-autotr' AND lang='sw'")
    assert row and row["text"] == "Crémazie-autotr"
    items = autotr.localize_titles([{"title": "Crémazie-autotr", "src_lang": "fr"}], "sw")
    assert items[0]["title_original"] == "" and items[0]["title_lang"] == "fr"
    assert not autotr._pending


def test_eteint_aucun_appel(app_ctx, autotr, monkeypatch):
    monkeypatch.setattr(autotr, "ENABLED", False)
    items = autotr.localize_titles([{"title": "toiture", "src_lang": None}], "sw")
    assert items[0]["title_shown"] == "toiture" and items[0]["title_lang"] == "fr"
    assert not autotr._pending


def test_casse_des_noms_propres_et_entites():
    import autotranslate as a
    assert a._tidy("pont Rem", "rem bridge") == "Rem bridge"
    assert a._tidy("Toiture", "roof") == "Roof"
    assert a._tidy("tour a eau", "water tower") == "water tower"
    assert a._tidy("l'eau", "l&#39;acqua") == "l'acqua"


def _answer(monkeypatch, payload):
    import urllib.request
    seen = {}

    def urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return io.BytesIO(json.dumps(payload).encode("utf-8"))   # urlopen s'utilise en with
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_fetch_retient_la_traduction_machine(monkeypatch):
    """Les mémoires partagées (« Disc. », « REM ») sont écartées : seule la
    traduction machine compte."""
    import autotranslate as a
    seen = _answer(monkeypatch, {
        "responseStatus": 200, "responseData": {"translatedText": "REM", "match": 0.9},
        "matches": [{"translation": "REM", "created-by": "MateCat"},
                    {"translation": "rem bridge", "created-by": "MT!"},
                    {"translation": "Disc.", "created-by": "MateCat"}],
    })
    assert a._fetch("pont Rem", "fr", "en") == "Rem bridge"
    assert "langpair=fr%7Cen" in seen["url"] and "mt=1" in seen["url"]
    assert "onlyprivate=1" in seen["url"]          # pas de mémoire partagée (« Ua » pour « photo »)
    _answer(monkeypatch, {"responseStatus": 200, "matches": []})
    assert a._fetch("x", "zh", "fr") == ""


def test_fetch_code_chinois_et_refus(monkeypatch):
    import autotranslate as a
    seen = _answer(monkeypatch, {"responseStatus": "403", "responseDetails": "INVALID LANGUAGE PAIR"})
    assert a._fetch("toiture", "fr", "zh") == ""
    assert "fr%7Czh-CN" in seen["url"]


def test_fetch_quota(monkeypatch):
    import autotranslate as a
    _answer(monkeypatch, {"responseStatus": 429, "quotaFinished": True,
                          "responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS"}})
    with pytest.raises(a._Unavailable) as exc:
        a._fetch("toiture", "fr", "sw")
    assert abs(exc.value.pause - a._until_tomorrow()) < 5          # jusqu'au lendemain, pas 10 min
