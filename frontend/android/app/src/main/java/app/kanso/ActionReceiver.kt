package app.kanso

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import org.json.JSONArray
import org.json.JSONObject
import java.util.TimeZone
import kotlin.concurrent.thread

// A new-mail notification's Done, Delete and Reply buttons: the change or reply goes to kanso in
// the background, so the notification shade stays open. Done and Delete remove the notification;
// a reply shows under it once sent.
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.getStringExtra(ACTION) ?: return
        val accountId = intent.getIntExtra(ACCOUNT_ID, -1)
        val threadId = intent.getIntExtra(THREAD_ID, -1)
        val text = if (action == REPLY) RemoteInput.getResultsFromIntent(intent)?.getCharSequence(REPLY)?.toString()?.trim() else null
        if (action == REPLY && text.isNullOrEmpty()) return
        if (action != REPLY) Notifications.cancel(context, accountId, threadId)
        val pending = goAsync()
        thread {
            try {
                val failure = if (text != null) reply(context, intent.getIntExtra(MESSAGE_ID, -1), text, threadId) else change(context, action, threadId)
                if (failure != null) {
                    val url = intent.getStringExtra(URL).orEmpty().ifEmpty { "/inbox?account=$accountId" }
                    Notifications.failed(context, accountId, intent.getStringExtra(ACCOUNT).orEmpty(), threadId, failure, url)
                }
            } finally {
                pending.finish()
            }
        }
    }

    /** null when it worked, else what to tell. */
    private fun change(context: Context, action: String, threadId: Int): String? {
        val body = JSONObject().put("action", action).put("thread_ids", JSONArray().put(threadId))
        val status = Server.send(context, "POST", "/api/threads/actions", body.toString())
        return if (status != null && status in 200..299) null else FAILED[action] ?: "Couldn’t change it"
    }

    /** null when sent (or left to kanso's worker to finish, 202), else what to tell. Sending takes
     * a few Gmail calls, so it gets longer than the usual timeout, within the receiver's ~10 s.
     * No answer or a server error may still mean it went out: then it says so rather than
     * inviting a second send. */
    private fun reply(context: Context, messageId: Int, text: String, threadId: Int): String? {
        val body = JSONObject().put("message_id", messageId).put("text", text).put("tz", TimeZone.getDefault().id)
        val status = Server.send(context, "POST", "/api/reply", body.toString(), readTimeout = 6000)
        if (status != null && status in 200..299) {
            Notifications.replied(context, threadId, text)
            return null
        }
        return if (status == null || status >= 500) "Couldn’t confirm your reply was sent" else "Couldn’t send your reply"
    }

    companion object {
        private const val ACTION = "action"
        private const val ACCOUNT_ID = "account_id"
        private const val ACCOUNT = "account"
        private const val THREAD_ID = "thread_id"
        private const val MESSAGE_ID = "message_id"
        private const val URL = "url"
        private const val REPLY = "reply" // the action, and the RemoteInput's key
        private val FAILED = mapOf("done" to "Couldn’t mark it done", "delete" to "Couldn’t delete it")

        fun intent(context: Context, action: String, accountId: Int, account: String, threadId: Int, url: String): PendingIntent =
            pending(context, base(context, action, accountId, account, threadId, url), threadId, mutable = false)

        /** The Reply button, with its text field. Its PendingIntent is mutable: Android adds the
         * typed text to it. */
        fun replyAction(context: Context, accountId: Int, account: String, threadId: Int, url: String, messageId: Int): NotificationCompat.Action {
            val intent = base(context, REPLY, accountId, account, threadId, url).putExtra(MESSAGE_ID, messageId)
            val input = RemoteInput.Builder(REPLY).setLabel("Reply").build()
            return NotificationCompat.Action.Builder(0, "Reply", pending(context, intent, threadId, mutable = true))
                .addRemoteInput(input)
                .setSemanticAction(NotificationCompat.Action.SEMANTIC_ACTION_REPLY)
                .setAllowGeneratedReplies(false)
                .setShowsUserInterface(false)
                .build()
        }

        private fun base(context: Context, action: String, accountId: Int, account: String, threadId: Int, url: String) =
            Intent(context, ActionReceiver::class.java)
                .setAction("app.kanso.$action") // with the thread as the request code, one per button
                .putExtra(ACTION, action)
                .putExtra(ACCOUNT_ID, accountId)
                .putExtra(ACCOUNT, account)
                .putExtra(THREAD_ID, threadId)
                .putExtra(URL, url)

        private fun pending(context: Context, intent: Intent, threadId: Int, mutable: Boolean): PendingIntent {
            val mutability = when {
                !mutable -> PendingIntent.FLAG_IMMUTABLE
                Build.VERSION.SDK_INT >= 31 -> PendingIntent.FLAG_MUTABLE
                else -> 0 // mutable by default before Android 12
            }
            return PendingIntent.getBroadcast(context, threadId, intent, mutability or PendingIntent.FLAG_UPDATE_CURRENT)
        }
    }
}
