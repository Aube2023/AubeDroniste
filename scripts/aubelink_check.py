#!/usr/bin/env python3
"""Contrôle de la liaison AubePilot → AubeLink pour un compte.

    sudo -u aube /srv/aubepilot/.venv/bin/python /srv/aubepilot/scripts/aubelink_check.py --user nicolas

Lit /etc/aubepilot.env comme systemd le lit pour le service (EnvironmentFile :
dernière occurrence retenue, guillemets entourants retirés ; une variable
déjà posée par l'environnement d'appel est gardée), puis appelle
GET /api/v1/beacon-status au nom de l'adresse @aubemail.com du compte
(X-Acting-User), exactement comme la page des balises. Affiche
« joignable, N drones, M associés » puis les associations. Code de sortie 0
si AubeLink répond, 1 sinon (fichier d'environnement illisible, fonction non
configurée, compte inconnu, AubeLink injoignable, clé ou portée refusée).
N'affiche jamais la clé.
"""
import argparse
import os
import sys
from typing import MutableMapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# En prod, /etc/aubepilot.env porte AUBELINK_URL, AUBELINK_KEY, AUBEPILOT_SECRET...
ENV_FILE = "/etc/aubepilot.env"


def _unquote(value: str) -> str:
    """Guillemets entourants (doubles ou simples) retirés, comme systemd."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_env_file(path: str, environ: MutableMapping[str, str] = os.environ) -> Optional[str]:
    """Charge `path` dans `environ` avec les règles de systemd (EnvironmentFile) :
    lignes CLE=valeur, commentaires en # ou ;, guillemets entourants retirés,
    DERNIÈRE occurrence d'une clé retenue (la commande du contrat ajoute la clé
    en fin de fichier par `>>`). Une variable déjà présente dans `environ` avant
    l'appel est gardée. Rend un message si le fichier existe mais ne peut pas
    être lu (sans sudo, par exemple), None sinon ; un fichier absent laisse
    l'environnement d'appel seul (poste de développement)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or exc.__class__.__name__
        return (f"{path} illisible ({reason}) : lancez le script sous le compte du service "
                "(sudo -u aube), sinon sa configuration reste inconnue.")
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line[0] in "#;" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            values[key] = _unquote(value.strip())
    inherited = set(environ)
    for key, value in values.items():
        if key not in inherited:
            environ[key] = value
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="username AubePilot dont l'adresse @aubemail.com sert de délégué")
    args = ap.parse_args()

    # Avant tout import de config, qui lit l'environnement une fois pour toutes.
    env_error = load_env_file(ENV_FILE)
    if env_error:
        print(env_error, file=sys.stderr)
        return 1

    import config
    from beacon import aubelink

    if not aubelink.enabled():
        print("AubeLink non configuré : AUBELINK_URL (http ou https) et AUBELINK_KEY (alp_...) sont requis.",
              file=sys.stderr)
        return 1

    from app import app   # après le chargement de l'environnement
    import db
    from beacon import devices

    with app.app_context():
        user = db.fetchone("SELECT id, username, email, deleted_at FROM users WHERE username=?",
                           (args.user.strip().lower(),))
        if not user:
            print(f"compte {args.user!r} introuvable", file=sys.stderr)
            return 1
        if user["deleted_at"]:
            # AubePilot n'agit plus au nom d'un compte supprimé (cf. aubelink.device_acting).
            print(f"compte {args.user!r} supprimé : AubeLink n'est pas appelé en son nom", file=sys.stderr)
            return 1
        acting = aubelink.acting_email(user["username"], user["email"])
        known = {d["device_uid"] for d in devices.list_devices(user["id"])}

    print(f"AubeLink : {config.AUBELINK_URL} ; délégué : {acting}")
    try:
        data = aubelink.beacon_status(acting, fresh=True)
    except aubelink.AubeLinkError as exc:
        detail = f", {exc.remote_code}" if exc.remote_code else ""
        print(f"échec : {exc.code} (statut HTTP {exc.status}{detail})", file=sys.stderr)
        return 1
    drones = [item["drone"] for item in data.get("drones") or []
              if isinstance(item, dict) and isinstance(item.get("drone"), dict)]
    linked = [d for d in drones if d.get("beaconId")]
    print(f"joignable, {len(drones)} drones, {len(linked)} associés"
          + (" (liste tronquée par AubeLink)" if data.get("truncated") else ""))
    for d in linked:
        note = "" if d["beaconId"] in known else " (balise inconnue de ce compte AubePilot)"
        print(f"  {d.get('droneId')} porte {d['beaconId']} : liaison {d.get('linkState')}, vol {d.get('flightState')}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
