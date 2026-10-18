"""Taxes de vente canadiennes sur AubePilot : TPS, TVH, TVQ, TVP.

Deux fournitures distinctes, deux regles, plus la TVP percue par la
plateforme (3.) :

1. La MISSION, fournie par le pilote au client. Un pilote inscrit aux
   fichiers (numero de TPS/TVH, et de TVQ pour le Quebec) doit percevoir ses
   taxes : elles s'ajoutent a son devis, le client les paie avec le prix, et
   elles sont reversees en entier au pilote, qui les declare et les remet.
   La commission se calcule sur le prix AVANT taxes. Un pilote non inscrit
   (petit fournisseur : au plus 30 000 $ de fournitures taxables sur quatre
   trimestres civils) n'en facture pas : rien n'est ajoute.
   Lieu de la fourniture : la province ou la mission a lieu (service lie a un
   lieu, et presque toujours celle du client). Mission hors du Canada : rien.

2. La COMMISSION, fournie par L'Aube Etoilee inc. (inscrite) au pilote.
   Taxable pour un pilote etabli au Canada, selon sa province (adresse du
   destinataire) ; detaxee pour un pilote a l'etranger (service exporte).
   TAXES COMPRISES dans le taux annonce, comme les frais Stripe : le pilote
   touche toujours le prix moins la commission (CGU art. 11 et 13), et la
   part de taxes qu'elle contient est calculee ici pour etre declaree.

Taux en vigueur (2026) : TPS 5 % ; TVH 13 % en Ontario, 14 % en
Nouvelle-Ecosse (depuis le 2025-04-01), 15 % au Nouveau-Brunswick, a
Terre-Neuve-et-Labrador et a l'Ile-du-Prince-Edouard ; TVQ 9,975 % (calculee
sur le prix avant TPS).

3. La TVP de la Saskatchewan (6 %) et la TVD du Manitoba (7 %) : ces deux
   provinces obligent la plateforme qui encaisse le paiement (« marketplace
   facilitator ») a s'inscrire et a percevoir la taxe sur les ventes taxables
   faites par son intermediaire, que le vendeur soit inscrit ou non
   (Sask. IN 2020-08, Man. RST 064). AubePilot l'ajoute donc au devis, la
   garde au versement et la remet : ligne marquee by=platform, hors du net
   pilote. Seulement si L'Aube Etoilee est inscrite dans la province
   (config.PLATFORM_PST_NUMBERS) : sans numero, rien n'est percu, et
   services.pst_exposure() signale a l'admin les ventes qui l'exigeraient.
   Taxable (prix entier, meme livre en fichiers) : photo et video (Sask.
   PST-25, Man. RST 030), cartographie et 3D (donnees traitees, a faire
   confirmer), suivi de chantier, surveillance. Exempt : inspection de
   batiments et d'ouvrages, agriculture et epandage. Calculee sur le prix
   avant TPS, sans taxe sur taxe.
   Colombie-Britannique : la livraison numerique n'est pas taxable (PST 126),
   rien sur la mission ; la commission (« online marketplace services ») porte
   la TVP de 7 % pour un pilote etabli en C.-B. une fois la plateforme
   inscrite (PST 142 ; seuil de 10 000 $), comprise dans la commission.

Les lignes de taxes sont stockees en JSON sur le devis et la reservation
(bids.taxes, bookings.taxes, bookings.fee_taxes) : [{code, rate, amount,
number[, by]}], code dans GST | HST | QST | PST | RST ; by="platform" pour
une taxe percue et remise par L'Aube Etoilee. Ce qui a ete affiche au client
est ce qui est encaisse, meme si le pilote change ses numeros ensuite.
"""
import json
import math
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

