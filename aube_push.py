"""Push de livrables AubePilot vers AubeDrive / AubePhotos.

Server-to-server : on POST le fichier multipart vers le point d'entree interne
de chaque service Aube, avec le courriel AubeMail du proprietaire (le client)
et le secret partage `AUBE_INTERNAL_SECRET` en en-tete `X-Internal-Secret`
(meme convention que leur /api/auth/provision) :

- AubeDrive  : /api/files/internal/upload, dossier racine « AubePilot » ;
- AubePhotos : /api/photos/internal/upload, album « AubePilot ».

Les deux renvoient `url`, le chemin relatif du dossier ou de l'album, qu'on
rend absolu avec l'adresse publique du service pour le montrer au client.
Les appels passent par l'adresse interne (AUBEDRIVE_INTERNAL_URL...) : sur
le VPS, la boucle locale evite la limite de taille de nginx (55 Mo pour
AubeDrive) alors qu'un livrable peut peser 1 Go.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from config import (
    AUBE_INTERNAL_SECRET,
    AUBEDRIVE_INTERNAL_URL,
    AUBEDRIVE_URL,
    AUBEPHOTOS_INTERNAL_URL,
    AUBEPHOTOS_URL,
    UPLOAD_DIR,
)

log = logging.getLogger("aubepilot.push")

_TIMEOUT = (10, 600)  # connect, read (livrables jusqu'a 1 Go)

# Ce que AubePhotos sait afficher (son ALLOWED_MIME)
PHOTOS_KINDS = ("image", "video")

SERVICE_NAMES = {"aubedrive": "AubeDrive", "aubephotos": "AubePhotos"}

# Codes d'erreur des services Aube ; ceux de SHOWN_ERRORS ont leur message
# (cle i18n push.err_<code>), les autres (secret refuse, requete incomplete :
# une faute de configuration chez nous) s'affichent comme « down ».
_CODES = ("unauthorized", "missing", "type", "no_account", "locked", "quota")
SHOWN_ERRORS = ("type", "no_account", "locked", "quota", "down", "file")


def _post_internal_upload(*, internal_url: str, public_url: str, path: str,
                          file_path: str, original_filename: str,
                          mime_type: Optional[str], email: str, booking_id: int,
                          extra: dict) -> dict:
    """POST multipart sur <internal_url><path>.

    Retourne {ok, url, code, reason} : url absolue (adresse publique) si ok ;
    code dans _CODES, ou "down" (service injoignable ou reponse inattendue),
    ou "file" (fichier local disparu)."""
    if not os.path.exists(file_path):
        return {"ok": False, "url": None, "code": "file", "reason": "fichier local introuvable"}
    if not AUBE_INTERNAL_SECRET:
        log.warning("push %s : AUBE_INTERNAL_SECRET absent", internal_url)
        return {"ok": False, "url": None, "code": "down", "reason": "secret interne absent"}

    data = {
        "email": email,
        "source": "aubepilot",
        "source_booking_id": str(booking_id),
        **extra,
    }
    try:
        with open(file_path, "rb") as fh:
            r = requests.post(
                f"{internal_url.rstrip('/')}{path}",
                data=data,
                files={"file": (original_filename, fh, mime_type or "application/octet-stream")},
                headers={"X-Internal-Secret": AUBE_INTERNAL_SECRET},
                timeout=_TIMEOUT,
            )
    except requests.RequestException as exc:
        log.warning("push %s failed: %s", internal_url, exc)
        return {"ok": False, "url": None, "code": "down", "reason": f"service injoignable : {exc}"}

    try:
        body = r.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code >= 400 or not body.get("ok"):
        code = body.get("code") if body.get("code") in _CODES else "down"
        if code == "unauthorized":
            log.error("push %s : secret interne refuse (%s)", internal_url, r.status_code)
        else:
            log.warning("push %s : %s %s", internal_url, r.status_code, body.get("error"))
        return {"ok": False, "url": None, "code": code,
                "reason": body.get("error") or f"refus du service ({r.status_code})"}

    rel = body.get("url") or ""
    url = f"{public_url.rstrip('/')}{rel}" if rel.startswith("/") else (rel or public_url)
    return {"ok": True, "url": url, "code": None, "reason": None}


def push_to_aubedrive(deliverable: dict, client_email: str, booking_id: int) -> dict:
    """Envoie un livrable vers AubeDrive (drive.aubeetoilee.com), dossier
    « AubePilot ». Tous les types de fichiers acceptes."""
    return _post_internal_upload(
        internal_url=AUBEDRIVE_INTERNAL_URL,
        public_url=AUBEDRIVE_URL,
        path="/api/files/internal/upload",
        file_path=os.path.join(UPLOAD_DIR, deliverable["stored_filename"]),
        original_filename=deliverable["original_filename"],
        mime_type=deliverable.get("mime_type"),
        email=client_email,
        booking_id=booking_id,
        extra={"folder_name": "AubePilot"},
    )


def push_to_aubephotos(deliverable: dict, client_email: str, booking_id: int) -> dict:
    """Envoie un livrable vers AubePhotos (photos.aubeetoilee.com), album
    « AubePilot ». Photos et videos seulement."""
    if deliverable.get("kind") not in PHOTOS_KINDS:
        return {"ok": False, "url": None, "code": "type",
                "reason": "AubePhotos ne prend que les photos et les videos"}
    return _post_internal_upload(
        internal_url=AUBEPHOTOS_INTERNAL_URL,
        public_url=AUBEPHOTOS_URL,
        path="/api/photos/internal/upload",
        file_path=os.path.join(UPLOAD_DIR, deliverable["stored_filename"]),
        original_filename=deliverable["original_filename"],
        mime_type=deliverable.get("mime_type"),
        email=client_email,
        booking_id=booking_id,
        extra={"album": "AubePilot"},
    )
