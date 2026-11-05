package app.kanso

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.text.SpannableString
import android.text.Spanned
import android.text.format.DateFormat
import android.text.style.RelativeSizeSpan
import android.text.style.TypefaceSpan
import android.view.View
import android.widget.RemoteViews
import androidx.core.widget.RemoteViewsCompat
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Locale
import java.util.concurrent.Executors

// The home-screen widget: one account's unread inbox, newest first, in rows like the app's. Each
// widget shows the account picked when it was placed (WidgetSetup); for two accounts, place two.
// It reads Kanso's API with the WebView's cookie, as the notifications do, and redraws on each FCM
// message for its account (MessagingService), when the app is left (MainActivity.onStop), and
// every 30 minutes (updatePeriodMillis in xml/widget_info.xml) for when notifications are off.
class Widget : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val pending = goAsync()
        queue.execute {
            try {
                draw(context, manager, ids)
            } catch (error: JSONException) {
                // Not the JSON this app knows (a newer or older Kanso): the widget stays as it was.
            } finally {
                pending.finish()
            }
        }
    }

    override fun onDeleted(context: Context, ids: IntArray) {
        val edit = prefs(context).edit()
        for (id in ids) edit.remove("$id").remove("$id-email")
        edit.apply()
    }

    companion object {
        private const val PREFS = "widgets" // "<widget id>": its account's id, "<widget id>-email": its address
        private const val ROWS = 30
        private const val DAY = 86_400_000L

        // One redraw at a time, so an older answer never replaces a newer one.
        private val queue = Executors.newSingleThreadExecutor()

        /** What a widget shows: an account's unread inbox, or why it can't. */
        private sealed interface Inbox {
            data class Threads(val email: String, val unread: Int, val threads: JSONArray) : Inbox
            data class Problem(val text: String) : Inbox
        }

        /** Redraws the widgets that show this account, or all of them. */
        fun refresh(context: Context, accountId: Int? = null) {
            val manager = AppWidgetManager.getInstance(context) ?: return // a device without widgets
            val ids = manager.getAppWidgetIds(ComponentName(context, Widget::class.java))
                .filter { accountId == null || account(context, it) == accountId }
            if (ids.isNotEmpty()) update(context, ids.toIntArray())
        }

        /** The app left its server: the widgets forget their accounts, and say so. */
        fun forget(context: Context) {
            val manager = AppWidgetManager.getInstance(context) ?: return
            queue.execute {
                prefs(context).edit().clear().apply()
                val gone = Inbox.Problem("Kanso is on another server now.\nAdd this widget again to pick an account.")
                for (id in manager.getAppWidgetIds(ComponentName(context, Widget::class.java))) {
                    manager.updateAppWidget(id, views(context, id, null, "Kanso", gone))
                }
            }
        }

        /** The account a widget shows, picked in WidgetSetup; then it draws. */
        fun choose(context: Context, widgetId: Int, accountId: Int, email: String) {
            prefs(context).edit().putInt("$widgetId", accountId).putString("$widgetId-email", email).apply()
            update(context, intArrayOf(widgetId))
        }

        // Through onUpdate, whose goAsync() keeps the app alive for the requests.
        private fun update(context: Context, ids: IntArray) = context.sendBroadcast(
            Intent(context, Widget::class.java)
                .setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE)
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids),
        )

        private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        private fun account(context: Context, widgetId: Int) = prefs(context).getInt("$widgetId", -1).takeIf { it >= 0 }

        // Runs on the queue: one request for the accounts, then one per account shown.
        private fun draw(context: Context, manager: AppWidgetManager, ids: IntArray) {
            val shown = ids.asList().mapNotNull { id -> account(context, id)?.let { id to it } } // not before WidgetSetup
            if (shown.isEmpty()) return
            val answer = Server.load(context, "/api/accounts")
            val accounts = parse(answer) { JSONArray(it) }
            val inboxes = shown.map { it.second }.toSet().associateWith { accountId ->
                val account = accounts?.let { list -> (0 until list.length()).map(list::getJSONObject).firstOrNull { it.getInt("id") == accountId } }
                when {
                    accounts == null -> problem(answer)
                    account == null -> Inbox.Problem("This account isn’t in Kanso anymore.")
                    else -> {
                        val page = Server.load(context, "/api/threads?account_id=$accountId&mailbox=inbox&q=is%3Aunread&limit=$ROWS")
                        parse(page) { JSONObject(it).getJSONArray("threads") }
                            ?.let { Inbox.Threads(account.getString("email"), account.getInt("unread"), it) }
                            ?: problem(page)
                    }
                }
            }
            for ((id, accountId) in shown) {
                val email = prefs(context).getString("$id-email", null) ?: "Kanso"
                manager.updateAppWidget(id, views(context, id, accountId, email, inboxes.getValue(accountId)))
            }
        }

        /** A 200's JSON, or null (another status, no answer, or not Kanso's JSON). */
        private fun <T> parse(answer: Pair<Int, ByteArray?>?, read: (String) -> T): T? = try {
            answer?.second?.let { read(String(it)) }
        } catch (error: JSONException) {
            null
        }

        private fun problem(answer: Pair<Int, ByteArray?>?) = Inbox.Problem(
            if (answer?.first == 401) "Log in to Kanso to see your inbox here.\nTap to open it." else "Can’t reach Kanso.\nTap to open it.",
        )

        private fun views(context: Context, widgetId: Int, accountId: Int?, email: String, inbox: Inbox): RemoteViews {
            val views = RemoteViews(context.packageName, R.layout.widget)
            val open = Notifications.open(context, if (accountId != null) "/inbox?account=$accountId" else "/inbox")
            for (view in listOf(R.id.header, R.id.zero, R.id.message)) views.setOnClickPendingIntent(view, open)
            // The launcher applies these to what the widget shows now, not to a fresh layout:
            // whatever an earlier state set has to be set again.
            val shown = when {
                inbox is Inbox.Problem -> R.id.message
                (inbox as Inbox.Threads).threads.length() == 0 -> R.id.zero
                else -> R.id.list
            }
            for (view in listOf(R.id.list, R.id.zero, R.id.message)) views.setViewVisibility(view, if (view == shown) View.VISIBLE else View.GONE)
            if (inbox is Inbox.Problem) {
                views.setTextViewText(R.id.account, email)
                views.setTextViewText(R.id.count, "")
                views.setTextViewText(R.id.message, inbox.text)
                return views
            }
            inbox as Inbox.Threads
            views.setTextViewText(R.id.account, inbox.email)
            views.setTextViewText(R.id.count, if (inbox.unread > 0) "${inbox.unread}" else "")
            val server = Server.url(context).orEmpty()
            val items = RemoteViewsCompat.RemoteCollectionItems.Builder().setHasStableIds(true).setViewTypeCount(1)
            for (i in 0 until inbox.threads.length()) {
                val thread = inbox.threads.getJSONObject(i)
                val row = RemoteViews(context.packageName, R.layout.widget_row)
                row.setTextViewText(R.id.sender, people(thread))
                row.setTextViewText(R.id.time, shortDate(thread.getString("last_message_at")))
                row.setTextViewText(R.id.subject, thread.getString("subject").ifEmpty { "(no subject)" })
                // The link a notification opens: the thread, in its account (useAppLinks, App.tsx).
                val link = "$server/inbox/${thread.getInt("id")}?account=$accountId"
                row.setOnClickFillInIntent(R.id.row, Intent().setData(Uri.parse(link)))
                items.addItem(thread.getLong("id"), row)
            }
            RemoteViewsCompat.setRemoteAdapter(context, views, widgetId, R.id.list, items.build())
            views.setPendingIntentTemplate(R.id.list, rowIntent(context, widgetId))
            return views
        }

        /** Opens MainActivity like a notification's tap (Notifications.open), with the link each
         * row fills in: mutable for that. */
        private fun rowIntent(context: Context, widgetId: Int): PendingIntent {
            val intent = Intent(Intent.ACTION_VIEW, null, context, MainActivity::class.java)
            val mutable = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0 // mutable by default before Android 12
            return PendingIntent.getActivity(context, widgetId, intent, mutable or PendingIntent.FLAG_UPDATE_CURRENT)
        }

        /** As the app's rows (people in ThreadList.tsx): "Daniel Reyes, me", then the number of
         * messages, quieter. */
        private fun people(thread: JSONObject): CharSequence {
            val me = thread.optJSONArray("me").strings().toSet()
            val names = thread.getJSONArray("senders").strings().map { if (it in me) "me" else it }.distinct().joinToString(", ")
            val count = thread.optInt("message_count")
            if (count <= 1) return names
            val text = SpannableString("$names $count")
            text.setSpan(TypefaceSpan("sans-serif"), names.length, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            text.setSpan(RelativeSizeSpan(0.85f), names.length, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            return text
        }

        private fun JSONArray?.strings() = if (this == null) emptyList() else List(length(), ::getString)

        private val ISO = Regex("""(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?(Z|[+-]\d\d:\d\d)?""")

        /** As shortDate (src/app/format.ts): the time today, the weekday within six days, else the
         * date, with the year when it isn't this year's. */
        private fun shortDate(iso: String): String {
            val (time, _, zone) = ISO.matchEntire(iso)?.destructured ?: return ""
            val date = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).parse(time + zone.ifEmpty { "Z" }) ?: return ""
            val now = Calendar.getInstance()
            val then = Calendar.getInstance().apply { this.time = date }
            val sameYear = then.get(Calendar.YEAR) == now.get(Calendar.YEAR)
            val skeleton = when {
                sameYear && then.get(Calendar.DAY_OF_YEAR) == now.get(Calendar.DAY_OF_YEAR) -> "Hm"
                now.timeInMillis - date.time < 6 * DAY -> "EEE"
                sameYear -> "MMMd"
                else -> "yMMMd"
            }
            val locale = Locale.getDefault()
            return SimpleDateFormat(DateFormat.getBestDateTimePattern(locale, skeleton), locale).format(date)
        }
    }
}
