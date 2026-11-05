package app.kanso

import android.content.Context
import com.google.android.gms.tasks.Tasks
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import org.json.JSONException
import org.json.JSONObject
import java.util.concurrent.TimeUnit

// Firebase, which FCM needs, started with the settings the Kanso server hands out (GET
// /api/notifications/firebase, from its FIREBASE_ANDROID_CONFIG) rather than a
// google-services.json built in: one app for every server. They're kept for the next starts.
object Push {
    private const val PREFS = "kanso"
    private const val FIREBASE = "firebase" // the server's JSON: project_id, app_id, api_key, sender_id

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // Only the default app is ever made.
    private fun app(context: Context) = FirebaseApp.getApps(context).firstOrNull()

    private fun options(json: String): FirebaseOptions? = try {
        val settings = JSONObject(json)
        FirebaseOptions.Builder()
            .setProjectId(settings.getString("project_id"))
            .setApplicationId(settings.getString("app_id"))
            .setApiKey(settings.getString("api_key"))
            .setGcmSenderId(settings.getString("sender_id"))
            .build()
    } catch (error: JSONException) {
        null
    }

    /** Starts Firebase with the kept settings, at each start of the app's process (KansoApp): an
     * FCM message can start it with no activity. */
    fun start(context: Context) {
        val options = prefs(context).getString(FIREBASE, null)?.let(::options) ?: return
        if (app(context) == null) FirebaseApp.initializeApp(context, options)
    }

    fun started(context: Context) = app(context) != null

    /** Asks Kanso for its Firebase settings and starts Firebase with them: false when Kanso has
     * none (FCM can't work); when it can't be asked (offline, logged out), whether Firebase runs
     * with the kept ones. Call from a background thread. */
    fun setup(context: Context): Boolean {
        val answer = Server.load(context, "/api/notifications/firebase")
        if (answer?.first == 404) {
            stop(context)
            return false
        }
        val json = answer?.second?.let { String(it) }
        val options = json?.let(::options) ?: return started(context)
        if (app(context)?.options != options) {
            // Another Firebase project: its token is no use. Notifications are off until turned on.
            stop(context)
            prefs(context).edit().putString(FIREBASE, json).apply()
            FirebaseApp.initializeApp(context, options)
        }
        return true
    }

    /** Stops Firebase and forgets its settings. The FCM token is deleted, here and in Kanso (best
     * effort), so nothing is sent to it anymore: notifications are off. Call from a background
     * thread. */
    @Suppress("DEPRECATION") // deleteToken: as getToken, see MessagingService.onNewToken
    fun stop(context: Context) {
        Notifications.token(context)?.let { token ->
            Server.send(context, "DELETE", "/api/notifications/fcm", JSONObject().put("token", token).toString())
        }
        Notifications.remember(context, null)
        app(context)?.let { app ->
            try {
                Tasks.await(FirebaseMessaging.getInstance().deleteToken(), 10, TimeUnit.SECONDS)
            } catch (error: Exception) {
                // Offline or slow: FCM drops a token unused for long, and Kanso no longer has it.
            }
            app.delete()
        }
        prefs(context).edit().remove(FIREBASE).apply()
    }
}
