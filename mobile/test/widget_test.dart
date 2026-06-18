// Tests des ecrans natifs (splash + hors-ligne). La WebView elle-meme
// necessite la plateforme Android et n'est pas testable unitairement.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aubepilot/main.dart';

void main() {
  testWidgets('le splash affiche la marque', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: SplashScreen())),
    );
    expect(find.text('AUBE PILOT'), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
  });

  testWidgets("l'ecran hors-ligne propose de reessayer", (tester) async {
    var retried = false;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: OfflineScreen(onRetry: () => retried = true)),
    ));
    expect(find.text('Connexion impossible'), findsOneWidget);
    await tester.tap(find.text('Réessayer'));
    expect(retried, isTrue);
  });
}
