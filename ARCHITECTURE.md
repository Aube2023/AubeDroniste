# Architecture AubePilot

Document destiné aux développeurs qui reprennent ou étendent le projet.
Pour le déploiement opérationnel, voir [`deploy/README.md`](deploy/README.md).

---

## Arborescence

```
AubePilot/
├── app.py                 Point d'entree Flask : routes, hooks, errors
├── config.py              Constantes + chemins (DATA_DIR, etc.) — env vars
├── db.py                  Connexion SQLite par requete + haversine_km
├── auth.py                PAM (Linux) + fallback dev + sessions itsdangerous
├── i18n.py                Traductions 9 langues, resolve_lang via cookie
├── mailer.py              SMTP + fallback dump local (data/mail/*.eml)
├── services.py            Logique metier (pilotes, missions, encheres, bookings…)
├── schema.sql             14 tables SQLite, foreign keys, index, WAL
├── wsgi.py                Entry point gunicorn
├── pyrightconfig.json
├── requirements.txt
├── run.sh                 dev local (venv + seed + flask debug)
├── README.md              Vue produit + comment lancer
├── ARCHITECTURE.md        ← vous etes ici
│
├── data/                  GITIGNORE — etat runtime
│   ├── aubepilot.db    SQLite (cree au boot si absent)
│   ├── uploads/           justificatifs brevets + photos drones
│   └── mail/              dump des emails en mode dev (.eml)
│
├── scripts/
│   ├── seed.py            Comptes + missions de demo (mdp `demo`)
│   └── send_test_email.py Envoi de test : welcome / new_bid / etc.
│
├── tests/
│   ├── conftest.py        Isole DATA_DIR dans /tmp pour les tests
│   └── test_smoke.py      Status codes, lang switch, API, mailer dump
│
├── deploy/
│   ├── README.md          Procedure prod complete
│   ├── nginx.conf.example reverse proxy + SSL
│   ├── aubepilot.service systemd unit
│   └── pam.example        notes sur l'auth PAM partagee
│
├── templates/
│   ├── base.html          layout + header/footer ecosysteme
│   ├── index.html         landing
│   ├── pilots_search.html / pilot_detail.html / pilot_edit.html / pilot_become.html
│   ├── missions_search.html / mission_detail.html / mission_create.html
│   ├── booking_detail.html
│   ├── dashboard.html / login.html / register.html / error.html
│   └── emails/
│       ├── _layout.html         layout commun (table-based, inline CSS)
│       ├── welcome.{html,txt}
│       ├── new_bid.{html,txt}
│       ├── bid_accepted.{html,txt}
│       └── new_message.{html,txt}
│
└── static/
    ├── css/style.css      ~1500 lignes, themes clair + sombre via CSS vars
    ├── js/app.js          geoloc + theme toggle + zone storage
    └── img/logo.svg
```

---

## Flux principaux

### Inscription
1. `POST /inscription` → `app.register`
2. `auth.create_user` → INSERT users + INSERT pilot_profiles si role pilot
3. `mailer.send_welcome` (async) → email bilingue FR + EN
4. session creee → cookie `aubepilot_sid`
5. redirect `/espace`

### Cycle d'une mission
1. Client publie : `POST /missions/nouvelle` → `services.create_mission`
2. Pilote enchérit : `POST /missions/<id>/enchere` → `services.place_bid`
   - Si nouvelle enchere (pas un update) → `mailer.send_new_bid` au client
3. Client accepte : `POST /missions/<id>/accepter/<bid_id>` → `services.accept_bid`
   - Crée `bookings` row, calcule `platform_fee` (commission dégressive, taux figé dans `platform_fee_pct`)
   - Reject les autres encheres
   - Mission passe en `assigned`
   - `mailer.send_bid_accepted` au pilote
4. Messagerie : `POST /missions/<id>/messages` → `services.send_message`
   - Si pas de message non-lu recent (5 min) → `mailer.send_new_message` au destinataire
