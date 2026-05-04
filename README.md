# AubeDroniste

Marketplace souveraine type "Uber pour drones" : met en relation des
**dronistes certifiés** (formations DGAC / EASA / Transport Canada / FAA /
ASECNA…) et des **clients** qui publient des missions (photo, vidéo,
cartographie RTK, inspection thermique, mariages, agriculture, etc.).
Recherche **géolocalisée par pays / ville / rayon**, enchères, messagerie,
avis bidirectionnels.

Fait partie de l'écosystème [L'Aube Étoilée](https://aubeetoilee.com).
Auth partagée `@aubemail.com` (PAM en prod, fallback dev local sur macOS).

## Stack

| Composant | Choix |
|---|---|
| Backend | Flask 3, Python 3.11+ |
| Stockage | SQLite (WAL) — facile à passer à PostgreSQL |
| Auth | PAM partagée (Linux) + fallback dev (`.dev_passwords`) |
| Frontend | Templates Jinja + CSS « Aube » + JS vanilla |
| API | JSON (`/api/dronistes`, `/api/missions`, `/api/stats`) |
| Port | **5034** |
| Domaine prod prévu | `droniste.aubeetoilee.com` |

## Démarrage

```bash
./run.sh
# ou directement
python3 app.py
```

À la première exécution : crée `aubedroniste.db` à partir de `schema.sql`
puis lance `seed.py` (8 comptes démo, mdp `demo` ; 5 dronistes répartis
France/Canada/Maroc/Algérie/Côte d'Ivoire, 3 missions ouvertes).

Ouvrir : <http://127.0.0.1:5034>

## Comptes de démo

| login | rôle | pays |
|---|---|---|
| `amine.benali` | droniste | Maroc |
| `sophie.tremblay` | droniste | Canada |
| `yacine.haddad` | droniste | Algérie |
| `linh.dupont` | droniste | France |
| `kofi.adjei` | droniste | Côte d'Ivoire |
| `client.alpha` | client | France |
| `client.beta` | client | Canada |
| `client.gamma` | client | Tunisie |

Mot de passe pour tous : `demo`.

## Modèle métier

### Comptes & rôles
Un compte peut être `client`, `droniste` ou `both`. Un client devient
droniste à la volée depuis l'espace (`/espace/droniste`).

### Profil droniste
- accroche, bio, années d'expérience
- tarif horaire / journée + devise
- rayon de déplacement (km), accepte missions distantes / urgentes
- assurance RC pro (compagnie + n° police)
- spécialités (15 codes : photo, vidéo, mapping, RTK, inspection…)
- **territoires opérés** (un pilote licencié dans plusieurs pays)
- langues parlées
- portfolio externe

### Formations / certifications (`pilot_certifications`)
Autorité (DGAC, EASA, Transport Canada, FAA, DGAC Maroc, ANAC TN,
ASECNA, OFAC, autre…) + intitulé (STS-01, A2, Avancé, Part 107) +
référence + dates + justificatif PDF. Validation manuelle par admin
(`is_verified`).

### Drones (`pilot_drones`)
Catégorie (micro, FPV, pro caméra, cinéma, RTK, agri, inspection,
livraison, VTOL) + marque/modèle/n° série + poids/charge utile/autonomie
+ capacités (caméra 4K/6K/8K, thermique, lidar, RTK, multispectrale,
zoom, projecteur, largage, épandage, etc.).

### Missions (`missions`)
Type (15), pays/région/ville/lat/lng/adresse, fourchette de budget,
devise, durée, dates, urgence, exigences (assurance, capacités,
certifications). Statuts : open → assigned → in_progress → done /
cancelled.

### Enchères (`bids`) → Réservations (`bookings`)
Un droniste soumissionne avec prix + délai + message. Le client accepte
une enchère → création d'une réservation, autres enchères rejetées,
mission passée en `assigned`. Commission plateforme **30%**
(`PLATFORM_FEE_PCT`).

### Avis (`reviews`)
Bidirectionnels (client note pilote ET inverse) sur 5 étoiles +
commentaire, après `completed`.

### Messagerie (`messages`)
Thread par mission entre client et pilote, marquage lu/non-lu.

## API JSON

```
GET /api/stats
GET /api/dronistes?country=&city=&mission_type=&capability=&lat=&lng=&radius_km=&min_rating=&only_available=
GET /api/missions ?country=&mission_type=&status=&lat=&lng=&radius_km=&only_urgent=
```

## Auth & sécurité

- Mémoire `feedback_auth` : **on ne touche JAMAIS aux mots de passe PAM**.
  En production Linux, l'auth délègue à `pam.authenticate(...)`. En dev /
  macOS, fallback SHA-256 + sel dans `.dev_passwords` (chmod 600).
- Mémoire `feedback_email_domain` : tous les comptes obtiennent un email
  `<username>@aubemail.com`. Le domaine `aubeetoilee.com` ne sert qu'aux
  sous-domaines services.
- Sessions serveur signées (itsdangerous), cookie `httpOnly`, `SameSite=Lax`.
- `MAX_CONTENT_LENGTH` = 10 Mo (uploads certifs / photos drones).

## Architecture des fichiers

```
config.py         constantes : port, devises, types missions, certifs, drones
schema.sql        14 tables SQLite
db.py             connexion par requête, haversine_km
auth.py           PAM + fallback + sessions + create_user
services.py       toute la logique métier (sans ORM, SQL à plat)
app.py            Flask : routes, templates, API JSON
seed.py           comptes / missions de démo
templates/        12 vues Jinja héritant de base.html
static/css/style.css   thème sombre Aube
static/js/app.js       useMyLocation()
static/img/logo.svg    logo drone schématique
run.sh            venv + deps + démarre
```

## Roadmap (non livré)

- Vérification administrative des certifications (back-office)
- Paiements via **AubePay** (escrow → libération à `completed`)
- Notifications email (alertes nouvelle mission dans rayon)
- Carte interactive MapLibre (déjà présente sur AubeMonument / AubeSIG)
- Application mobile Flutter (réutiliser le pattern AubeSIG offline-first)
- Webhooks vers AubeStatus
