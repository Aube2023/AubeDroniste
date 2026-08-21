package com.aubeetoilee.aubepilot

import android.webkit.CookieManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

/**
 * Persistance de la connexion entre deux lancements de l'app.
 *
 * Le CookieManager d'Android n'ecrit ses cookies sur disque que par
 * intermittence : si le systeme tue l'app avant cette ecriture, le cookie de
 * session disparait et l'utilisateur doit se reconnecter a chaque lancement.
 * Ce canal natif :
 *  - force l'ecriture disque (flush) apres chaque page et a la mise en pause ;
 *  - garde une copie du cookie de session dans les SharedPreferences et le
 *    reinjecte au demarrage si la WebView l'a perdu.
 * Necessairement natif : le cookie est HttpOnly, donc invisible depuis le
 * JavaScript de la page ; seul le CookieManager natif peut le lire.
 */
class MainActivity : FlutterActivity() {
    private val channelName = "aubepilot/session"
    private val siteUrl = "https://pilot.aubeetoilee.com"
    private val cookieName = "aubepilot_sid"
    private val prefsName = "aubepilot_session"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "persistSession" -> result.success(persistSession())
                    "restoreSession" -> result.success(restoreSession())
                    else -> result.notImplemented()
                }
            }
    }

    private fun currentSid(): String? {
        val cookies = CookieManager.getInstance().getCookie(siteUrl) ?: return null
        return cookies.split(';')
            .map { it.trim() }
            .firstOrNull { it.startsWith("$cookieName=") }
            ?.substringAfter('=')
            ?.takeIf { it.isNotEmpty() }
    }

    /** Copie le cookie de session dans les prefs (ou l'efface apres une
     *  deconnexion, pour ne pas ressusciter une session revoquee), puis flush. */
    private fun persistSession(): Boolean {
        val sid = currentSid()
        val prefs = getSharedPreferences(prefsName, MODE_PRIVATE)
        if (sid != null) {
            prefs.edit().putString(cookieName, sid).apply()
        } else {
            prefs.edit().remove(cookieName).apply()
        }
        CookieManager.getInstance().flush()
        return sid != null
    }

    /** Reinjecte le cookie sauvegarde si la WebView l'a perdu.
     *  A appeler AVANT le chargement de la premiere page. */
    private fun restoreSession(): Boolean {
        if (currentSid() != null) return true
        val saved = getSharedPreferences(prefsName, MODE_PRIVATE)
            .getString(cookieName, null) ?: return false
        val manager = CookieManager.getInstance()
        manager.setCookie(
            siteUrl,
            "$cookieName=$saved; Path=/; Max-Age=2592000; Secure; HttpOnly; SameSite=Lax",
        )
        manager.flush()
        return true
    }

    override fun onPause() {
        // Filet de securite : ecrit les cookies sur disque des que l'app passe
        // en arriere-plan, avant que le systeme puisse tuer le processus.
        CookieManager.getInstance().flush()
        super.onPause()
    }
}
