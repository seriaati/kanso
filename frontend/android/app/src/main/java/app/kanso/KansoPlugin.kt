package app.kanso

import android.Manifest
import android.content.Intent
import android.content.res.Configuration
import android.graphics.Color
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.view.WindowCompat
import com.getcapacitor.JSObject
import com.getcapacitor.PermissionState
import com.getcapacitor.Plugin
import com.getcapacitor.PluginCall
import com.getcapacitor.PluginMethod
import com.getcapacitor.annotation.CapacitorPlugin
import com.getcapacitor.annotation.Permission
import com.getcapacitor.annotation.PermissionCallback
import com.google.firebase.messaging.FirebaseMessaging
import kotlin.concurrent.thread

private const val NOTIFICATIONS = "notifications"

// The app's own bridge to the web app (src/app/native.ts).
@CapacitorPlugin(
    name = "Kanso",
    permissions = [Permission(alias = NOTIFICATIONS, strings = [Manifest.permission.POST_NOTIFICATIONS])],
)
class KansoPlugin : Plugin() {
    private var bars: Pair<Int, Boolean>? = null

    // A token change Kanso missed (onNewToken couldn't reach it) is caught up on at each start.
    @Suppress("DEPRECATION") // getToken: see MessagingService.onNewToken
    override fun load() {
        if (!Push.started(context) || Notifications.token(context) == null) return
        FirebaseMessaging.getInstance().token.addOnSuccessListener { token -> thread { Notifications.renew(context, token) } }
    }

    // Plugin methods run on Capacitor's own background thread: they can wait for Kanso.

    /** New-mail notifications on this phone (src/app/notifications.ts): `on`, `off`, `denied` in
     * Android's settings, or `unsupported` when the Kanso server has no Firebase settings for the
     * app (FIREBASE_ANDROID_CONFIG). `token`: the one Kanso has, while they're on. */
    @PluginMethod
    fun notificationState(call: PluginCall) {
        val firebase = Push.setup(context)
        val token = Notifications.token(context)
        val state = when {
            !firebase -> "unsupported"
            NotificationManagerCompat.from(context).areNotificationsEnabled() -> if (token != null) "on" else "off"
            // Android 13+ asks for them; it stops asking after the second no.
            Build.VERSION.SDK_INT < 33 || getPermissionState(NOTIFICATIONS) == PermissionState.DENIED -> "denied"
            else -> "off"
        }
        call.resolve(JSObject().put("state", state).put("token", token))
    }

    /** Asks for permission to notify (Android 13+), then resolves with the FCM token for Kanso,
     * or without one when permission is refused. */
    @PluginMethod
    fun requestNotifications(call: PluginCall) {
        if (!Push.setup(context)) return call.reject("This Kanso server isn’t set up for notifications in the app.")
        if (Build.VERSION.SDK_INT >= 33 && getPermissionState(NOTIFICATIONS) != PermissionState.GRANTED) {
            requestPermissionForAlias(NOTIFICATIONS, call, "notificationsAnswered")
        } else {
            token(call)
        }
    }

    @PermissionCallback
    private fun notificationsAnswered(call: PluginCall) {
        if (getPermissionState(NOTIFICATIONS) == PermissionState.GRANTED) token(call) else call.resolve()
    }

    @Suppress("DEPRECATION") // getToken: see MessagingService.onNewToken
    private fun token(call: PluginCall) {
        FirebaseMessaging.getInstance().token
            .addOnSuccessListener { call.resolve(JSObject().put("token", it)) }
            .addOnFailureListener { call.reject("Firebase gave no token: ${it.message}") }
    }

    /** Once Kanso has the token (or has dropped it, with `token` null): notifications are on (off). */
    @PluginMethod
    fun rememberNotifications(call: PluginCall) {
        Notifications.remember(context, call.getString("token"))
        call.resolve()
    }

    /** Leaves this Kanso server for the screen that asks for one (ServerActivity): what belongs
     * to this one (login, notifications, widgets' accounts) goes. */
    @PluginMethod
    fun changeServer(call: PluginCall) {
        val activity = activity
        Server.leave(activity) {
            call.resolve()
            activity.startActivity(Intent(activity, ServerActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        }
    }

    /** The status and navigation bars take the page's color, with icons for a light or dark page. */
    @PluginMethod
    fun setSystemBars(call: PluginCall) {
        val color = try {
            Color.parseColor(call.getString("color"))
        } catch (error: IllegalArgumentException) {
            return call.reject("Not a color: ${call.getString("color")}")
        }
        bars = color to (call.getBoolean("dark") ?: false)
        activity.runOnUiThread {
            applyBars()
            call.resolve()
        }
    }

    // Capacitor's SystemBars resets them to the system theme when the configuration changes
    // (rotation, dark mode): put the page's back after it.
    override fun handleOnConfigurationChanged(newConfig: Configuration) {
        super.handleOnConfigurationChanged(newConfig)
        activity.window.decorView.post { applyBars() }
    }

    // SystemBars keeps the WebView between the bars (the page has no viewport-fit=cover), so what
    // shows behind them is the window's own background.
    private fun applyBars() {
        val (color, dark) = bars ?: return
        val window = activity.window
        window.decorView.setBackgroundColor(color)
        WindowCompat.getInsetsController(window, window.decorView).apply {
            isAppearanceLightStatusBars = !dark
            isAppearanceLightNavigationBars = !dark
        }
    }
}
