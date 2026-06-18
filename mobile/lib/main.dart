// AubePilot mobile — vraie app hybride connectee a pilot.aubeetoilee.com.
//
// Ce qui en fait une vraie app (et pas un simple site dans un cadre) :
//  - barre de navigation NATIVE en bas (Accueil / Missions / Pilotes / Espace)
//  - le header et le footer du site sont masques dans l'app : seul le
//    contenu utile s'affiche, la navigation se fait par les onglets natifs
//  - re-toucher l'onglet actif recharge la page (equivalent du « rafraichir »)
//  - ecran hors-ligne natif avec bouton Reessayer
//  - liens externes / mailto / tel ouverts dans l'app adequate
//  - PDF (devis) ouverts dans le navigateur (la WebView ne telecharge pas)
//  - upload de fichiers (brevet, logo, avatar) via le selecteur natif
//  - deep links : les liens pilot.aubeetoilee.com ouvrent l'app sur la bonne page
//  - splash de premier chargement aux couleurs de la marque
import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

const String kSiteUrl = 'https://pilot.aubeetoilee.com';
const String kSiteHost = 'pilot.aubeetoilee.com';

// Palette du site (static/css/style.css) — theme clair.
const Color kInk = Color(0xFF14161F); // noir franc
const Color kIndigo = Color(0xFF4257B2); // accent indigo
const Color kPaper = Color(0xFFFFFFFF);

/// Onglets natifs en bas de l'app. Chaque onglet pointe vers une page du
/// site ; le header/footer web etant masques, c'est LA navigation de l'app.
class AppTab {
  const AppTab(this.label, this.icon, this.activeIcon, this.path);
  final String label;
  final IconData icon;
  final IconData activeIcon;
  final String path;
}

const List<AppTab> kTabs = [
  AppTab('Accueil', Icons.home_outlined, Icons.home_rounded, '/'),
  AppTab('Missions', Icons.flag_outlined, Icons.flag_rounded, '/missions'),
  AppTab('Pilotes', Icons.groups_outlined, Icons.groups_rounded, '/pilotes'),
  AppTab('Mon espace', Icons.person_outline_rounded, Icons.person_rounded,
      '/espace'),
];

/// CSS injecte dans chaque page : masque le footer et les liens de
/// navigation du header (la navigation passe par les onglets natifs), mais
/// GARDE le logo + le selecteur de langue + le bouton de theme.
const String kAppModeJs = '''
(function () {
  if (document.getElementById('aubepilot-app-css')) return;
  var s = document.createElement('style');
  s.id = 'aubepilot-app-css';
  s.textContent =
      'footer.footer{display:none !important}'
    + '.topnav > a,.topnav form,.zone-pill{display:none !important}'
    + 'header.topbar{padding-top:6px;padding-bottom:6px}'
    + 'body{padding-bottom:12px}';
  document.documentElement.appendChild(s);
})();
''';

/// Index de l'onglet natif correspondant a un chemin d'URL, ou null si aucun
/// onglet ne doit changer (page transverse, ex /cgu). Fonction PURE (testable).
int? tabIndexForPath(String path) {
  for (var i = kTabs.length - 1; i >= 0; i--) {
    final p = kTabs[i].path;
    if (p == '/' ? path == '/' : path == p || path.startsWith('$p/')) {
      return i;
    }
  }
  // Espaces authentifies / parcours -> onglet "Mon espace".
  if (path.startsWith('/espace') ||
      path.startsWith('/connexion') ||
      path.startsWith('/inscription') ||
      path.startsWith('/reservations') ||
      path.startsWith('/profil')) {
    return 3;
  }
  return null;
}

/// Decide si une URL doit s'ouvrir HORS WebView (app externe / navigateur) :
/// schemes non-web (mailto/tel), domaines tiers (hors site et hors Stripe), et
/// telechargements que la WebView Android ne gere pas. Fonction PURE (testable).
bool shouldOpenExternally(Uri uri) {
  if (uri.scheme != 'http' && uri.scheme != 'https') return true;
  final h = uri.host;
  // Egalite stricte ou vrai sous-domaine : un simple endsWith('stripe.com')
  // laisserait entrer evilstripe.com dans la WebView.
  bool isDomain(String host, String domain) =>
      host == domain || host.endsWith('.$domain');
  final isPayment = isDomain(h, 'stripe.com') || isDomain(h, 'stripe.network');
  if (h != kSiteHost && !isPayment) return true;
  final path = uri.path.toLowerCase();
  if (path.endsWith('.pdf') ||
      path.endsWith('/document') ||
      path.endsWith('/download') ||
      path.startsWith('/media/')) {
    return true;
  }
  return false;
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
  ]);
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.dark,
    systemNavigationBarColor: kPaper,
    systemNavigationBarIconBrightness: Brightness.dark,
  ));
  // Demande les permissions au demarrage (silencieux si deja accorde)
  unawaited(_requestPermissions());
  runApp(const AubePilotApp());
}