5. Booking → `completed` (côté client ou pilote)
6. Avis bidirectionnel via `/reservations/<id>/avis`

---

## i18n

- Module `i18n.py` — table de traductions à plat (`_T = {key: {fr, en, es, ru, hi, uk, tr, ur, bn}}`)
- `g.lang` est résolu au `before_request` (cookie `aube_lang` → `Accept-Language` → `fr`)
- Templates utilisent `{{ t('key') }}` (injecté via `context_processor`)
- Sélecteur dans `base.html` → `GET /lang/<code>` pose le cookie
- `i18n.RTL` liste les écritures droite-à-gauche (ourdou) ; `lang_dir()` alimente `<html dir>`, le CSS suit avec les propriétés logiques (`border-inline-start`, `inset-inline-end`…)
- **Une URL par langue** (référencement) : le français reste à la racine (`/pilotes/5`), les autres langues sont préfixées (`/en/pilotes/5`). `app.LANG_ENDPOINTS` liste les pages publiques concernées ; `_register_lang_routes()` double chaque règle avec `/<lang>`, `_push_lang` (url_defaults) fait produire le préfixe par `url_for`, `_pull_lang` (url_value_preprocessor) le retire des arguments de vue. Priorité : préfixe d'URL > cookie > préférence du compte > `Accept-Language`. Une URL nue visitée avec une langue **choisie** (cookie ou compte) redirige en 302 vers sa version préfixée ; jamais sur la seule foi d'`Accept-Language` (un robot doit trouver la page, pas une redirection). `base.html` émet canonical + `hreflang` pour toutes les langues + `x-default` (fr) + `og:locale`.
- Titres et descriptions : `seo._S` (même forme que `_T`), accès par `seo._s(lang, clé, city=, country=)` qui fournit `{place}` et, pour le français, `{in_place}` avec la bonne préposition (`i18n.fr_in_country` : « au Maroc », « en France », « aux Pays-Bas »). Noms de pays traduits : `geodata/country_names.json` + `i18n.country_name()`.
- Pages d'atterrissage de l'annuaire : `/pilotes/pays/<pays>[/<ville>]` et `/pilotes/specialite/<code>` (`services.landing_*`, `templates/pilots_landing.html`, bloc `_directory_hub.html`). Indexables seulement avec au moins un pilote (sinon `noindex`), listées au sitemap dans ce cas.
- Sitemap : `seo.render_sitemap()` sort chaque chemin dans chaque langue avec ses alternates `xhtml:link` (format hreflang des sitemaps).
- IndexNow : clé dérivée du secret (`seo.INDEXNOW_KEY`, servie sur `/<clé>.txt`), `seo.indexnow_ping()` en arrière-plan à l'inscription d'un pilote, à la sauvegarde du profil et à la publication d'une mission ; actif seulement si `SITE_URL` est en HTTPS.
- Emails : **bilingues empilés** (FR puis EN dans le même message) — pas de logique de préférence par destinataire à gérer

Pour ajouter une nouvelle clé : éditer `i18n.py` `_T = {...}`, avec une entrée par langue de `SUPPORTED`
(une langue manquante retombe sur le français).
Pas de fallback de fichier YAML — tout est en Python pour la simplicité.

---

## Auth

- Production Linux : `pam.authenticate(username, password, "login")` via `python-pam`
- Dev macOS : fallback SHA-256 + sel dans `.dev_passwords` (chmod 600, gitignore)
- **Ne jamais reset un mot de passe PAM** — partage avec tous les services Aube
- Sessions stockées en DB (table `sessions`) + cookie httpOnly signé itsdangerous
- 30 jours d'expiration, hôte renvoie `last_seen_at`

---

## Mailer