GST_RATE = 5.0
QST_RATE = 9.975
HST_RATES = {"ON": 13.0, "NS": 14.0, "NB": 15.0, "NL": 15.0, "PE": 15.0}
PST_RATES = {"BC": 7.0, "SK": 6.0, "MB": 7.0}
PST_CODES = {"BC": "PST", "SK": "PST", "MB": "RST"}
# Types de mission (config.MISSION_TYPES) sur lesquels la TVP de la Sask. et
# la TVD du Manitoba s'appliquent : voir 3. ci-dessus.
PST_TAXABLE_TYPES = frozenset({
    "photo", "video", "cinema", "reportage", "mariage", "evenement",
    "immobilier", "patrimoine",
    "mapping", "3d", "topographie", "volumes",
    "btp", "surveillance",
})
# Provinces ou la TVP d'une MISSION est percue par la plateforme.
PST_MISSION_PROVINCES = ("SK", "MB")
PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")


def _norm(text) -> str:
    """minuscules, sans accents, ponctuation -> espaces."""
    raw = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", raw.lower()).split())


# Noms de provinces (fr/en) et regions administratives du Quebec : le champ
# « region » d'une mission est libre (« Montérégie », « Ontario », « QC »...).
_NAMES = {
    "QC": ("quebec", "province de quebec", "bas saint laurent", "saguenay lac saint jean",
           "capitale nationale", "mauricie", "estrie", "outaouais", "abitibi temiscamingue",
           "cote nord", "nord du quebec", "gaspesie iles de la madeleine", "gaspesie",
           "chaudiere appalaches", "laval", "lanaudiere", "laurentides", "monteregie",
           "centre du quebec", "montreal"),
    "ON": ("ontario",),
    "NS": ("nova scotia", "nouvelle ecosse"),
    "NB": ("new brunswick", "nouveau brunswick"),
    "NL": ("newfoundland and labrador", "terre neuve et labrador", "newfoundland",
           "terre neuve", "labrador"),
    "PE": ("prince edward island", "ile du prince edouard", "pei"),
    "MB": ("manitoba",),
    "SK": ("saskatchewan",),
    "AB": ("alberta",),
    "BC": ("british columbia", "colombie britannique"),
    "YT": ("yukon",),
    "NT": ("northwest territories", "territoires du nord ouest"),
    "NU": ("nunavut",),
}
_BY_NAME = {name: code for code, names in _NAMES.items() for name in names}
_CODE_ALIASES = {"pq": "QC", "que": "QC", "qc": "QC", "ont": "ON", "nfld": "NL", "pei": "PE",
                 "cb": "BC", "ipe": "PE", "ne": "NS", "tn": "NL", "tno": "NT"}

# Premiere lettre du code postal -> province (X : Nunavut pour X0A-X0C).
_POSTAL_LETTER = {"A": "NL", "B": "NS", "C": "PE", "E": "NB", "G": "QC", "H": "QC", "J": "QC",
                  "K": "ON", "L": "ON", "M": "ON", "N": "ON", "P": "ON", "R": "MB", "S": "SK",
                  "T": "AB", "V": "BC", "Y": "YT"}
_POSTAL_RE = re.compile(r"\b([ABCEGHJKLMNPRSTVXY]\d[A-Z])\s?\d[A-Z]\d\b", re.I)


def is_canada(country) -> bool:
    return _norm(country) in ("canada", "ca")


def province_from_text(text) -> Optional[str]:
    """« Québec », « Montérégie », « ON », « Montréal, QC » -> code ; None sinon."""
    n = _norm(text)
    if not n:
        return None
    if n.upper() in PROVINCES:
        return n.upper()
    if n in _BY_NAME:
        return _BY_NAME[n]
    if n in _CODE_ALIASES:
        return _CODE_ALIASES[n]
    padded = f" {n} "
    for name in sorted(_BY_NAME, key=len, reverse=True):
        if len(name) >= 5 and f" {name} " in padded:
            return _BY_NAME[name]
    last = n.split()[-1]
    if len(n.split()) > 1 and last.upper() in PROVINCES:
        return last.upper()
    return None


def province_from_postal(text) -> Optional[str]:
    m = _POSTAL_RE.search(str(text or "").upper())
    if not m:
        return None
    fsa = m.group(1).upper()
    if fsa[0] == "X":
        return "NU" if fsa[:3] in ("X0A", "X0B", "X0C") else "NT"
    return _POSTAL_LETTER.get(fsa[0])


