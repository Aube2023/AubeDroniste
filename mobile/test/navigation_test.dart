// Tests unitaires des fonctions PURES de routage (sans WebView ni device) :
//  - tabIndexForPath : quel onglet natif surligner pour un chemin donne
//  - shouldOpenExternally : quelle URL sort de la WebView (app/navigateur)
import 'package:flutter_test/flutter_test.dart';

import 'package:aubepilot/main.dart';

void main() {
  group('tabIndexForPath', () {
    test('accueil', () => expect(tabIndexForPath('/'), 0));
    test('missions', () => expect(tabIndexForPath('/missions'), 1));
    test('detail mission -> onglet missions',
        () => expect(tabIndexForPath('/missions/12'), 1));
    test('pilotes', () => expect(tabIndexForPath('/pilotes'), 2));
    test('detail pilote -> onglet pilotes',
        () => expect(tabIndexForPath('/pilotes/7'), 2));
    test('espace', () => expect(tabIndexForPath('/espace'), 3));
    test('espace pilote -> espace',
        () => expect(tabIndexForPath('/espace/pilote'), 3));
    test('reservations -> espace',
        () => expect(tabIndexForPath('/reservations/5'), 3));
    test('connexion -> espace', () => expect(tabIndexForPath('/connexion'), 3));
    test('inscription -> espace',
        () => expect(tabIndexForPath('/inscription'), 3));
    test('page transverse -> null (garde l onglet courant)',
        () => expect(tabIndexForPath('/cgu'), isNull));
  });

  group('shouldOpenExternally', () {
    Uri u(String s) => Uri.parse(s);
    const site = 'https://pilot.aubeetoilee.com';

    test('mailto -> externe',
        () => expect(shouldOpenExternally(u('mailto:a@b.com')), isTrue));
    test('tel -> externe',
        () => expect(shouldOpenExternally(u('tel:+33600000000')), isTrue));
    test('pdf (devis) -> externe',
        () => expect(shouldOpenExternally(u('$site/reservations/1/devis.pdf')),
            isTrue));
    test('media -> externe',
        () => expect(shouldOpenExternally(u('$site/media/x.jpg')), isTrue));
    test('document (brevet) -> externe',
        () => expect(
            shouldOpenExternally(u('$site/pilotes/1/brevets/2/document')),
            isTrue));
    test('download -> externe',
        () => expect(shouldOpenExternally(u('$site/x/download')), isTrue));
    test('Stripe checkout -> INTERNE (reste dans la WebView)',
        () => expect(
            shouldOpenExternally(u('https://checkout.stripe.com/pay/x')),
            isFalse));
    test('page du site -> INTERNE',
        () => expect(shouldOpenExternally(u('$site/missions')), isFalse));
    test('domaine tiers -> externe',
        () => expect(
            shouldOpenExternally(u('https://www.facebook.com/x')), isTrue));
    test('domaine usurpateur evilstripe.com -> externe (anti-phishing)',
        () => expect(
            shouldOpenExternally(u('https://evilstripe.com/pay/x')), isTrue));
    test('domaine usurpateur evilstripe.network -> externe',
        () => expect(shouldOpenExternally(u('https://evilstripe.network/x')),
            isTrue));
    test('vrai sous-domaine Stripe (js.stripe.com) -> INTERNE',
        () => expect(
            shouldOpenExternally(u('https://js.stripe.com/v3/')), isFalse));
    test('hote usurpateur du site (....evil.com) -> externe',
        () => expect(
            shouldOpenExternally(
                u('https://pilot.aubeetoilee.com.evil.com/connexion')),
            isTrue));
  });
}
