# AubePilot — application mobile

Application hybride Flutter (Android et iPhone) connectée à https://pilot.aubeetoilee.com :
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
- **App Links / liens universels** : les liens `pilot.aubeetoilee.com`
  ouvrent directement l'app sur la bonne page (vérifiés côté serveur par
  `/.well-known/assetlinks.json` pour Android, voir `config.py` →
  `ANDROID_CERT_SHA256`, et `/.well-known/apple-app-site-association` pour
  iOS, voir `IOS_APP_IDS`)

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

## iOS

Projet `ios/` (bundle `com.aubeetoilee.aubepilot`, équipe Apple
`A78LGGU44D`, signature automatique, iPhone seulement, iOS 15 minimum).
Ce qui diffère d'Android :

- **Agent** : `AubePilotMobile/<version> (iOS)` est AJOUTÉ à l'agent Safari de
  la WebView (Android garde l'agent court) ; le serveur y cherche
  `aubepilotmobile` (`app._in_app`).
- **Session** : `ios/Runner/AppDelegate.swift` (canal `aubepilot/session`,
  pendant de `MainActivity.kt`) garde le cookie `aubepilot_sid` dans le
  trousseau (`AfterFirstUnlockThisDeviceOnly`, hors sauvegardes) et le
  réinjecte au démarrage.
- **Autorisations** : aucune demande au lancement (l'App Review la refuse) ;
  la WebView demande la position quand la page s'en sert, et
  `<input type="file">` passe par le sélecteur de WKWebView (photos, appareil
  photo, fichiers). Textes d'autorisation dans `ios/Runner/Info.plist`.
  permission_handler n'active aucune autorisation sur iOS (macros à 0).
- **PDF et documents** affichés dans l'app (WKWebView sait les lire, et la
  session suit) ; seuls les `/download` partent dans Safari.
- **Geste retour** : glisser depuis le bord gauche.
- **Erreurs -999 et 102** de WKWebView (navigation remplacée, réponse non
  affichable) ignorées : sinon l'écran hors-ligne surgit à tort.
- **Icône** : `assets/icon-ios.png`, carré plein 1024 sans transparence
  (`static/brand/icon-512.svg` sans le rayon des coins). Écran de lancement
  navy au logo (`LaunchScreen.storyboard`).

### Build iOS

Même copie hors iCloud que pour Android. Xcode 27 est installé mais
`xcode-select` pointe sur les CommandLineTools : passer `DEVELOPER_DIR`.

```sh
export PATH=/Library/Developer/CommandLineTools/usr/bin:$HOME/flutter/bin:$PATH
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
flutter build ipa --release   # build/ios/archive/Runner.xcarchive + build/ios/ipa/AubePilot.ipa
```

Xcode crée lui-même le certificat « Apple Distribution » et le profil App
Store (compte Apple connecté dans Xcode > Réglages > Comptes). Envoi : ouvrir
l'archive dans Xcode (Window > Organizer > Distribute App > App Store
Connect) ou glisser l'IPA dans Transporter. Après le `rsync`, recopier
`ios/` (sans `Pods/`, `.symlinks/`, `Flutter/ephemeral/`) vers iCloud.

Pièges :

- **Simulateur** : `flutter build ios --simulator` échoue avec Xcode 27
  (« Flutter.framework does not contain architectures "arm64 x86_64" ») :
  le `lipo -verify_arch` de Xcode 27 refuse plusieurs architectures. Compiler
  en arm64 seul :
  `xcodebuild -workspace ios/Runner.xcworkspace -scheme Runner -configuration Debug -sdk iphonesimulator -destination 'id=<simulateur>' -derivedDataPath build/ios-sim ARCHS=arm64 ONLY_ACTIVE_ARCH=YES build`
  puis `xcrun simctl install` / `launch`. L'archive App Store (arm64 seul)
  n'est pas touchée.
- **Pods** : Xcode 27 refuse une cible iOS < 15 ; le `post_install` du
  Podfile aligne tous les pods sur 15.0.
- **Mac 8 Go** : un seul simulateur démarré à la fois, sinon `simctl launch`
  reste bloqué.

### Fiche App Store

`store/app-store-fr.txt` : textes, étiquettes de confidentialité,
classification par âge, notes pour l'examinateur (compte d'examen commun).
Captures 6,9 pouces (1320 x 2868) dans `store/captures-ios-fr/`, prises dans
le simulateur iPhone 16 Pro Max : en debug seulement, l'app lit
`aubepilot_capture.txt` dans son dossier temporaire
(`$(xcrun simctl get_app_container <sim> com.aubeetoilee.aubepilot data)/tmp/`) :
1re ligne = page de départ (`/pilotes/1`), le reste = script joué à chaque
fin de chargement (`AubeMap.focus(1)` pour ne montrer que la fiche n° 1).
Pas de variables d'environnement : sur iOS, `Platform.environment` est
toujours vide (même avec `SIMCTL_CHILD_…`).