Future<void> _requestPermissions() async {
  try {
    await [
      Permission.locationWhenInUse,
      Permission.notification,
      Permission.camera,
    ].request();
  } catch (_) {
    // pas critique au demarrage
  }
}

class AubePilotApp extends StatelessWidget {
  const AubePilotApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Aube Pilot',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: kIndigo),
        scaffoldBackgroundColor: kPaper,
        useMaterial3: true,
      ),
      home: const WebHome(),
    );
  }
}

class WebHome extends StatefulWidget {
  const WebHome({super.key});

  @override
  State<WebHome> createState() => _WebHomeState();
}

class _WebHomeState extends State<WebHome> {
  late final WebViewController _controller;
  final AppLinks _appLinks = AppLinks();
  StreamSubscription<Uri>? _linkSub;
  int _progress = 0;
  bool _loading = true;
  bool _firstLoad = true; // splash plein ecran tant que rien n'est affiche
  bool _offline = false;
  int _tabIndex = 0;
  bool _dark = false; // suit le theme du site (bouton sombre/clair)

  @override
  void initState() {
    super.initState();
    final params = WebViewPlatform.instance is AndroidWebViewPlatform
        ? AndroidWebViewControllerCreationParams()
        : const PlatformWebViewControllerCreationParams();
    _controller = WebViewController.fromPlatformCreationParams(params)
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(kPaper)
      ..setUserAgent('AubePilotMobile/1.1 (Android)')
      ..setNavigationDelegate(NavigationDelegate(
        onNavigationRequest: _onNavigationRequest,
        onPageStarted: (url) {
          // Masque le chrome web le plus tot possible (evite le flash du
          // header pendant le chargement).
          unawaited(_controller.runJavaScript(kAppModeJs).catchError((_) {}));
          setState(() {
            _loading = true;
            _offline = false;
            _syncTab(url);
          });
        },
        onProgress: (p) => setState(() => _progress = p),
        onPageFinished: (url) {
          unawaited(_controller.runJavaScript(kAppModeJs).catchError((_) {}));
          unawaited(_syncTheme());
          setState(() {
            _loading = false;
            _firstLoad = false;
            _syncTab(url);
          });
        },
        onWebResourceError: (error) {
          // Seule une erreur de la page principale (pas une image ou un
          // script tiers) doit declencher l'ecran hors-ligne.
          if (error.isForMainFrame ?? true) {
            setState(() {
              _offline = true;
              _loading = false;
            });
          }
        },
      ));

    final platform = _controller.platform;
    if (platform is AndroidWebViewController) {
      platform.setMediaPlaybackRequiresUserGesture(false);
      platform.setGeolocationPermissionsPromptCallbacks(
        onShowPrompt: (request) async =>
            const GeolocationPermissionsResponse(allow: true, retain: true),
        onHidePrompt: () {},
      );
      platform.setOnPlatformPermissionRequest((request) {
        request.grant();
      });
      // <input type="file"> (brevet, logo, avatar) -> selecteur natif
      platform.setOnShowFileSelector(_onShowFileSelector);
    }

    _bootstrap();
  }

