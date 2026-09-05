"""Client AubeCaptcha à recopier dans les applications de l'écosystème.

Un seul fichier, sans dépendance : à déposer à côté de votre app.py.

    from aubecaptcha import verifier, WIDGET_SNIPPET

    ok, raison = verifier(request.form.get("aubecaptcha-token"),
                          hote_attendu="mon-service.aubeetoilee.com")
    if not ok:
        return render_template("inscription.html", erreur="Vérification refusée"), 400

Réglages par variables d'environnement :

    AUBECAPTCHA_SITEKEY   clé publique du site (posée dans le gabarit)
    AUBECAPTCHA_SECRET    secret de vérification (jamais dans une page)
    AUBECAPTCHA_URL       défaut https://captcha.aubeetoilee.com
    AUBECAPTCHA_FAIL_OPEN 1 pour laisser passer si le service est injoignable
                          (défaut 0 : on refuse, un formulaire non protégé vaut
                          moins qu'un formulaire momentanément indisponible)
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("AUBECAPTCHA_URL", "https://captcha.aubeetoilee.com").rstrip("/")
SITEKEY = os.environ.get("AUBECAPTCHA_SITEKEY", "")
SECRET = os.environ.get("AUBECAPTCHA_SECRET", "")
FAIL_OPEN = os.environ.get("AUBECAPTCHA_FAIL_OPEN", "0") == "1"

WIDGET_SNIPPET = (
    '<script src="%s/widget.js" async defer></script>\n'
    '<div class="aubecaptcha" data-sitekey="%s"></div>' % (BASE, SITEKEY or "VOTRE_SITEKEY")
)


def verifier(token: str, *, hote_attendu: str | None = None,
             secret: str | None = None, timeout: int = 10) -> tuple[bool, str]:
    """Valide un jeton auprès d'AubeCaptcha.

    Retourne (accepté, raison). `hote_attendu` compare le domaine d'où le défi
    a réellement été résolu : un jeton frappé ailleurs que sur votre site est
    refusé même s'il est valide.
    """
    cle = secret or SECRET
    if not cle:
        return False, "secret_absent"
    if not token:
        return False, "jeton_absent"

    corps = json.dumps({"secret": cle, "token": token}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/verify", data=corps, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "AubeCaptcha-Client/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rep:
            data = json.loads(rep.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as err:
        try:
            data = json.loads(err.read().decode("utf-8", errors="replace"))
        except ValueError:
            return FAIL_OPEN, "service_en_erreur"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return FAIL_OPEN, "service_injoignable"

    if not data.get("success"):
        return False, data.get("error") or "refuse"
    if hote_attendu:
        hote = (data.get("hostname") or "").lower()
        attendu = hote_attendu.lower()
        if hote != attendu and not hote.endswith("." + attendu):
            return False, "mauvais_domaine"
    return True, "ok"