- `mailer.send(to, subject, template, context)` — rend `{template}.html` + `.txt`
- Si `SMTP_HOST` vide → dump dans `data/mail/<ts>-<recipient>.eml`
- Sinon → SMTP avec STARTTLS optionnel
- **Ne lève jamais d'exception** : on log, on retourne False, le flux métier continue
- Envoi asynchrone par défaut (thread daemon) pour ne pas bloquer la requête HTTP
- Templates emails bilingues FR + EN dans le même message

Helpers :
- `send_welcome(user)`
- `send_new_bid(client, mission, bid, pilot)`
- `send_bid_accepted(pilot, mission, booking, client)`
- `send_new_message(recipient, sender, mission, body)`

Tester : `python scripts/send_test_email.py welcome demo@aubemail.com`

---

## Base de données

**SQLite WAL** par défaut. Migration vers PostgreSQL :
1. Remplacer `INTEGER PRIMARY KEY AUTOINCREMENT` par `SERIAL PRIMARY KEY` dans `schema.sql`
2. Adapter `db.py:_connect` pour `psycopg2.connect`
3. Ajuster les `ON CONFLICT` (Postgres a la même syntaxe, OK)
4. Lancer une migration via `scripts/migrate_to_postgres.py` (à écrire)

Commission plateforme dégressive : `config.py:PLATFORM_FEE_TIERS` (20/15/10 % selon les missions terminées entre les mêmes parties, `services.platform_fee_pct_for`). Calculée à `accept_bid`, stockée dans `bookings.platform_fee` + `platform_fee_pct` (`services.booking_fee_pct` déduit le taux des anciens bookings).

---

## CSS / Thèmes

`static/css/style.css` utilise des **CSS variables** dans `:root` (clair par défaut)
et `[data-theme="dark"]` pour basculer.

- FOUC prévenu par un script inline dans `<head>` qui lit `localStorage['aube-theme']`
- Toggle dans la topbar via `app.js:toggleTheme()`
- Couleurs métier : amber `#b87519` (signal pro), teal `#1e6f5e` (success), red `#b1382c` (urgent/error)

Composants éditoriaux clés :
- `.flightbook-hero` — fiche pilote
- `.cockpit-banner` — dashboard
- `.notam-strip` — métriques landing
- `.mission-brief` / `.country-tile` / `.fleet-card` / `.patch`

---

## Paiements (Stripe Connect Express)

`payments.py` est une couche fine au-dessus du SDK Stripe officiel. Elle
fonctionne en deux modes :

- **FAKE** (par défaut, sans clé) : tous les appels Stripe sont simulés.
  L'onboarding redirige vers `/stripe/fake-onboarding/<id>`, le paiement
  vers `/stripe/fake-checkout/<booking>`. Permet de tester le flow de
  bout en bout sans configurer Stripe.
- **TEST / LIVE** (avec `STRIPE_SECRET_KEY`) : appels réels au SDK Stripe.

### Modèle métier : escrow

```
1. Client accepte une enchère     → bookings.status = pending_payment
2. Client paie (Stripe Checkout)  → bookings.status = funded
   (webhook checkout.session.completed)
3. Pilote livre, client valide    → Stripe.Transfer(prix − commission) vers pilote
   (POST /reservations/<id>/valider) → bookings.status = completed
4. Auto-release J+7               → idem si client absent
   (cron scripts/release_stale_bookings.py)
```

### Commission

`config.py:PLATFORM_FEE_TIERS = [(0, 20.0), (3, 15.0), (9, 10.0)]` : le taux
dépend du nombre de missions déjà `completed` entre le client et le pilote
(`services.completed_missions_between`). `PLATFORM_FEE_PCT` = taux de base
(affichage). Le taux est figé sur le booking (`platform_fee_pct`) ; le
`Transfer` au pilote utilise `agreed_price - platform_fee`.

### Annulation (anti-contournement)