def province_from_point(lat, lng) -> Optional[str]:
    """Province du centroide de RTA le plus proche (geodata/ca_fsa.csv)."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (41.0 <= lat <= 84.0 and -142.0 <= lng <= -52.0):
        return None
    import geocode
    best, best_d = None, None
    coslat = math.cos(math.radians(lat))
    for flat, flng, _name, prov in geocode._ca_fsa().values():
        d = (flat - lat) ** 2 + ((flng - lng) * coslat) ** 2
        if best_d is None or d < best_d:
            best, best_d = prov, d
    # au-dela d'environ 600 km du centroide le plus proche, on ne devine pas
    return best if best_d is not None and best_d < 30.0 else None


def mission_province(mission: dict) -> Optional[str]:
    """Province d'une mission au Canada : champ region, code postal de
    l'adresse, ville, puis coordonnees. None hors du Canada ou si rien ne
    permet de le dire."""
    if not mission or not is_canada(mission.get("country")):
        return None
    return (province_from_text(mission.get("region"))
            or province_from_postal(mission.get("address"))
            or province_from_text(mission.get("city"))
            or province_from_point(mission.get("lat"), mission.get("lng")))


def person_province(person: dict) -> Optional[str]:
    """Province d'un utilisateur etabli au Canada (ville, coordonnees)."""
    if not person or not is_canada(person.get("country")):
        return None
    return (province_from_text(person.get("city"))
            or province_from_point(person.get("lat"), person.get("lng")))


# ---------------------------------------------------------------------------
# Numeros d'inscription
# ---------------------------------------------------------------------------

def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def normalize_gst(raw) -> str:
    """Numero d'entreprise + compte de TPS/TVH : « 123456789 RT0001 ». Vide
    -> "". ValueError si la forme ou le chiffre de controle (Luhn, celui du
    numero d'entreprise de l'ARC) ne va pas."""
    s = re.sub(r"[\s\-.]", "", str(raw or "")).upper()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{9})(?:RT(\d{4}))?", s)
    if not m or not _luhn_ok(m.group(1)):
        raise ValueError("gst")
    return f"{m.group(1)} RT{m.group(2) or '0001'}"


def normalize_qst(raw) -> str:
    """Numero d'inscription a la TVQ : « 1234567890 TQ0001 ». Vide -> ""."""
    s = re.sub(r"[\s\-.]", "", str(raw or "")).upper()
    if not s:
        return ""
    m = re.fullmatch(r"(\d{10})(?:TQ(\d{4}))?", s)
    if not m:
        raise ValueError("qst")
    return f"{m.group(1)} TQ{m.group(2) or '0001'}"


# ---------------------------------------------------------------------------
# Calcul
# ---------------------------------------------------------------------------

