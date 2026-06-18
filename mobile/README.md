# Aube Pilot — application mobile

Application hybride Flutter connectée à https://pilot.aubeetoilee.com :
navigation native par onglets en bas (Accueil / Missions / Pilotes / Mon
espace), le header et le footer du site sont masqués dans l'app — seul le
contenu utile s'affiche, comme une vraie app.

## Fonctionnalités

- **Barre d'onglets native** en bas — re-toucher l'onglet actif rafraîchit la page
- **Header/footer web masqués** dans l'app (navigation 100 % native)
- Palette alignée sur le site (noir franc + accent indigo)
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

```sh
flutter pub get
flutter analyze && flutter test
flutter build apk --release   # → build/app/outputs/flutter-apk/app-release.apk
```

La release est signée avec le keystore **debug** local (voir
`android/app/build.gradle.kts`). Pour publier sur le Play Store : créer un
vrai keystore, puis mettre à jour `ANDROID_CERT_SHA256` côté serveur avec la
nouvelle empreinte SHA-256 (plusieurs empreintes possibles, séparées par des
virgules).
