package app.kanso

import android.app.Activity
import android.content.Context
import android.net.Uri
import android.webkit.CookieManager
import android.webkit.WebStorage
import android.webkit.WebView
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

// The kanso server the app loads, as entered on ServerActivity and kept in SharedPreferences, and
// requests to it with the WebView's session cookie, for when no page is there to make them: a
// notification arriving, or one of its buttons. Call the requests from a background thread.
object Server {
    private const val PREFS = "kanso"
    private const val SERVER = "server"

    /** The server's origin (https://kanso.example.com), or null before one was entered. */
    fun url(context: Context): String? = prefs(context).getString(SERVER, null)

    fun remember(context: Context, url: String) = prefs(context).edit().putString(SERVER, url).apply()

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** An address as typed ("kanso.example.com", "https://kanso.example.com/inbox/") as an origin,
     * https:// when it has no scheme; null when it isn't a web address. */
    fun normalize(typed: String): String? {
        val text = typed.trim().let { if ("://" in it) it else "https://$it" }
        val uri = Uri.parse(text)
        val scheme = uri.scheme?.lowercase()
        val authority = uri.encodedAuthority?.substringAfterLast('@')?.lowercase()
        if (scheme !in listOf("https", "http") || uri.host.isNullOrEmpty() || authority.isNullOrEmpty()) return null
        return "$scheme://$authority"
    }

    /** Whether kanso answers at [url]: its /api/health says so (healthy or not), without a login. */
    fun isKanso(url: String): Boolean = try {
        val connection = URL("$url/api/health").openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 8000
            connection.readTimeout = 8000
            val body = (if (connection.responseCode < 400) connection.inputStream else connection.errorStream)
                ?.use { String(it.readBytes()) }
            body != null && JSONObject(body).optString("app") == "kanso"
        } finally {
            connection.disconnect()
        }
    } catch (error: IOException) {
        false
    } catch (error: JSONException) {
        false
    }

    /** Leaves this server, for another or to ask again: its FCM token and Firebase settings, the
     * notifications, the widgets' accounts and the WebView's cookies and storage go, and the
     * stored address too. Best effort while the old server can't be reached. [done] runs on the
     * main thread. */
    fun leave(activity: Activity, done: () -> Unit) {
        thread {
            Push.stop(activity) // first: it tells the old server to drop the token
            Notifications.forget(activity)
            Widget.forget(activity)
            prefs(activity).edit().remove(SERVER).apply()
            activity.runOnUiThread {
                CookieManager.getInstance().removeAllCookies(null)
                CookieManager.getInstance().flush()
                WebStorage.getInstance().deleteAllData() // the page's storage: local storage, IndexedDB, caches
                WebView(activity).run {
                    clearCache(true) // the HTTP cache, shared by all of the app's WebViews
                    destroy()
                }
                done()
            }
        }
    }

    /** The response's status, or null when the server couldn't be reached or didn't answer in
     * [readTimeout] ms. */
    fun send(context: Context, method: String, path: String, json: String, readTimeout: Int = 4000): Int? =
        request(context, method, path, readTimeout) { connection ->
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(json.toByteArray()) }
            connection.responseCode
        }

    /** The body, or null on any failure. */
    fun get(context: Context, path: String): ByteArray? =
        request(context, "GET", path) { connection ->
            if (connection.responseCode == 200) connection.inputStream.use { it.readBytes() } else null
        }

    /** The status, with the body when it's 200; null when the server couldn't be reached. */
    fun load(context: Context, path: String): Pair<Int, ByteArray?>? =
        request(context, "GET", path) { connection ->
            val status = connection.responseCode
            status to (if (status == 200) connection.inputStream.use { it.readBytes() } else null)
        }

    private fun <T> request(context: Context, method: String, path: String, readTimeout: Int = 4000, use: (HttpURLConnection) -> T): T? {
        val server = url(context) ?: return null
        return try {
            val connection = URL(server + path).openConnection() as HttpURLConnection
            try {
                connection.requestMethod = method
                // Short: a notification's button has about 10 seconds in all.
                connection.connectTimeout = 4000
                connection.readTimeout = readTimeout
                CookieManager.getInstance().getCookie(server)?.let { connection.setRequestProperty("Cookie", it) }
                // kanso refuses changes without it (backend core/hardening.py).
                connection.setRequestProperty("X-Requested-With", "kanso")
                use(connection)
            } finally {
                connection.disconnect()
            }
        } catch (error: IOException) {
            null
        }
    }
}
