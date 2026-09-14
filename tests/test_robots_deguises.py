"""Robots deguises en navigateur (bots.py) : le User-Agent ne suffit plus, on
lit ce qu'un vrai navigateur envoie toujours avec une page. Les vrais
navigateurs passent, les collecteurs a en-tetes incomplets sont comptes
comme robots, et rien n'est conserve."""
import bots


def _h(browser_headers, **over):
    h = dict(browser_headers)
    for k, v in over.items():
        key = k.replace("_", "-")
        if v is None:
            h.pop(key, None)
        else:
            h[key] = v
    return h


def test_vrais_navigateurs_passent(browser_headers):
    assert bots.reason(_h(browser_headers)) is None                                          # Chrome
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) "
                          "Gecko/20100101 Firefox/132.0", Sec_CH_UA=None)) is None
    # Safari : ni Client Hints, ni (avant 16.4) Sec-Fetch
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                          "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
                          Sec_CH_UA=None, Sec_Fetch_Mode=None, Sec_Fetch_Dest=None, Sec_Fetch_Site=None)) is None
    # WebView Android (application AubePilot) : pas de Client Hints
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (Linux; Android 14; Pixel 8 Build/AP2A; wv) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36 AubePilotMobile/1.4.1",
                          Sec_CH_UA=None)) is None
    # Vieux Chrome (avant les Client Hints) mais avec Sec-Fetch
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/85.0.4183.83 Safari/537.36", Sec_CH_UA=None)) is None


def test_collecteurs_deguises_sont_des_robots(browser_headers):
    chrome = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36")
    # le collecteur du 13 septembre : Chrome recent, aucun en-tete de navigateur
    assert bots.reason({"User-Agent": chrome, "Accept": "*/*"}) == "no-accept-language"
    assert bots.reason({"User-Agent": chrome, "Accept-Language": "en-US"}) == "accept"
    assert bots.reason({"User-Agent": chrome, "Accept-Language": "en-US",
                        "Accept": "text/html,*/*;q=0.8"}) == "no-sec-fetch"
    assert bots.reason(_h(browser_headers, User_Agent=chrome, Sec_CH_UA=None)) == "no-client-hints"
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
                          "Gecko/20100101 Firefox/130.0",
                          Sec_CH_UA=None, Sec_Fetch_Mode=None, Sec_Fetch_Dest=None)) == "no-sec-fetch"
    # agents avoues, requetes qui ne sont pas des pages, prechargement
    assert bots.reason(_h(browser_headers, User_Agent="Mozilla/5.0 (compatible; Googlebot/2.1)")) == "ua"
    assert bots.reason(_h(browser_headers, User_Agent="python-requests/2.32")) == "ua"
    assert bots.reason(_h(browser_headers, User_Agent="")) == "no-ua"
    assert bots.reason(_h(browser_headers, Sec_Fetch_Mode="cors", Sec_Fetch_Dest="empty")) == "not-navigation"
    assert bots.reason(_h(browser_headers, Sec_Purpose="prefetch")) == "prefetch"
    assert bots.reason(_h(browser_headers, Purpose="prefetch")) == "prefetch"


def test_le_collecteur_ne_compte_ni_visiteur_ni_arrivee_google(client, app_ctx, browser_headers):
    import services
    avant = services.visits_totals(1)
    ref_avant = {r["host"]: r["views"] for r in services.visits_by_referrer(1)["rows"]}
    crawler = {"User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/132.0.0.0 Mobile Safari/537.36",
               "Accept": "*/*", "Referer": "https://www.google.com/"}
    anon = client.application.test_client()
    # le client de test simule un navigateur ; on remplace tout par le collecteur
    anon.environ_base = {k: v for k, v in anon.environ_base.items() if not k.startswith("HTTP_")}
    assert anon.get("/", headers=crawler).status_code == 200
    assert anon.get("/pilotes", headers=crawler).status_code == 200
    apres = services.visits_totals(1)
    assert apres["human"] == avant["human"]
    assert apres["bot"] == avant["bot"] + 2
    ref_apres = {r["host"]: r["views"] for r in services.visits_by_referrer(1)["rows"]}
    assert ref_apres.get("google.com", 0) == ref_avant.get("google.com", 0)
    # un vrai navigateur venu de Google, lui, compte
    assert anon.get("/pilotes", headers=dict(browser_headers, Referer="https://www.google.com/")).status_code == 200
    assert services.visits_totals(1)["human"] == avant["human"] + 1
    ref_fin = {r["host"]: r["views"] for r in services.visits_by_referrer(1)["rows"]}
    assert ref_fin.get("google.com", 0) == ref_avant.get("google.com", 0) + 1


def test_referent_impossible_et_client_hints_incoherents(browser_headers):
    h = dict(browser_headers, Host="localhost.localdomain")
    # apres un 302 un navigateur garde la page d'origine, jamais /lang/xx
    assert bots.reason(dict(h, Referer="https://localhost.localdomain/lang/ur?next=%2Ftr%2Fpilotes")) == "referer-redirect"
    # un navigateur serialise toujours le chemin : « https://site » nu n'existe pas
    assert bots.reason(dict(h, Referer="https://localhost.localdomain")) == "referer-origin"
    assert bots.reason(dict(h, Referer="https://localhost.localdomain/")) is None
    assert bots.reason(dict(h, Referer="https://www.google.com")) is None       # autre site : pas notre affaire
    # agent tire au sort, Client Hints fixes : les versions ne concordent plus
    assert bots.reason(dict(h, **{"Sec-CH-UA": '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"'})) == "client-hints-mismatch"
    assert bots.reason(dict(h, **{"Sec-CH-UA-Platform": '"Windows"'})) == "platform-mismatch"   # UA dit Macintosh
    assert bots.reason(dict(h, **{"Sec-CH-UA-Platform": '"macOS"'})) is None
    assert bots.reason(dict(h, **{"Sec-CH-UA-Mobile": "?1"})) == "mobile-mismatch"
    # Edge et Opera : la version Chromium figure dans les marques, ca passe
    edge = h["User-Agent"] + " Edg/145.0.0.0"
    assert bots.reason(dict(h, **{"User-Agent": edge, "Sec-CH-UA": '"Microsoft Edge";v="145", "Chromium";v="145", "Not-A.Brand";v="99"'})) is None
    assert "Cookie-names=" in bots.describe(dict(h, Cookie="aube_lang=fr; session=abc")) and "abc" not in bots.describe(dict(h, Cookie="session=abc"))