  /// Charge la page initiale : le deep link si l'app a ete ouverte via un
  /// lien pilot.aubeetoilee.com, sinon l'accueil. Puis ecoute les liens
  /// recus pendant que l'app tourne.
  Future<void> _bootstrap() async {
    Uri start = Uri.parse(kSiteUrl);
    try {
      final initial = await _appLinks.getInitialLink();
      // Scheme verifie aussi : un intent forge en http:// ou autre ne doit
      // pas etre charge dans la WebView.
      if (initial != null &&
          initial.scheme == 'https' &&
          initial.host == kSiteHost) {
        start = initial;
      }
    } catch (_) {}
    _syncTab(start.toString());
    unawaited(_controller.loadRequest(start));
    // Lien recu pendant que l'app tourne (deep link a chaud) : on remet
    // l'etat a zero (sort de l'ecran hors-ligne, relance le loader, aligne
    // l'onglet) AVANT de charger, sinon l'overlay offline reste par-dessus
    // et un meme lien ne rafraichit pas l'ecran.
    _linkSub = _appLinks.uriLinkStream.listen((uri) {
      if (uri.scheme != 'https' || uri.host != kSiteHost) return;
      if (mounted) {
        setState(() {
          _offline = false;
          _loading = true;
          _syncTab(uri.toString());
        });
      }
      _controller.loadRequest(uri);
    });
  }

  FutureOr<NavigationDecision> _onNavigationRequest(
      NavigationRequest request) async {
    final uri = Uri.tryParse(request.url);
    if (uri == null) return NavigationDecision.navigate;
    // Schemes non-web, domaines tiers et telechargements -> app externe.
    // (Stripe Checkout reste DANS la WebView : il y fonctionne bien et garde
    // le parcours de paiement fluide.)
    if (shouldOpenExternally(uri)) {
      unawaited(launchUrl(uri, mode: LaunchMode.externalApplication)
          .catchError((_) => false));
      return NavigationDecision.prevent;
    }
    return NavigationDecision.navigate;
  }

  Future<List<String>> _onShowFileSelector(FileSelectorParams params) async {
    try {
      // Respecte l'attribut accept de l'<input> : si la page demande une
      // image (avatar, logo, photo de brevet), on ouvre directement la
      // galerie d'images plutot que tous les fichiers.
      final wantsImage = params.acceptTypes.any((t) => t.contains('image'));
      final result = await FilePicker.platform.pickFiles(
        allowMultiple: params.mode == FileSelectorMode.openMultiple,
        type: wantsImage ? FileType.image : FileType.any,
      );
      if (result == null) return <String>[];
      return result.paths
          .whereType<String>()
          .map((p) => Uri.file(p).toString())
          .toList();
    } catch (_) {
      return <String>[];
    }
  }

  /// Aligne la barre d'onglets native sur le theme du site. Sonde aussi
  /// quelques secondes apres le chargement pour suivre le bouton de theme.
  Future<void> _syncTheme() async {
    for (var i = 0; i < 8; i++) {
      try {
        final result = await _controller.runJavaScriptReturningResult(
            "document.documentElement.getAttribute('data-theme')||'light'");
        final dark = result.toString().contains('dark');
        if (mounted && dark != _dark) {
          setState(() => _dark = dark);
          SystemChrome.setSystemUIOverlayStyle(SystemUiOverlayStyle(
            statusBarColor: Colors.transparent,
            statusBarIconBrightness: dark ? Brightness.light : Brightness.dark,
            systemNavigationBarColor: dark ? kInk : kPaper,
            systemNavigationBarIconBrightness:
                dark ? Brightness.light : Brightness.dark,
          ));
        }
      } catch (_) {}
      await Future.delayed(const Duration(seconds: 2));
      if (!mounted) return;
    }
  }

  /// Met en surbrillance l'onglet correspondant a la page affichee
  /// (y compris quand on y arrive par un lien interne ou un deep link).
  void _syncTab(String url) {
    final path = Uri.tryParse(url)?.path ?? '/';
    for (var i = kTabs.length - 1; i >= 0; i--) {
      final p = kTabs[i].path;
      if (p == '/' ? path == '/' : path == p || path.startsWith('$p/')) {
        _tabIndex = i;
        return;
      }
    }
    // /espace/pilote, /reservations, /connexion... -> on garde l'onglet actuel
    if (path.startsWith('/espace') ||
        path.startsWith('/connexion') ||
        path.startsWith('/inscription') ||
        path.startsWith('/reservations') ||
        path.startsWith('/profil')) {
      _tabIndex = 3;
    }
  }

  void _onTabTap(int index) {
    if (index == _tabIndex && !_offline) {
      _controller.reload(); // re-toucher l'onglet actif = rafraichir
      return;
    }
    setState(() {
      _tabIndex = index;
      _offline = false;
      _loading = true;
    });
    _controller.loadRequest(Uri.parse('$kSiteUrl${kTabs[index].path}'));
  }