Le trou « payer → contact révélé → annuler → traiter en direct » est fermé :
`services.compute_cancellation_fee` retient, si le client a payé et hors
fenêtre de grâce (`CANCELLATION_GRACE_HOURS`), des frais de service
(`CANCELLATION_SERVICE_FEE_PCT`, plafond `CANCELLATION_SERVICE_FEE_CAP`), plus
le dédommagement pilote si préavis < `LATE_CANCELLATION_HOURS`. Le
dédommagement est viré immédiatement (`release_to_pilot(kind="cancel-compensation")`,
clé d'idempotence distincte). `cancel_booking_by_pilot` : remboursement
intégral, devis `withdrawn`, mission `open`, courriel `booking_cancelled_by_pilot`.

### Onboarding pilote

Compte Express avec la capacite `transfers` seule (la plateforme encaisse
via Checkout, puis `Transfer` au pilote) ; pas de `business_type` force
(ecole/societe choisissent « entreprise » a l'onboarding). Pilote hors du
pays de la plateforme (`STRIPE_PLATFORM_COUNTRY`, CA) : accord de service
`recipient` (cross-border payouts — a activer dans Dashboard → Connect).
Webhook LIVE : `checkout.session.completed`, `charge.refunded`,
`account.updated`.


- `GET /espace/pilote/stripe` → crée un compte Connect Express et
  redirige vers l'URL d'onboarding Stripe (KYC)
- `GET /stripe/return` → après onboarding, vérifie le statut Stripe et
  met à jour `pilot_profiles.stripe_charges_enabled / payouts_enabled`
- Webhook `account.updated` → idem en push depuis Stripe

### Anti-bypass

Avant `bookings.status = funded` :
- Email pilote non visible côté public
- Messages filtrés via `services.message_passes_filter()` (regex sur
  email, téléphone, WhatsApp/Telegram/IG, etc., défini dans
  `config.MESSAGE_BANNED_PATTERNS`)

### Dispute & refund

- `POST /reservations/<id>/dispute` → bookings.status = disputed,
  notification admin (TODO email)
- `GET /admin/disputes` (admin only) → liste des litiges
- `POST /admin/reservations/<id>/refund` → Stripe Refund total ou partiel

### Webhooks

`POST /stripe/webhook` traite :
- `checkout.session.completed` → marque booking funded
- `account.updated` → MAJ statut pilote
- `charge.refunded` → marque booking refunded

En prod : configurer dans le Stripe dashboard, copier le webhook secret
dans `STRIPE_WEBHOOK_SECRET`.

---

## Découverte : géocodage, filtres confiance, FAQ, contact

- `geocode.lookup(q, country, lang)` : cache `geocode_cache` (créée par
  `db.run_migrations()`), puis Nominatim (`_fetch`, throttle 1 req/s, timeout
  5 s, jamais d'exception). Les échecs sont aussi mis en cache (`found=0`).
  En test, `geocode._fetch` est monkeypatché : **aucun appel réseau**.
- `app._resolve_near()` lit `near` / `lat` / `lng` / `radius_km` / `near_only`
  et alimente `services.search_pilots` / `search_missions` (tri par distance,
  exclusion seulement si `near_only`). `_map_center()` passe le centre à
  `_map.html` (marqueur violet + cercle GeoJSON du rayon).
- `search_pilots` agrège les brevets en une requête (`certs_total`,
  `certs_verified`, `verified_authorities`) pour les filtres `only_verified`,
  `only_insured`, `authority` et les puces des cartes.
- `content.faq(lang, featured_only)` : source unique de la FAQ (page, accueil,
  `seo.home()`/`seo.faq_page()` → JSON-LD `FAQPage`).
- `/contact` : GET `contact_form`, POST `contact_submit` (rate limit 3/min,
  12/h, pot de miel `website`, validation) → `mailer.send_contact_message`
  (Reply-To = expéditeur) + `mailer.send_contact_ack` + `audit_log`.

## Confiance affichée : brevets et assurance

Deux promesses faites au client, deux circuits identiques : le badge ne suit
jamais une déclaration, seulement une pièce contrôlée.

| | Brevets | Assurance RC pro |
|---|---|---|
| Table | `pilot_certifications` (une ligne par brevet) | colonnes `insurance_*` de `pilot_profiles` |
| Dépôt | `/espace/pilote/certification` | `/espace/pilote/assurance` |
| Revue | `/admin/certifications` | `/admin/assurances` |
| Verdict | `review_status` + `is_verified` | `insurance_status` |
| Badge public | `users.is_verified` (recalculé par `refresh_user_verified`) | `services.INSURED_VERIFIED_SQL` |
| Échéance | `expires_at` fait tomber le badge | `insurance_expires_at` idem |

- `insurance` (la case cochée) reste une **déclaration** du pilote : elle
  n'allume aucun badge et ne fait pas passer le filtre `only_insured`, qui
  exige `insurance_status = 'verified'` et une couverture non échue.
- Tout nouveau dépôt remet le verdict à `pending` : un contrôle ne vaut que
  pour la pièce contrôlée (`services.submit_insurance`).
- L'attestation est cloisonnée comme un brevet : admin, le pilote lui-même,
  ou un client ayant une relation financée (`/pilotes/<id>/assurance/document`).
- La fiche publique dit l'état plutôt que de se taire : « Assurance vérifiée »
  ou « Assurance déclarée, non vérifiée ».

## Aider le pilote à être trouvé

- **Livrables** (`pilot_deliverables`, `config.DELIVERABLES`) : ce que le
  CLIENT reçoit (panorama 360°, orthophoto, rapport d'inspection…), par
  opposition aux capacités du matériel qui vivent sur `pilot_drones`
  (« caméra 8K », « LiDAR »). Un client cherche un résultat, pas un capteur.
  Libellés traduits sous `deliverable.<code>`, filtrables via
  `search_pilots(deliverable=…)`. Attention : `list_pilot_deliverables` est le
  pilote, `list_deliverables` les fichiers remis sur une réservation.
- **Présence professionnelle** (`pilot_links`) : site, avis Google, LinkedIn,
  Instagram… Les adresses suivent la même règle que `portfolio_url` : visibles
  pour une organisation, sinon après une mission payée. Avant paiement on
  affiche les **libellés** sans les adresses, pour que le signal de confiance
  passe sans ouvrir la porte au contournement. `services._clean_url` refuse
  tout schéma autre que http(s).

- `services.pilot_visibility(user_id)` : ce qui manque concrètement, pondéré,
  avec `blocking` pour les points qui retirent le pilote d'un canal entier
  (coordonnées → recherche par code postal et pages ville ; spécialité → pages
  d'atterrissage ; disponibilité → annuaire et alertes ; Stripe → il ne peut
  pas être payé). Affiché en haut de `/espace/pilote` via `_visibility.html`,
  chaque ligne disant ce que le point débloque plutôt qu'un score décoratif.
- Partage (`_share.html`, attend `share_url` + `share_text`) : LinkedIn,
  Facebook, X, WhatsApp, Telegram, courriel, copie du lien, et le menu natif
  du téléphone quand `navigator.share` existe. **Ce sont des liens, pas des
  widgets** : aucun SDK tiers n'est chargé, donc aucun mouchard n'entre sur le
  site et rien ne part vers ces plateformes avant un clic. Le corollaire
  assumé : pas de compteur de partages, il faudrait leur SDK.
- L'URL partagée est la canonique de la langue courante (`seo_alternates`),
  encodée par le filtre `urlquote` (le `urlencode` de Jinja laisse les « / »).
  Les métadonnées Open Graph de `base.html` fournissent titre, description et
  image de l'aperçu.

## Règlement en direct (Connect fermé)

Tant que `STRIPE_CONNECT_ENABLED` vaut 0, une réservation acceptée resterait
bloquée en `pending_payment` pour toujours : pas de séquestre possible, donc
pas de mission terminée, pas d'avis, pas de relation révélée. La sortie de
secours est `POST /reservations/<id>/regle-en-direct`, réservée au **client**
et fermée (404) dès que Connect s'ouvre, sinon elle deviendrait une porte
permanente pour éviter la commission.

`services.mark_booking_settled_offline` passe la réservation en `funded` avec
`settled_offline=1`, `platform_fee=0` et **aucun** `stripe_payment_intent_id`.
Les conséquences se déduisent de là :

- `confirm_completion` détecte `settled_offline` et termine la mission **sans
  transfert Stripe** (sinon l'absence de compte Connect la bloquerait, et le
  cron d'auto-libération tournerait dans le vide) ;
- les chemins d'annulation testent `stripe_payment_intent_id` : vide, ils
  n'engagent aucun mouvement d'argent, sans modification ;
- `has_funded_relation` accepte `funded`, donc l'identité du pilote est
  révélée, ce qu'il faut bien pour que le client puisse le payer ;
- `reviewable_booking_for` exige `completed`, l'avis reste donc possible.

Ce que ça coûte est écrit à l'écran, des deux côtés : pas de séquestre, pas de
remboursement par la plateforme, aucune commission perçue.

## Sécurité

`security.py` centralise toutes les protections. Activées automatiquement
sur l'app via `before_request` / `after_request` hooks.

### CSRF
- Token de 32 octets stocké en session (`session["_csrf"]`).
- Validé sur tous les `POST/PUT/DELETE/PATCH` sauf `/stripe/webhook`
  (signature Stripe distincte).
- Templates : `{{ csrf_input() }}` rend automatiquement le hidden input.
  Présent dans tous les `<form method="post">` (21 occurrences).
- Bypass automatique en mode `TESTING=True` (les tests pytest).

### Rate limiting
- Token bucket en mémoire, par IP + endpoint.
- Routes durcies :
  - `/connexion` : 8/min, 40/h
  - `/inscription` : 4/min, 15/h
  - `/missions/<id>/enchere` : 20/min, 100/h
  - `/missions/<id>/messages` : 30/min, 300/h
  - `/reservations/<id>/payer` : 10/min, 50/h
- Réponse 429 dépassé.
- Single-process : si on passe à plusieurs workers gunicorn, migrer
  vers Redis ou similar.

### En-têtes HTTP sortants
- `X-Frame-Options: DENY` (anti-clickjacking)
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: geolocation=(self), camera=(), microphone=()`
- `Content-Security-Policy` : autorise Google Fonts + Stripe (js, frames,
  api, checkout). `frame-ancestors 'none'` (anti-clickjacking renforcé).
- `Strict-Transport-Security: max-age=31536000` (uniquement si SITE_URL
  est en HTTPS).

### Cookies de session
- `httpOnly=True` (anti-XSS)
- `secure=True` automatique si SITE_URL commence par `https://`
- `SameSite=Lax` (anti-CSRF de niveau 2, surtout pour cross-site)
- Lifetime 30 jours

### Protection redirection ouverte
- `security.safe_next(url, fallback)` accepte uniquement les URL
  relatives ou de même host. Utilisé sur `/connexion` (`?next=`).

### Validation de configuration prod
- `security.assert_production_ready(app)` au boot :
  - Refuse `AUBEPILOT_SECRET=change-me-in-prod-...` en prod (raise)
  - Warn si SITE_URL n'est pas HTTPS
  - Bypassé si `FLASK_DEBUG=1` ou macOS

### Audit
- Table `audit_log` (user_id, action, target, payload JSON, timestamp).
- Helper `security.audit(...)`. Utilisé sur les opérations sensibles
  (accept_bid, dispute, refund).

### À surveiller / améliorations possibles
- Pas de 2FA (TOTP) — à ajouter si on prend de la valeur transactionnelle
- Pas de lockout après N échecs de login (rate limit suffit pour démarrer)
- Pas de CAPTCHA sur `/inscription` — à ajouter si spam
- Logs : pas de PII dans les logs (mots de passe non loggés ✓, tokens non loggés ✓)

---

## Variables d'environnement (toutes optionnelles en dev)

| Var | Défaut | Effet |
|---|---|---|
| `AUBEPILOT_PORT` | 5034 | port HTTP |
| `AUBEPILOT_HOST` | 0.0.0.0 | bind |
| `AUBEPILOT_DATA` | `./data` | DB + uploads + mail dump |
| `AUBEPILOT_SECRET` | dev | clé itsdangerous, **changer en prod** |
| `SITE_URL` | `http://localhost:5034` | utilisé dans les liens d'emails |
| `SMTP_HOST` | (vide) | si vide, dump local |
| `SMTP_PORT` | 587 | |
| `SMTP_USER` / `SMTP_PASSWORD` | (vide) | login SMTP |
| `SMTP_FROM` | `no-reply@aubeetoilee.com` | expéditeur |
| `SMTP_FROM_NAME` | `AubePilot` | nom expéditeur |
| `SMTP_TLS` | 1 | STARTTLS |
| `STRIPE_SECRET_KEY` | (vide=fake) | `sk_test_...` ou `sk_live_...` |
| `STRIPE_PUBLISHABLE_KEY` | (vide) | `pk_test_...` |
| `STRIPE_WEBHOOK_SECRET` | (vide) | `whsec_...` (depuis Stripe dashboard) |
| `AUBEPILOT_AUTO_RELEASE_DAYS` | 7 | délai auto-release escrow |
| `NOMINATIM_URL` | `https://nominatim.openstreetmap.org/search` | géocodeur (instance auto-hébergée possible) |
| `AUBEPILOT_GEOCODE_CONTACT` | `rprp@aubemail.com` | contact dans le User-Agent Nominatim (politique d'usage) |
| `AUBEPILOT_CONTACT_EMAIL` | `rprp@aubemail.com` | destinataire du formulaire `/contact` |
| `AUBEPILOT_CONTACT_REPLY_HOURS` | 24 | délai de réponse annoncé |
| `AUBEPILOT_SOCIAL_LINKEDIN` … `_YOUTUBE` | (vide) | liens réseaux du pied de page + `sameAs` |

---

## Tests

```bash
pip install pytest
pytest -q
```

Les tests utilisent `tests/conftest.py:isolated_data_dir` qui place
`AUBEPILOT_DATA` dans `/tmp` avant l'import de l'app — la DB de prod
n'est jamais touchée.

---

## Roadmap technique non livrée

- **Stripe Connect** (escrow + commission auto) — voir `services.accept_bid` pour le hook futur
- **AubePay intégration** alternative à Stripe
- **PostgreSQL migration** (script à écrire)
- **Vérification admin des brevets** (back-office, table déjà en place : `pilot_certifications.is_verified`)
- **Multilingue arabe** (`i18n._T['ar']` + `'ar'` dans `SUPPORTED` ; le RTL CSS est déjà en place)
- **PWA** (manifest + service worker offline)
- **Rate limiting** sur `/api/*` et `/connexion`
- **Webhooks sortants** (Zapier / Make pour les pros)

---

## Conventions de code

- Pas d'ORM : SQL à plat dans `services.py` pour la lisibilité
- Pas de framework JS côté client : Jinja + vanilla JS suffisent
- `config.py` ne contient que des **constantes** et chemins, jamais de logique
- Logs via `logging` standard (pas de print en prod)
- Toute fonction qui peut planter mais ne doit pas casser le flux user (mailer, audit) attrape `Exception` largement et log

---

## Crédits

Conçu pour l'écosystème [L'Aube Étoilée](https://aubeetoilee.com).
Auth partagée `@aubemail.com`. Marque souveraine francophone.
