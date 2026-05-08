"""Pose / met a jour un mot de passe local pour tester en dev (macOS).

Sur macOS, l'auth PAM n'est pas disponible — AubePilot utilise le
fichier .dev_passwords (SHA-256 sale). Cet helper permet d'ajouter un
user "factice" qui imite ton compte AubeMail pour le tester localement.

Sur Linux (prod), ce script n'a aucun effet : PAM ignore .dev_passwords.

Usage :
    python scripts/dev_setpw.py <username> [password]

Si le password n'est pas passe en argument, le script le demande
interactivement (sans echo). Le username est passe en lower-case.
"""
import getpass
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import auth  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    username = auth.normalize_username(sys.argv[1])
    if not username:
        print("Username vide.", file=sys.stderr)
        return 2

    if len(sys.argv) >= 3:
        password = sys.argv[2]
    else:
        password = getpass.getpass(f"Mot de passe pour '{username}' : ")

    if len(password) < 4:
        print("Mot de passe trop court (min 4).", file=sys.stderr)
        return 3

    auth.set_dev_password(username, password)
    print(f"OK — '{username}' peut maintenant se connecter localement.")
    print("(Le fichier .dev_passwords est gitignore, ne quittera pas ton macOS.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
