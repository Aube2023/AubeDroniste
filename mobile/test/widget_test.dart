// Tests des ecrans natifs (splash + hors-ligne) et des boites alert/confirm/
// prompt. La WebView elle-meme necessite la plateforme Android et n'est pas
// testable unitairement.
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aubepilot/main.dart';

/// App avec un bouton « go » qui lance [action].
Widget _app(Future<void> Function(BuildContext) action) => MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () => action(context),
            child: const Text('go'),
          ),
        ),
      ),
    );

void main() {
  testWidgets('le splash affiche la marque', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: SplashScreen())),
    );
    expect(find.text('AubePilot'), findsOneWidget);
    expect(find.byType(Image), findsOneWidget);
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

  testWidgets('confirm() : Annuler rend false, OK rend true', (tester) async {
    bool? result;
    await tester.pumpWidget(_app((context) async {
      result = await showJsDialog(context, 'Valider la livraison ?');
    }));

    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text('Valider la livraison ?'), findsOneWidget);
    await tester.tap(find.text('Annuler'));
    await tester.pumpAndSettle();
    expect(result, isFalse);

    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(result, isTrue);
  });

  testWidgets('confirm() : toucher hors de la boite vaut Annuler',
      (tester) async {
    bool? result;
    await tester.pumpWidget(_app((context) async {
      result = await showJsDialog(context, 'Ouvrir un litige ?');
    }));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    await tester.tapAt(const Offset(5, 5));
    await tester.pumpAndSettle();
    expect(find.text('Ouvrir un litige ?'), findsNothing);
    expect(result, isFalse);
  });

  testWidgets('confirm() : bouton Annuler dans la langue de la page',
      (tester) async {
    bool? result;
    await tester.pumpWidget(_app((context) async {
      result = await showJsDialog(context, 'Open a dispute?',
          cancelLabel: cancelLabelFor('en'));
    }));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(result, isFalse);
  });

  testWidgets('alert() : un seul bouton OK', (tester) async {
    await tester.pumpWidget(_app((context) async {
      await showJsDialog(context, 'Position indisponible',
          cancellable: false);
    }));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text('Position indisponible'), findsOneWidget);
    expect(find.text('Annuler'), findsNothing);
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(find.text('Position indisponible'), findsNothing);
  });

  testWidgets('prompt() : rend le texte pre-rempli ou modifie',
      (tester) async {
    String? result;
    await tester.pumpWidget(_app((context) async {
      result = await showJsPrompt(context, '', 'https://pilot.aubeetoilee.com/p/1');
    }));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text('https://pilot.aubeetoilee.com/p/1'), findsOneWidget);
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(result, 'https://pilot.aubeetoilee.com/p/1');

    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField), 'autre');
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(result, 'autre');
  });
}
