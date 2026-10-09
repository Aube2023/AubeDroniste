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

  group('shouldOpenExternally sur iOS (documents dans la WebView)', () {
    Uri u(String s) => Uri.parse(s);
    const site = 'https://pilot.aubeetoilee.com';
    bool ios(String s) => shouldOpenExternally(u(s), inlineDocuments: true);

    test('pdf (devis) -> INTERNE, garde la session',
        () => expect(ios('$site/reservations/1/devis.pdf'), isFalse));
    test('document (brevet) -> INTERNE',
        () => expect(ios('$site/pilotes/1/brevets/2/document'), isFalse));
    test('download (livrable) -> toujours externe',
        () => expect(ios('$site/reservations/1/livrables/2/download'), isTrue));
    test('mailto -> toujours externe',
        () => expect(ios('mailto:a@b.com'), isTrue));
    test('domaine tiers -> toujours externe',
        () => expect(ios('https://www.facebook.com/x'), isTrue));
  });

  group('isIgnorableLoadError', () {
    test('annulation WKWebView (-999) ignoree',
        () => expect(isIgnorableLoadError(-999), isTrue));
    test('chargement interrompu WKWebView (102) ignore',
        () => expect(isIgnorableLoadError(102), isTrue));
    test('hote introuvable iOS (-1003) -> hors-ligne',
        () => expect(isIgnorableLoadError(-1003), isFalse));
    test('pas de reseau iOS (-1009) -> hors-ligne',
        () => expect(isIgnorableLoadError(-1009), isFalse));
    test('Android ERROR_HOST_LOOKUP (-2) -> hors-ligne',
        () => expect(isIgnorableLoadError(-2), isFalse));
  });

  group('appUserAgent', () {
    const safari = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) '
        'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148';
    test('Android : agent court inchange',
        () => expect(appUserAgent(ios: false),
            'AubePilotMobile/$kAppVersion (Android)'));
    test('iOS : marque ajoutee a l agent de la WebView',
        () => expect(appUserAgent(ios: true, webViewAgent: safari),
            '$safari AubePilotMobile/$kAppVersion (iOS)'));
    test('iOS sans agent de la WebView : marque seule',
        () => expect(appUserAgent(ios: true),
            'AubePilotMobile/$kAppVersion (iOS)'));
    test('le serveur reconnait l app (app.APP_UA_MARK)',
        () => expect(
            appUserAgent(ios: true, webViewAgent: safari)
                .toLowerCase()
                .contains('aubepilotmobile'),
            isTrue));
  });

  group('cancelLabelFor', () {
    test('francais', () => expect(cancelLabelFor('fr'), 'Annuler'));
    test('anglais', () => expect(cancelLabelFor('en'), 'Cancel'));
    test('variante regionale pt-BR -> pt',
        () => expect(cancelLabelFor('pt-BR'), 'Cancelar'));
    test('zh_Hans -> zh', () => expect(cancelLabelFor('zh_Hans'), '取消'));
    test('langue inconnue -> francais',
        () => expect(cancelLabelFor('xx'), 'Annuler'));
    test('absente -> francais', () => expect(cancelLabelFor(null), 'Annuler'));
    test('les 31 langues du site sont couvertes',
        () => expect(kCancelLabels.length, 31));
  });
}
