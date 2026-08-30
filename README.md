# AubePilot

Marketplace souveraine type "Uber pour drones" : met en relation des
**pilotes certifiés** (formations DGAC / EASA / Transport Canada / FAA /
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
| Langues | **FR + EN** (sélecteur topbar, cookie `aube_lang`) |
| Thèmes | **Aube (clair) + Nuit (sombre)** — toggle topbar |
| Mailer | SMTP en prod, dump `.eml` local en dev |
| API | JSON (`/api/pilotes`, `/api/missions`, `/api/near`, `/api/country-breakdown`, `/api/stats`) |
| Port | **5034** |
| Domaine prod prévu | `pilot.aubeetoilee.com` |

## Démarrage

```bash
./run.sh
# ou directement
python3 app.py
```

À la première exécution : crée `aubepilot.db` à partir de `schema.sql`
puis lance `seed.py` (8 comptes démo, mdp `demo` ; 5 pilotes répartis
France/Canada/Maroc/Algérie/Côte d'Ivoire, 3 missions ouvertes).

Ouvrir : <http://127.0.0.1:5034>

## Comptes de démo

| login | rôle | pays |
|---|---|---|
| `amine.benali` | pilote | Maroc |
| `sophie.tremblay` | pilote | Canada |
| `yacine.haddad` | pilote | Algérie |
| `linh.dupont` | pilote | France |
| `kofi.adjei` | pilote | Côte d'Ivoire |
| `client.alpha` | client | France |
| `client.beta` | client | Canada |
| `client.gamma` | client | Tunisie |

Mot de passe pour tous : `demo`.

## Modèle métier

### Comptes & rôles
Un compte peut être `client`, `pilote` ou `both`. Un client devient
pilote à la volée depuis l'espace (`/espace/pilote`).

### Profil pilote
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
Un pilote soumissionne avec prix + délai + message. Le client accepte
une enchère → création d'une réservation, autres enchères rejetées,
mission passée en `assigned`. Commission plateforme **dégressive**
(`PLATFORM_FEE_TIERS` : 20 % missions 1-3, 15 % missions 4-9, 10 % dès la
10e mission terminée entre le même client et le même pilote ; taux figé sur
le booking dans `platform_fee_pct`).

### Annulation
- Client, avant paiement : libre. Jusqu'à 2 h après paiement : remboursement
  intégral (grâce). Ensuite : frais de service 10 % (max 150) retenus par la
  plateforme — le contact du pilote a été révélé. Préavis < 24 h : + 25 % du
  devis versés au pilote (Transfer Stripe immédiat, `stripe_transfer_id`).
- Pilote (`/reservations/<id>/annuler-pilote`) : remboursement intégral du
  client, devis retiré, mission remise en ligne, courriel au client.

### Avis (`reviews`)
Bidirectionnels (client note pilote ET inverse) sur 5 étoiles +
commentaire, après `completed`.

### Messagerie (`messages`)
Thread par mission entre client et pilote, marquage lu/non-lu.

### Recherche « autour de » (code postal / adresse / ville)
`near=` sur `/pilotes`, `/missions`, `/api/pilotes`, `/api/missions` est
géocodé côté serveur (`geocode.py` : Nominatim/OpenStreetMap, sans clé,
1 req/s max, cache SQLite `geocode_cache` positif ET négatif). Résultats
triés du plus proche au plus loin, `radius_km` (25 → 500) + `near_only=1`
pour exclure hors rayon ; la carte affiche le centre et le cercle de rayon.

### Filtres « confiance » (annuaire pro)
`only_verified=1` (brevet contrôlé par l'admin ou compte vérifié),
`only_insured=1` (RC pro déclarée), `authority=<code>` (ex. `Transport Canada`).
Les cartes pilote affichent les autorités des brevets vérifiés (`✓ DGAC`).

### FAQ, contact
- `/faq` : FAQ bilingue centralisée dans `content.py` (mêmes textes que
  l'aperçu de l'accueil et le JSON-LD `FAQPage`) ; les chiffres (commission,
  auto-libération, annulation) viennent de `config.py`, jamais en dur.
- `/contact` : formulaire (CSRF, rate limit 3/min, pot de miel) → courriel à
  `AUBEPILOT_CONTACT_EMAIL` (défaut `rprp@aubemail.com`, Reply-To expéditeur)
  + accusé de réception bilingue. Réseaux sociaux du pied de page via
  `AUBEPILOT_SOCIAL_LINKEDIN|INSTAGRAM|FACEBOOK|YOUTUBE` (rien si vide).

## API JSON

```
GET /api/stats
GET /api/geocode?q=<code postal | adresse | ville>&country=
GET /api/pilotes?near=&radius_km=&near_only=&country=&city=&mission_type=&capability=&lat=&lng=&min_rating=&only_available=&only_verified=&only_insured=&authority=
GET /api/missions?near=&radius_km=&near_only=&country=&mission_type=&status=&lat=&lng=&only_urgent=
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
content.py        FAQ bilingue (page /faq, accueil, JSON-LD)
geocode.py        code postal / adresse -> lat,lng (Nominatim + cache)
schema.sql        15 tables SQLite
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

## Documentation pour développeurs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — flux internes, conventions, hooks
- [`deploy/README.md`](deploy/README.md) — procédure de mise en production
- [`tests/`](tests/) — `pytest -q` pour la suite de smoke tests

## Tester l'envoi d'emails

En dev (sans SMTP configuré), les emails sont écrits dans `data/mail/*.eml` :

```bash
python scripts/send_test_email.py welcome demo@aubemail.com
python scripts/send_test_email.py new_bid demo@aubemail.com
python scripts/send_test_email.py bid_accepted demo@aubemail.com
python scripts/send_test_email.py new_message demo@aubemail.com

# Inspecter le résultat :
ls -la data/mail/
open data/mail/*.eml      # ouverture dans Mail.app sur macOS
```

Configurer un vrai SMTP via les variables d'env `SMTP_HOST`, `SMTP_PORT`, etc.
(voir [`ARCHITECTURE.md`](ARCHITECTURE.md#variables-d-environnement)).

## Roadmap (non livré)

- Vérification administrative des certifications (back-office)
- Paiements via **AubePay** (escrow → libération à `completed`)
- Notifications email (alertes nouvelle mission dans rayon)
- ~~Carte interactive MapLibre~~ (faite : OpenFreeMap, accueil + annuaire + missions)
- Application mobile Flutter (réutiliser le pattern AubeSIG offline-first)
- Webhooks vers AubeStatus