  Future<bool> _onBack() async {
    if (await _controller.canGoBack()) {
      await _controller.goBack();
      return false;
    }
    return true;
  }

  void _retry() {
    setState(() {
      _offline = false;
      _loading = true;
    });
    _controller.reload();
  }

  @override
  void dispose() {
    _linkSub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final topPadding = MediaQuery.of(context).padding.top;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) async {
        if (didPop) return;
        final allowPop = await _onBack();
        if (allowPop && context.mounted) {
          Navigator.of(context).maybePop();
        }
      },
      child: Scaffold(
        backgroundColor: _dark ? kInk : kPaper,
        body: Stack(
          children: [
            Padding(
              padding: EdgeInsets.only(top: topPadding),
              child: WebViewWidget(controller: _controller),
            ),
            if (_loading && !_firstLoad && !_offline)
              Positioned(
                top: topPadding,
                left: 0,
                right: 0,
                child: LinearProgressIndicator(
                  value: _progress > 0 ? _progress / 100 : null,
                  color: kIndigo,
                  backgroundColor: kIndigo.withValues(alpha: 0.12),
                  minHeight: 2,
                ),
              ),
            if (_firstLoad && !_offline) const SplashScreen(),
            if (_offline) OfflineScreen(onRetry: _retry),
          ],
        ),
        bottomNavigationBar: _firstLoad
            ? null // pas de barre pendant le splash
            : Theme(
                // suit le theme du site (bouton sombre/clair du header)
                data: ThemeData(
                  colorScheme: ColorScheme.fromSeed(
                    seedColor: kIndigo,
                    brightness: _dark ? Brightness.dark : Brightness.light,
                  ),
                  useMaterial3: true,
                ),
                child: NavigationBar(
                  selectedIndex: _tabIndex,
                  onDestinationSelected: _onTabTap,
                  backgroundColor: _dark ? kInk : kPaper,
                  indicatorColor: kIndigo.withValues(alpha: 0.18),
                  height: 64,
                  labelBehavior:
                      NavigationDestinationLabelBehavior.alwaysShow,
                  destinations: [
                    for (final tab in kTabs)
                      NavigationDestination(
                        icon: Icon(tab.icon),
                        selectedIcon: Icon(tab.activeIcon,
                            color: _dark ? kPaper : kIndigo),
                        label: tab.label,
                      ),
                  ],
                ),
              ),
      ),
    );
  }
}

/// Splash plein ecran affiche pendant le tout premier chargement.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      color: kInk,
      alignment: Alignment.center,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'AUBE PILOT',
            style: TextStyle(
              color: kPaper,
              fontSize: 26,
              fontWeight: FontWeight.w800,
              letterSpacing: 4,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            "L'AUBE ÉTOILÉE",
            style: TextStyle(
              color: kPaper.withValues(alpha: 0.55),
              fontSize: 11,
              letterSpacing: 3,
            ),
          ),
          const SizedBox(height: 28),
          const SizedBox(
            width: 22,
            height: 22,
            child: CircularProgressIndicator(
              color: kIndigo,
              strokeWidth: 2.5,
            ),
          ),
        ],
      ),
    );
  }
}

/// Ecran natif affiche quand le site est injoignable (avion, zone blanche…).
class OfflineScreen extends StatelessWidget {
  const OfflineScreen({super.key, required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: kPaper,
      alignment: Alignment.center,
      padding: const EdgeInsets.all(32),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.cloud_off_rounded,
              size: 56, color: kInk.withValues(alpha: 0.35)),
          const SizedBox(height: 20),
          const Text(
            'Connexion impossible',
            style: TextStyle(
              color: kInk,
              fontSize: 20,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Vérifiez votre connexion internet,\npuis réessayez.',
            textAlign: TextAlign.center,
            style: TextStyle(color: kInk.withValues(alpha: 0.6), height: 1.4),
          ),
          const SizedBox(height: 24),
          FilledButton(
            onPressed: onRetry,
            style: FilledButton.styleFrom(
              backgroundColor: kInk,
              foregroundColor: kPaper,
              padding:
                  const EdgeInsets.symmetric(horizontal: 28, vertical: 14),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(999),
              ),
            ),
            child: const Text('Réessayer'),
          ),
        ],
      ),
    );
  }
}
