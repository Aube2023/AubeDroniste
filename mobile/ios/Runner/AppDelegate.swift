import Flutter
import Security
import UIKit
import WebKit

@main
@objc class AppDelegate: FlutterAppDelegate {
  /// Moteur Flutter cree au lancement : les plugins s'y enregistrent ici,
  /// avant le super.application, comme avant le passage aux scenes. La scene
  /// (SceneDelegate) l'affiche ensuite dans sa fenetre.
  let moteur = FlutterEngine(name: "aubepilot")

  override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  ) -> Bool {
    moteur.run()
    GeneratedPluginRegistrant.register(with: moteur)
    if let registrar = moteur.registrar(forPlugin: "AubePilotSession") {
      SessionStore.register(messenger: registrar.messenger())
    }
    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }
}

/// Cycle de vie par scene (UIScene), obligatoire avec le SDK d'iOS 27 : une
/// app compilee avec Xcode 27 sans UIApplicationSceneManifest est tuee au
/// lancement sur iOS 27 (TN3187). C'est ce qui a fait refuser 1.6.0 (8)
/// (regle 2.1, iPad Air sous iPadOS 27).
///
/// La scene cree la fenetre et y montre le moteur de l'AppDelegate. Les liens
/// entrants (app_links) et les passages premier plan / arriere-plan arrivent
/// desormais ici : on les relaie a l'AppDelegate, qui les distribue aux plugins
/// comme avant.
class SceneDelegate: UIResponder, UIWindowSceneDelegate {
  var window: UIWindow?

  private var app: AppDelegate? { UIApplication.shared.delegate as? AppDelegate }

  func scene(
    _ scene: UIScene, willConnectTo session: UISceneSession,
    options connectionOptions: UIScene.ConnectionOptions
  ) {
    guard let windowScene = scene as? UIWindowScene, let app = app else { return }
    // Scene recreee (iPad) : on reprend l'ecran deja lie au moteur.
    let ecran: FlutterViewController
    if let existant = app.moteur.viewController {
      ecran = existant
    } else {
      ecran = FlutterViewController(engine: app.moteur, nibName: nil, bundle: nil)
      ecran.loadDefaultSplashScreenView()
    }
    let fenetre = UIWindow(windowScene: windowScene)
    fenetre.rootViewController = ecran
    fenetre.makeKeyAndVisible()
    window = fenetre
    app.window = fenetre
    // Ouverture a froid par un lien : il arrive ici, pas dans openURLContexts.
    if !connectionOptions.urlContexts.isEmpty {
      self.scene(scene, openURLContexts: connectionOptions.urlContexts)
    }
    for activite in connectionOptions.userActivities {
      self.scene(scene, continue: activite)
    }
  }

  func sceneDidDisconnect(_ scene: UIScene) {
    window = nil
    app?.window = nil
  }

  func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
    for contexte in URLContexts {
      var options: [UIApplication.OpenURLOptionsKey: Any] = [:]
      options[.sourceApplication] = contexte.options.sourceApplication
      options[.annotation] = contexte.options.annotation
      options[.openInPlace] = contexte.options.openInPlace
      _ = app?.application(UIApplication.shared, open: contexte.url, options: options)
    }
  }

  func scene(_ scene: UIScene, continue userActivity: NSUserActivity) {
    _ = app?.application(UIApplication.shared, continue: userActivity) { _ in }
  }

  func sceneDidBecomeActive(_ scene: UIScene) {
    app?.applicationDidBecomeActive(UIApplication.shared)
  }

  func sceneWillResignActive(_ scene: UIScene) {
    app?.applicationWillResignActive(UIApplication.shared)
  }

  func sceneWillEnterForeground(_ scene: UIScene) {
    app?.applicationWillEnterForeground(UIApplication.shared)
  }

  func sceneDidEnterBackground(_ scene: UIScene) {
    app?.applicationDidEnterBackground(UIApplication.shared)
  }
}

