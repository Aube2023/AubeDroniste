# AubePilot — application mobile

Application hybride Flutter connectée à https://pilot.aubeetoilee.com :
navigation native par onglets en bas (Accueil / Missions / Pilotes / Mon
espace), le header et le footer du site sont masqués dans l'app — seul le
contenu utile s'affiche, comme une vraie app.

## Fonctionnalités

- **Barre d'onglets native** en bas — re-toucher l'onglet actif rafraîchit la page
- **Header/footer web masqués** dans l'app (navigation 100 % native)
- Palette alignée sur le site (noir franc + accent indigo), icône et
  écrans de démarrage au logo du 2026-09-16 (navy `#082744`)
- Boîtes `alert()` / `confirm()` / `prompt()` du site affichées en natif
  (sans elles la WebView les avalait : `confirm()` rendait « non » et les
  boutons `data-confirm` ne faisaient rien) ; « Annuler » suit la langue de
  la page
- Splash de premier chargement aux couleurs de la marque
- Écran hors-ligne natif avec bouton « Réessayer »
- Liens externes / `mailto:` / `tel:` ouverts dans l'application adéquate
- PDF (devis) ouverts dans le navigateur
- Upload de fichiers (brevet, logo, avatar) via le sélecteur natif
- Géolocalisation accordée à la WebView (recherche par rayon)
- **App Links** : les liens `pilot.aubeetoilee.com` ouvrent directement
  l'app sur la bonne page (vérifié côté serveur par
  `/.well-known/assetlinks.json`, voir `config.py` → `ANDROID_CERT_SHA256`)

## Build

Toujours depuis une copie hors iCloud (l'espace de « Mobile Documents » casse
Gradle) :

```sh
rsync -a --delete --exclude build/ --exclude .dart_tool/ --exclude '*.apk' \
  mobile/ ~/.aube-build/aubepilot-mobile/
cd ~/.aube-build/aubepilot-mobile
export PATH=/Library/Developer/CommandLineTools/usr/bin:$HOME/flutter/bin:$PATH
flutter pub get && flutter analyze && flutter test
flutter build appbundle --release   # Google Play : build/app/outputs/bundle/release/app-release.aab
flutter build apk --release         # /static/dl/ : build/app/outputs/flutter-apk/app-release.apk
```

## Signature

Depuis la 1.5.0, APK et AAB sont signés par la **clé d'importation Play**
`cles/cle-android.jks` (alias `aubepilot`) ; `android/key.properties` porte
les mots de passe. Les deux sont ignorés par git ; mots de passe et
empreintes dans `cles/LISEZMOI.txt`. Sans `key.properties`, Gradle retombe
sur la clé de debug (binaire refusé par Play).

Les liens `pilot.aubeetoilee.com` n'ouvrent l'app que si le certificat qui
l'a signée figure dans `/.well-known/assetlinks.json` (`config.py` →
`ANDROID_CERT_SHA256`) : clé d'importation et ancienne clé de debug y sont.
Une fois l'app sur Play, ajouter l'empreinte SHA-256 de la « clé de
signature de l'application » (Play Console > Intégrité de l'application).

Un APK signé par la clé de debug (1.4.1 et avant) ne se met pas à jour par
une 1.5.0 : désinstaller d'abord.

## Fiche Play

`store/icon-play-512.png` : icône de la fiche (carré plein, Play arrondit
lui-même les coins).
