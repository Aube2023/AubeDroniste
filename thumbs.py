"""Miniatures WebP des images publiques de /media.

Une photo de profil ou de couverture envoyee depuis un telephone pese souvent
0,5 a 1,5 Mo, alors qu'une carte de l'annuaire l'affiche en rond de 56 px ou
en bandeau de 120 px de haut. Les pages servent donc une copie reduite au
format WebP, a l'adresse /media/w<largeur>/<nom d'origine>.webp.

La copie est fabriquee a la premiere demande puis gardee sur disque dans
UPLOAD_DIR/_thumbs/w<largeur>/. L'original reste intact : la visionneuse plein
ecran (data-full) et og:image le gardent.

Regles :
- largeurs fixes (WIDTHS) : personne ne peut faire fabriquer une taille
  arbitraire ;
- une miniature n'est servie que si l'original existe encore : supprimer une
  photo la rend aussitot introuvable, meme si sa copie traine sur le disque ;
- si Pillow manque ou ne sait pas lire le fichier, on renvoie l'original.
"""
import logging
import os
import shutil
import threading
from typing import Optional

import config

log = logging.getLogger("aubepilot.thumbs")

# Largeurs servies, en pixels reels (ecran retina compris) :
# 160 avatars, 320 grande photo de profil et logos, 640 bandeaux des cartes,
# 1280 portfolio, 1920 couverture pleine largeur de la fiche.
WIDTHS = (160, 320, 640, 1280, 1920)
# Formats que l'on reduit. Le GIF (souvent anime) et la video passent tels quels.
RASTER_EXT = {"jpg", "jpeg", "png", "webp"}
QUALITY = 80
DIR_NAME = "_thumbs"


def _root() -> str:
    return os.path.join(config.UPLOAD_DIR, DIR_NAME)


def _strip(rel: str) -> str:
    """'uploads/x.jpg' ou 'x.jpg' -> 'x.jpg' (chemin relatif a UPLOAD_DIR)."""
    rel = rel or ""
    return rel[len("uploads/"):] if rel.startswith("uploads/") else rel


def is_raster(name: str) -> bool:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in RASTER_EXT


def url(rel: str, width: int) -> str:
    """Adresse de la miniature, ou de l'original si le format ne s'y prete pas."""
    name = _strip(rel)
    if not name:
        return ""
    if width not in WIDTHS or not is_raster(name):
        return "/media/" + name
    return f"/media/w{width}/{name}.webp"


def path_for(name: str, width: int) -> str:
    return os.path.join(_root(), f"w{width}", name + ".webp")


def ensure(name: str, width: int) -> Optional[str]:
    """Chemin d'une miniature a jour de `name` (relatif a UPLOAD_DIR), fabriquee
    au besoin. None si l'original manque ou ne se lit pas."""
    if width not in WIDTHS or not is_raster(name):
        return None
    src = os.path.join(config.UPLOAD_DIR, name)
    try:
        src_mtime = os.path.getmtime(src)
    except OSError:
        return None
    dst = path_for(name, width)
    try:
        if os.path.getmtime(dst) >= src_mtime:
            return dst
    except OSError:
        pass
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    # Un fichier temporaire par appel puis os.replace : deux workers qui
    # fabriquent la meme miniature en meme temps ne s'ecrasent pas a moitie.
    tmp = f"{dst}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with Image.open(src) as im:
            # JPEG : decodage directement a l'echelle utile (bien plus rapide
            # sur une photo de 12 Mpx).
            im.draft("RGB", (width, width * 4))
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "transparency" in im.info or im.mode in ("LA", "PA") else "RGB")
            if im.width > width:
                im = im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)
            im.save(tmp, "WEBP", quality=QUALITY, method=4)
        os.replace(tmp, dst)
        return dst
    except Exception as exc:  # fichier abime, format exotique, disque plein
        log.warning("miniature %s w%s : %s", name, width, exc)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None


def remove(rel: str) -> None:
    """Efface les miniatures d'un fichier (toutes largeurs). Silencieux."""
    name = _strip(rel)
    if not name:
        return
    for w in WIDTHS:
        try:
            os.remove(path_for(name, w))
        except OSError:
            pass


def remove_matching(prefixes: tuple, folder: str = "") -> int:
    """Efface, dans chaque largeur, les miniatures dont le nom commence par
    l'un des `prefixes` et le sous-dossier `folder` entier (suppression d'un
    compte). Retourne le nombre de fichiers retires."""
    removed = 0
    for w in WIDTHS:
        base = os.path.join(_root(), f"w{w}")
        if not os.path.isdir(base):
            continue
        if folder:
            full = os.path.join(base, folder)
            if os.path.isdir(full):
                removed += sum(len(f) for _, _, f in os.walk(full))
                shutil.rmtree(full, ignore_errors=True)
        for entry in os.listdir(base):
            full = os.path.join(base, entry)
            if prefixes and entry.startswith(prefixes) and os.path.isfile(full):
                try:
                    os.remove(full)
                    removed += 1
                except OSError:
                    pass
    return removed
