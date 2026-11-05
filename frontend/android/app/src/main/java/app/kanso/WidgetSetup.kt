package app.kanso

import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import kotlin.concurrent.thread

// Picks the account a widget shows, when it's placed (and on Android 12+ when it's reconfigured
// with a long press). With one account there's nothing to pick: it's chosen at once.
class WidgetSetup : AppCompatActivity() {
    private var widgetId = AppWidgetManager.INVALID_APPWIDGET_ID

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        widgetId = intent.getIntExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID)
        // Leaving without a pick (Back, a tap outside) doesn't place the widget.
        setResult(RESULT_CANCELED, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
        // Launchers start it from outside the app, so it's exported: only for kanso's own widgets.
        val provider = AppWidgetManager.getInstance(this).getAppWidgetInfo(widgetId)?.provider
        if (provider != ComponentName(this, Widget::class.java)) return finish()
        setContentView(R.layout.widget_setup)
        if (Server.url(this) == null) {
            findViewById<TextView>(R.id.status).text = "Open kanso and connect it to your server first, then add the widget again."
            return
        }
        thread {
            val answer = Server.load(this, "/api/accounts")
            runOnUiThread { if (!isDestroyed) show(answer) }
        }
    }

    private fun show(answer: Pair<Int, ByteArray?>?) {
        val status = findViewById<TextView>(R.id.status)
        val accounts = try {
            answer?.second?.let { JSONArray(String(it)) }
        } catch (error: JSONException) {
            null
        }
        when {
            answer?.first == 401 -> status.text = "Log in to kanso first, then add the widget again."
            accounts == null -> status.text = "Can’t reach kanso. Check your connection, then add the widget again."
            accounts.length() == 0 -> status.text = "Connect a Gmail account in kanso first."
            accounts.length() == 1 -> pick(accounts.getJSONObject(0))
            else -> {
                status.visibility = View.GONE
                val list = findViewById<LinearLayout>(R.id.accounts)
                for (i in 0 until accounts.length()) {
                    val account = accounts.getJSONObject(i)
                    val row = layoutInflater.inflate(R.layout.widget_setup_account, list, false)
                    row.findViewById<TextView>(R.id.email).text = account.getString("email")
                    val name = row.findViewById<TextView>(R.id.name)
                    name.text = account.getString("display_name")
                    if (name.text.isEmpty()) name.visibility = View.GONE
                    row.setOnClickListener { pick(account) }
                    list.addView(row)
                }
            }
        }
    }

    private fun pick(account: JSONObject) {
        Widget.choose(this, widgetId, account.getInt("id"), account.getString("email"))
        setResult(RESULT_OK, Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId))
        finish()
    }
}