/// Persistance de la connexion entre deux lancements (pendant iOS de
/// MainActivity.kt).
///
/// Sans la case « connexion automatique », le site pose un cookie de session
/// navigateur, que WKWebView efface a la fermeture de l'app : l'utilisateur
/// devrait se reconnecter a chaque lancement. Ce canal :
///  - copie le cookie de session dans le trousseau apres chaque page (ou l'en
///    retire apres une deconnexion, pour ne pas ressusciter une session
///    revoquee) ;
///  - le reinjecte au demarrage si la WebView l'a perdu.
/// Necessairement natif : le cookie est HttpOnly, invisible du JavaScript.
/// Le trousseau (AfterFirstUnlockThisDeviceOnly) ne suit ni les sauvegardes
/// ni un autre appareil, comme allowBackup=false sur Android.
enum SessionStore {
  static let channelName = "aubepilot/session"
  static let siteURL = URL(string: "https://pilot.aubeetoilee.com/")!
  static let cookieName = "aubepilot_sid"
  static let keychainService = "com.aubeetoilee.aubepilot.session"
  static let maxAge: TimeInterval = 60 * 60 * 24 * 30

  static func register(messenger: FlutterBinaryMessenger) {
    let channel = FlutterMethodChannel(name: channelName, binaryMessenger: messenger)
    channel.setMethodCallHandler { call, result in
      switch call.method {
      case "persistSession": persist(result)
      case "restoreSession": restore(result)
      default: result(FlutterMethodNotImplemented)
      }
    }
  }

  private static var cookieStore: WKHTTPCookieStore {
    WKWebsiteDataStore.default().httpCookieStore
  }

  private static func currentSid(_ done: @escaping (String?) -> Void) {
    cookieStore.getAllCookies { cookies in
      let host = siteURL.host ?? ""
      let sid = cookies.first {
        $0.name == cookieName && ($0.domain == host || $0.domain == "." + host)
      }?.value
      done((sid?.isEmpty ?? true) ? nil : sid)
    }
  }

  private static func persist(_ result: @escaping FlutterResult) {
    currentSid { sid in
      if let sid = sid {
        Keychain.save(sid, service: keychainService, account: cookieName)
      } else {
        Keychain.delete(service: keychainService, account: cookieName)
      }
      result(sid != nil)
    }
  }

  /// A appeler AVANT le chargement de la premiere page.
  private static func restore(_ result: @escaping FlutterResult) {
    currentSid { sid in
      if sid != nil {
        result(true)
        return
      }
      guard let saved = Keychain.read(service: keychainService, account: cookieName),
        let cookie = HTTPCookie(properties: [
          .originURL: siteURL,
          .path: "/",
          .name: cookieName,
          .value: saved,
          .secure: "TRUE",
          .expires: Date().addingTimeInterval(maxAge),
          HTTPCookiePropertyKey("HttpOnly"): "TRUE",
          .sameSitePolicy: HTTPCookieStringPolicy.sameSiteLax.rawValue,
        ])
      else {
        result(false)
        return
      }
      cookieStore.setCookie(cookie) { result(true) }
    }
  }
}

enum Keychain {
  private static func query(_ service: String, _ account: String) -> [String: Any] {
    [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
    ]
  }

  static func save(_ value: String, service: String, account: String) {
    let data = Data(value.utf8)
    let base = query(service, account)
    let status = SecItemUpdate(base as CFDictionary, [kSecValueData as String: data] as CFDictionary)
    if status == errSecItemNotFound {
      var add = base
      add[kSecValueData as String] = data
      add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
      SecItemAdd(add as CFDictionary, nil)
    }
  }

  static func read(service: String, account: String) -> String? {
    var q = query(service, account)
    q[kSecReturnData as String] = true
    q[kSecMatchLimit as String] = kSecMatchLimitOne
    var item: CFTypeRef?
    guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess,
      let data = item as? Data
    else { return nil }
    return String(data: data, encoding: .utf8)
  }

  static func delete(service: String, account: String) {
    SecItemDelete(query(service, account) as CFDictionary)
  }
}
