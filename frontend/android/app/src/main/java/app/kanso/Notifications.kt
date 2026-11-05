package app.kanso

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Shader
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.service.notification.StatusBarNotification
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import kotlin.math.abs
import kotlin.math.max

// New-mail notifications, drawn from the backend's FCM data messages (sync/notify.py):
// "mail" (one new message) or "summary" (a burst of them), and "clear" when threads were read,
// archived or deleted anywhere. Each account has its own channel and group, and each thread one
// notification, which a newer message in it updates.
object Notifications {
    private const val PREFS = "kanso"
    private const val TOKEN = "fcm_token"
    private const val THREAD = "thread" // tag of a thread's notification, its id the thread's
    private const val BURST = "burst" // tag of a "summary" message, its id the account's
    private const val GROUP = "account" // tag of an account's group summary, its id the account's
    private const val SENT = "app.kanso.sent" // extra: when FCM got the message it shows

    /** The FCM token Kanso has, when notifications are on in this app. */
    fun token(context: Context): String? = prefs(context).getString(TOKEN, null)

    fun remember(context: Context, token: String?) = prefs(context).edit().putString(TOKEN, token).apply()

    /** Hands Kanso the app's new token, when notifications are on. Call from a background thread. */
    fun renew(context: Context, token: String) {
        if (token(context).let { it == null || it == token }) return
        val status = Server.send(context, "PUT", "/api/notifications/fcm", JSONObject().put("token", token).toString())
        if (status in 200..299) remember(context, token)
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** [sent]: when FCM got the message from Kanso. */
    fun show(context: Context, data: Map<String, String>, sent: Long) {
        val accountId = data["account_id"]?.toIntOrNull() ?: return
        if (data["type"] == "clear") return clear(context, accountId, data["thread_ids"].orEmpty(), sent)
        val account = data["account"].orEmpty()
        val builder = builder(context, accountId, account)
            .setContentIntent(open(context, data["url"] ?: "/inbox"))
            .addExtras(Bundle().apply { putLong(SENT, sent) })
        when (data["type"]) {
            "mail" -> {
                val threadId = data["thread_id"]?.toIntOrNull() ?: return
                val sender = data["sender"].orEmpty()
                val subject = data["subject"].orEmpty()
                val snippet = data["snippet"].orEmpty()
                // Collapsed: "sender | subject", then the snippet. Expanded: the sender, then the
                // subject and the snippet on lines of their own.
                builder
                    .setContentTitle("$sender | $subject")
                    .setContentText(snippet)
                    .setStyle(
                        NotificationCompat.BigTextStyle()
                            .setBigContentTitle(sender)
                            .bigText(listOf(subject, snippet).filter { it.isNotEmpty() }.joinToString("\n")),
                    )
                    .setLargeIcon(picture(context, sender, data["image"]))
                val url = data["url"].orEmpty()
                for ((action, title) in listOf("done" to "Done", "delete" to "Delete")) {
                    builder.addAction(0, title, ActionReceiver.intent(context, action, accountId, account, threadId, url))
                }
                // A verification mail gets Copy code instead of Reply.
                val code = data["code"]
                if (code != null) {
                    builder.addAction(0, "Copy code", ActionReceiver.copyIntent(context, accountId, account, threadId, url, code))
                } else {
                    data["message_id"]?.toIntOrNull()?.let { builder.addAction(ActionReceiver.replyAction(context, accountId, account, threadId, url, it)) }
                }
                post(context, accountId, account, THREAD, threadId, builder)
            }
            "summary" -> {
                builder.setContentTitle(data["title"]).setContentText(account)
                post(context, accountId, account, BURST, accountId, builder)
            }
        }
    }

    /** Removes them all: they belong to a server the app is leaving. */
    fun forget(context: Context) = NotificationManagerCompat.from(context).cancelAll()

    /** Removes a thread's notification, and its account's group when nothing is left in it. */
    fun cancel(context: Context, accountId: Int, threadId: Int) {
        NotificationManagerCompat.from(context).cancel(THREAD, threadId)
        cancel(context, accountId) { it.tag == THREAD && it.id == threadId }
    }

    /** A "clear" message: these threads (comma-separated ids) were read, archived or deleted
     * somewhere. No ids: the account has no unread mail in the inbox left, so its burst summary
     * goes too. Only what FCM got before the clear goes, as clears aren't urgent and newer mail in
     * the thread can overtake one. */
    private fun clear(context: Context, accountId: Int, threadIds: String, sent: Long) {
        val ids = threadIds.split(',').mapNotNull { it.toIntOrNull() }.toSet()
        cancel(context, accountId) {
            val picked = if (ids.isEmpty()) it.tag == THREAD || it.tag == BURST else it.tag == THREAD && it.id in ids
            picked && it.notification.extras.getLong(SENT) <= sent
        }
    }

    /** Removes the account's notifications that [which] picks, and its group summary when
     * nothing else is left in the group. */
    private fun cancel(context: Context, accountId: Int, which: (StatusBarNotification) -> Boolean) {
        val manager = NotificationManagerCompat.from(context)
        val group = "account-$accountId"
        val (gone, others) = manager.activeNotifications
            .filter { it.notification.group == group && it.notification.flags and android.app.Notification.FLAG_GROUP_SUMMARY == 0 }
            .partition(which)
        for (n in gone) manager.cancel(n.tag, n.id)
        if (others.isEmpty()) manager.cancel(GROUP, accountId)
    }

    /** After a reply from the notification: it shows under the message, which also ends the
     * reply's spinner, as Android expects. */
    fun replied(context: Context, threadId: Int, text: CharSequence) {
        if (!allowed(context)) return
        val manager = NotificationManagerCompat.from(context)
        val shown = manager.activeNotifications.firstOrNull { it.tag == THREAD && it.id == threadId } ?: return
        val notification = android.app.Notification.Builder.recoverBuilder(context, shown.notification)
            .setRemoteInputHistory(arrayOf(text))
            .build()
        manager.notify(THREAD, threadId, notification)
    }

    /** A button's change didn't reach Kanso (offline, logged out): a quiet note that opens the
     * thread instead. Android 12+ doesn't let a button's receiver open the app itself. */
    fun failed(context: Context, accountId: Int, account: String, threadId: Int, text: String, url: String) {
        val builder = builder(context, accountId, account)
            .setContentTitle(text)
            .setContentText("Tap to open it in Kanso.")
            .setContentIntent(open(context, url))
            .setSilent(true)
        post(context, accountId, account, THREAD, threadId, builder)
    }

    private fun builder(context: Context, accountId: Int, account: String) =
        NotificationCompat.Builder(context, channel(context, accountId, account))
            .setSmallIcon(R.drawable.ic_notification)
            .setSubText(account) // in the header, after "Kanso"
            .setCategory(NotificationCompat.CATEGORY_EMAIL)
            .setGroup("account-$accountId")
            .setAutoCancel(true)
            .setOnlyAlertOnce(true) // a newer message in the thread updates it quietly

    private fun channel(context: Context, accountId: Int, account: String): String {
        val id = "account-$accountId"
        val channel = NotificationChannelCompat.Builder(id, NotificationManagerCompat.IMPORTANCE_HIGH)
            .setName(account.ifEmpty { "Kanso" })
            .setDescription("New mail in the inbox")
            .build()
        NotificationManagerCompat.from(context).createNotificationChannel(channel) // or renames it
        return id
    }

    private fun allowed(context: Context) =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun post(context: Context, accountId: Int, account: String, tag: String, id: Int, builder: NotificationCompat.Builder) {
        if (!allowed(context)) return
        val manager = NotificationManagerCompat.from(context)
        manager.notify(tag, id, builder.build())
        // The group's summary: Android shows it when the account has several notifications.
        val summary = builder(context, accountId, account)
            .setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setSilent(true)
            .setContentIntent(open(context, "/inbox?account=$accountId"))
        manager.notify(GROUP, accountId, summary.build())
    }

    /** Opens `url` (a path on the server) in the app: MainActivity hands it to the web app as an
     * appUrlOpen link, and the account comes from the link's ?account=. */
    fun open(context: Context, url: String): PendingIntent {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(Server.url(context).orEmpty() + url), context, MainActivity::class.java)
        return PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    // As accountColors (src/app/accounts.ts), which letterAvatar (src/app/format.ts) picks from.
    private val COLORS = listOf("#2F4B6E", "#A07338", "#556B3F", "#87607E", "#A35A3A", "#4F6E8C", "#6B6B66").map(Color::parseColor)
    private val PAPER = Color.parseColor("#F7F5EE")

    /** The sender's photo or logo in a circle, else their initial, as the app draws them
     * (public/push-sw.js does the same for Web Push). An SVG logo doesn't decode: the initial. */
    private fun picture(context: Context, name: String, image: String?): Bitmap {
        val size = context.resources.getDimensionPixelSize(android.R.dimen.notification_large_icon_width)
        val photo = image?.takeIf { it.startsWith("/") }
            ?.let { Server.get(context, it) }
            ?.let { BitmapFactory.decodeByteArray(it, 0, it.size) }
        val square = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(square)
        if (photo != null) {
            canvas.drawColor(PAPER) // logos with transparency need a light ground
            val scale = max(size.toFloat() / photo.width, size.toFloat() / photo.height)
            val (w, h) = photo.width * scale to photo.height * scale
            canvas.drawBitmap(photo, null, RectF((size - w) / 2, (size - h) / 2, (size + w) / 2, (size + h) / 2), Paint(Paint.FILTER_BITMAP_FLAG))
        } else {
            val letter = name.codePoints().filter { Character.isLetterOrDigit(it) }.findFirst()
            var hash = 0
            name.lowercase().codePoints().forEach { hash = hash * 31 + it }
            canvas.drawColor(COLORS[(abs(hash.toLong()) % COLORS.size).toInt()])
            val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
                color = PAPER
                textSize = size * 0.45f
                typeface = Typeface.DEFAULT_BOLD
                textAlign = Paint.Align.CENTER
            }
            val text = if (letter.isPresent) String(Character.toChars(letter.asInt)).uppercase() else "?"
            canvas.drawText(text, size / 2f, size / 2f - (paint.descent() + paint.ascent()) / 2 + size * 0.03f, paint)
        }
        val round = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { shader = BitmapShader(square, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP) }
        Canvas(round).drawCircle(size / 2f, size / 2f, size / 2f, paint)
        return round
    }
}