def _cents(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _part(base, rate) -> float:
    """base x taux %, arrondi au cent le plus proche (demi vers le haut), en
    decimal : en flottant, 300 x 9,975 % donne 29,92499... et perdrait un cent."""
    return _cents(Decimal(str(base)) * Decimal(str(rate)) / Decimal(100))


def _rates(province: Optional[str], gst: str, qst: str) -> list:
    out = []
    if gst:
        if province in HST_RATES:
            out.append(("HST", HST_RATES[province], gst))
        else:
            out.append(("GST", GST_RATE, gst))
    if qst and province == "QC":
        out.append(("QST", QST_RATE, qst))
    return out


def platform_pst_number(province: Optional[str]) -> str:
    """Numero de TVP/TVD de L'Aube Etoilee dans cette province ; vide tant
    qu'elle n'y est pas inscrite (rien n'est alors percu)."""
    from config import PLATFORM_PST_NUMBERS
    return (PLATFORM_PST_NUMBERS or {}).get(province or "", "") or ""


def mission_pst_due(province: Optional[str], mission_type) -> bool:
    """La TVP de cette mission revient-elle a la plateforme (inscrite ou
    non) ? Sask. et Manitoba, types taxables seulement."""
    return province in PST_MISSION_PROVINCES and (mission_type or "") in PST_TAXABLE_TYPES


def mission_taxes(price, pilot: dict, mission: dict) -> dict:
    """Taxes ajoutees au prix du pilote pour cette mission.

    {"province", "lines": [{code, rate, amount, number[, by]}], "total"} :
    celles du pilote inscrit (TPS/TVH, TVQ), puis la TVP percue par la
    plateforme (by="platform") ou elle est inscrite. Mission hors du Canada :
    aucune ligne. Province introuvable : celle du pilote ; a defaut, le
    Quebec s'il est inscrit a la TVQ."""
    gst = (pilot or {}).get("gst_number") or ""
    qst = (pilot or {}).get("qst_number") or ""
    price = float(price or 0)
    mission = mission or {}
    if price <= 0 or not is_canada(mission.get("country")):
        return {"province": None, "lines": [], "total": 0.0}
    prov = mission_province(mission) or person_province(pilot) or ("QC" if qst else None)
    lines = [{"code": code, "rate": rate, "amount": _part(price, rate), "number": num}
             for code, rate, num in _rates(prov, gst, qst)]
    pst_number = platform_pst_number(prov)
    if pst_number and mission_pst_due(prov, mission.get("mission_type")):
        rate = PST_RATES[prov]
        lines.append({"code": PST_CODES[prov], "rate": rate, "amount": _part(price, rate),
                      "number": pst_number, "by": "platform"})
    return {"province": prov, "lines": lines, "total": _cents(sum(l["amount"] for l in lines))}


def platform_collected(lines) -> float:
    """Part des lignes percue et remise par la plateforme (TVP) : elle reste
    a L'Aube Etoilee au versement, hors du net du pilote."""
    return _cents(sum(float(l.get("amount") or 0) for l in loads(lines)
                      if l.get("by") == "platform"))


def commission_taxes(fee, pilot: dict, mission: Optional[dict] = None) -> dict:
    """Part de taxes COMPRISE dans la commission (fournie par la plateforme
    au pilote). Pilote hors du Canada : detaxee (lignes vides, zero_rated).
    Province du pilote, sinon celle de la mission, sinon le Quebec (province
    de la plateforme). Pilote en C.-B. : TVP de 7 % en plus une fois la
    plateforme inscrite."""
    from config import PLATFORM_GST_NUMBER, PLATFORM_QST_NUMBER
    fee = float(fee or 0)
    if fee <= 0:
        return {"province": None, "lines": [], "total": 0.0, "zero_rated": False}
    if not is_canada((pilot or {}).get("country")):
        return {"province": None, "lines": [], "total": 0.0, "zero_rated": True}
    prov = person_province(pilot) or mission_province(mission or {}) or "QC"
    rates = _rates(prov, PLATFORM_GST_NUMBER, PLATFORM_QST_NUMBER)
    if prov == "BC" and platform_pst_number("BC"):
        rates.append(("PST", PST_RATES["BC"], platform_pst_number("BC")))
    total_rate = sum(Decimal(str(r)) for _c, r, _n in rates)
    base = Decimal(str(fee)) * 100 / (100 + total_rate)
    lines = [{"code": code, "rate": rate, "amount": _part(base, rate), "number": num}
             for code, rate, num in rates]
    return {"province": prov, "lines": lines, "total": _cents(sum(l["amount"] for l in lines)),
            "zero_rated": False}


def dumps(lines) -> Optional[str]:
    return json.dumps(lines, ensure_ascii=False) if lines else None


def loads(raw) -> list:
    """Lignes stockees (JSON) -> liste ; tolere None et le JSON abime."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return out if isinstance(out, list) else []


def rates_label(lines, label, comma: bool = False) -> str:
    """« TPS 5 % + TVQ 9,975 % » (label : code -> nom affiche ; comma :
    virgule decimale)."""
    def pct(r):
        s = f"{float(r):g}"
        return s.replace(".", ",") if comma else s
    return " + ".join(f"{label(l['code'])} {pct(l['rate'])} %" for l in lines)
