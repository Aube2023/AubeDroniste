"""Taxes de vente canadiennes sur AubePilot : TPS, TVH, TVQ, TVP.

Deux fournitures distinctes, deux regles, plus la TVP percue par la
plateforme (3.) :

1. La MISSION, fournie par le pilote au client. Un pilote inscrit aux
   fichiers (numero de TPS/TVH, et de TVQ pour le Quebec) doit percevoir ses
   taxes : elles s'ajoutent a son devis, le client les paie avec le prix, et
   elles sont reversees en entier au pilote, qui les declare et les remet.
   La commission se calcule sur le prix AVANT taxes. Un pilote non inscrit
   (petit fournisseur : au plus 30 000 $ de fournitures taxables sur quatre
   trimestres civils, ou dans un seul trimestre) n'en facture pas.
   Lieu de la fourniture (supply_province, DORS/2010-117 art. 13 et 14,
   LTVQ art. 22.15.0.1 a 22.15.0.3) : en general la province de l'adresse
   du CLIENT ; pour un service lie a un immeuble (inspection, leve,
   cartographie, suivi de chantier : REAL_PROPERTY_TYPES), celle du bien ;
   sans adresse canadienne du client, la ou le service est rendu. Mission
   hors du Canada : rien.
   Plateforme reputee fournisseur (ligne by="platform", numeros de L'Aube
   Etoilee) : TVQ d'une fourniture faite au Quebec par un pilote qui n'y
   reside pas et n'est pas inscrit a la TVQ (LTVQ art. 477.5.2), et TPS/TVH
   d'un pilote etabli hors du Canada non inscrit (LTA art. 211.13). La
   commission ne porte alors pas cette taxe.

2. La COMMISSION, fournie par L'Aube Etoilee inc. (inscrite) au pilote.
   Pilote etabli au Canada : selon sa province (adresse du destinataire).
   Pilote a l'etranger : detaxee si sa mission est hors du Canada ; sinon
   le service (« obtenir des commandes ») n'est pas exporte (ann. VI,
   partie V, art. 7 f) et 23 d) ; LTVQ art. 185) : TPS de 5 % s'il est
   inscrit a la TPS (pas d'adresse canadienne : service rendu au Quebec),
   et TVQ si la mission est au Quebec et qu'il est inscrit a la TVQ.
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
# Services lies a un immeuble : taxes de la province ou se trouve le bien
# (DORS/2010-117 art. 14, LTVQ art. 22.15.0.3 ; ARC, memorandum 3-3-6-1 :
# inspection, arpentage). Les autres suivent l'adresse du client.
REAL_PROPERTY_TYPES = frozenset({
    "inspection", "toiture", "ouvrage_art", "eolienne", "photovoltaique", "ligne_ht",
    "pipeline", "ferroviaire", "industriel", "thermographie",
    "mapping", "3d", "topographie", "volumes", "btp",
})
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


def supply_province(mission: dict, client: Optional[dict] = None,
                    pilot: Optional[dict] = None) -> Optional[str]:
    """Province ou la mission est fournie, pour les taxes : celle du bien
    pour un service lie a un immeuble, sinon celle de l'adresse du client ;
    a defaut, la ou la mission a lieu, puis la province du pilote. None
    pour une mission hors du Canada."""
    mission = mission or {}
    if not is_canada(mission.get("country")):
        return None
    here = mission_province(mission)
    if (mission.get("mission_type") or "") in REAL_PROPERTY_TYPES:
        return here or person_province(client) or person_province(pilot)
    return person_province(client) or here or person_province(pilot)


def platform_deemed(pilot: dict, province: Optional[str]) -> tuple:
    """(TPS/TVH, TVQ) : la plateforme est-elle reputee fournir la mission a
    la place du pilote ? TPS/TVH : pilote etabli hors du Canada, sans numero
    de TPS (LTA art. 211.13). TVQ : fourniture faite au Quebec par un pilote
    etabli hors du Quebec, sans numero de TVQ (LTVQ art. 477.5.2), meme petit
    fournisseur. Pays ou province du pilote inconnus : pas de presomption."""
    pilot = pilot or {}
    if not province or not (pilot.get("country") or "").strip():
        return (False, False)
    abroad = not is_canada(pilot.get("country"))
    home = None if abroad else person_province(pilot)
    gst = abroad and not pilot.get("gst_number")
    qst = (province == "QC" and not pilot.get("qst_number")
           and (abroad or (home is not None and home != "QC")))
    return (gst, qst)


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


def mission_taxes(price, pilot: dict, mission: dict, client: Optional[dict] = None) -> dict:
    """Taxes ajoutees au prix du pilote pour cette mission.

    {"province", "lines": [{code, rate, amount, number[, by]}], "total"} :
    celles du pilote inscrit (TPS/TVH, TVQ), puis celles que la plateforme
    percoit elle-meme (by="platform") : TPS/TVH et TVQ dont elle est reputee
    fournisseur (platform_deemed), TVP ou elle est inscrite. Province :
    supply_province ; a defaut, le Quebec si le pilote est inscrit a la TVQ.
    Mission hors du Canada : aucune ligne."""
    from config import PLATFORM_GST_NUMBER, PLATFORM_QST_NUMBER
    gst = (pilot or {}).get("gst_number") or ""
    qst = (pilot or {}).get("qst_number") or ""
    price = float(price or 0)
    mission = mission or {}
    if price <= 0 or not is_canada(mission.get("country")):
        return {"province": None, "lines": [], "total": 0.0}
    prov = supply_province(mission, client, pilot) or ("QC" if qst else None)
    lines = [{"code": code, "rate": rate, "amount": _part(price, rate), "number": num}
             for code, rate, num in _rates(prov, gst, qst)]
    deemed_gst, deemed_qst = platform_deemed(pilot, prov)
    for code, rate, num in _rates(prov, PLATFORM_GST_NUMBER if deemed_gst else "",
                                  PLATFORM_QST_NUMBER if deemed_qst else ""):
        lines.append({"code": code, "rate": rate, "amount": _part(price, rate),
                      "number": num, "by": "platform"})
    pst_number = platform_pst_number(prov)
    if pst_number and mission_pst_due(prov, mission.get("mission_type")):
        rate = PST_RATES[prov]
        lines.append({"code": PST_CODES[prov], "rate": rate, "amount": _part(price, rate),
                      "number": pst_number, "by": "platform"})
    return {"province": prov, "lines": lines, "total": _cents(sum(l["amount"] for l in lines))}


def platform_collected(lines, codes=None) -> float:
    """Part des lignes percue et remise par la plateforme (TVP, taxes dont
    elle est reputee fournisseur) : elle reste a L'Aube Etoilee au
    versement, hors du net du pilote. codes : limiter a ces codes."""
    return _cents(sum(float(l.get("amount") or 0) for l in loads(lines)
                      if l.get("by") == "platform" and (codes is None or l.get("code") in codes)))


SALES_CODES = ("GST", "HST", "QST")      # TPS/TVH/TVQ (la TVP n'en fait pas partie)


def deemed_tax_in(amount, lines) -> list:
    """Taxes comprises dans un dedommagement d'annulation garde sur une
    fourniture dont la plateforme est reputee fournisseur : fraction de
    chaque loi, taux/(100 + taux) (LTA art. 182, LTVQ art. 318)."""
    out = []
    for l in loads(lines):
        if l.get("by") == "platform" and l.get("code") in SALES_CODES:
            r = Decimal(str(l["rate"]))
            out.append({"code": l["code"], "rate": l["rate"], "number": l.get("number"),
                        "amount": _cents(Decimal(str(amount)) * r / (100 + r))})
    return out


def commission_taxes(fee, pilot: dict, mission: Optional[dict] = None,
                     client: Optional[dict] = None) -> dict:
    """Part de taxes COMPRISE dans la commission (fournie par la plateforme
    au pilote), voir 2. ci-dessus. Pilote au Canada : province du pilote,
    sinon celle de la mission, sinon le Quebec (province de la plateforme) ;
    pas de TVQ si la plateforme est reputee fournir la mission au Quebec.
    Pilote a l'etranger : detaxee (zero_rated) si la mission est hors du
    Canada ; sinon TPS si inscrit, TVQ si mission au Quebec et inscrit a la
    TVQ, rien s'il n'est inscrit nulle part (la plateforme percoit alors les
    taxes de la mission). Pilote en C.-B. : TVP de 7 % en plus une fois la
    plateforme inscrite."""
    from config import PLATFORM_GST_NUMBER, PLATFORM_QST_NUMBER
    fee = float(fee or 0)
    pilot = pilot or {}
    if fee <= 0:
        return {"province": None, "lines": [], "total": 0.0, "zero_rated": False}
    if not is_canada(pilot.get("country")):
        where = supply_province(mission or {}, client, pilot)
        if not where:
            return {"province": None, "lines": [], "total": 0.0, "zero_rated": True}
        rates = []
        if pilot.get("gst_number"):
            rates.append(("GST", GST_RATE, PLATFORM_GST_NUMBER))
        if where == "QC" and pilot.get("qst_number"):
            rates.append(("QST", QST_RATE, PLATFORM_QST_NUMBER))
        return _included(fee, "QC", rates)
    prov = person_province(pilot) or mission_province(mission or {}) or "QC"
    rates = _rates(prov, PLATFORM_GST_NUMBER, PLATFORM_QST_NUMBER)
    if prov == "BC" and platform_pst_number("BC"):
        rates.append(("PST", PST_RATES["BC"], platform_pst_number("BC")))
    return _included(fee, prov, rates)


def _included(fee, prov, rates) -> dict:
    """Taxes comprises dans `fee` aux taux `rates` [(code, taux, numero)]."""
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
