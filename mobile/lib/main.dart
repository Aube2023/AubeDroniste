import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';

const String kSiteUrl = 'https://pilot.aubeetoilee.com';
const Color kAubeGreen = Color(0xFF2D5A27);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await SystemChrome.setPreferredOrientations([
    DeviceOrientation.portraitUp,
  ]);
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: kAubeGreen,
    statusBarIconBrightness: Brightness.light,
    systemNavigationBarColor: Colors.white,
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
        colorScheme: ColorScheme.fromSeed(seedColor: kAubeGreen),
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
  int _progress = 0;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    final params = WebViewPlatform.instance is AndroidWebViewPlatform
        ? AndroidWebViewControllerCreationParams()
        : const PlatformWebViewControllerCreationParams();
    _controller = WebViewController.fromPlatformCreationParams(params)
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(Colors.white)
      ..setUserAgent('AubePilotMobile/1.0 (Android)')
      ..setNavigationDelegate(NavigationDelegate(
        onPageStarted: (_) => setState(() => _loading = true),
        onProgress: (p) => setState(() => _progress = p),
        onPageFinished: (_) => setState(() => _loading = false),
      ))
      ..loadRequest(Uri.parse(kSiteUrl));

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
    }
  }

  Future<bool> _onBack() async {
    if (await _controller.canGoBack()) {
      await _controller.goBack();
      return false;
    }
    return true;
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
        backgroundColor: kAubeGreen,
        body: Stack(
          children: [
            Padding(
              padding: EdgeInsets.only(top: topPadding),
              child: WebViewWidget(controller: _controller),
            ),
            if (_loading)
              Positioned(
                top: topPadding,
                left: 0,
                right: 0,
                child: LinearProgressIndicator(
                  value: _progress > 0 ? _progress / 100 : null,
                  color: kAubeGreen,
                  backgroundColor: kAubeGreen.withValues(alpha: 0.15),
                  minHeight: 2,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
