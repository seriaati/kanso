package moe.seria.kanso

import android.app.DownloadManager
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.webkit.CookieManager
import android.webkit.URLUtil
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import com.getcapacitor.Bridge
import com.getcapacitor.BridgeActivity
import com.getcapacitor.BridgeWebViewClient
import com.getcapacitor.CapConfig
import org.json.JSONObject
import java.io.File

class MainActivity : BridgeActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        registerPlugin(KansoPlugin::class.java)
        super.onCreate(savedInstanceState)
        val bridge = bridge ?: return // no server yet (load)
        bridge.webView.setDownloadListener { url, userAgent, disposition, mimeType, _ -> download(url, userAgent, disposition, mimeType) }
        bridge.webViewClient = Client(bridge)
    }

    // The server comes from ServerActivity, not the bundled config: Capacitor's config is the
    // bundled one (capacitor.config.ts) with that server as server.url. The bridge takes the page
    // to load, the origin its JavaScript is injected into and the hosts the WebView may show from
    // it. Without a server yet, ServerActivity asks for one.
    override fun load() {
        val server = Server.url(this) ?: return askServer(unreachable = false)
        val json = JSONObject(assets.open("capacitor.config.json").bufferedReader().use { it.readText() })
        json.put("server", (json.optJSONObject("server") ?: JSONObject()).put("url", server))
        // CapConfig reads from assets or a file only.
        val dir = File(noBackupFilesDir, "capacitor").apply { mkdirs() }
        File(dir, "capacitor.config.json").writeText(json.toString())
        config = CapConfig.loadFromFile(this, dir.path)
        super.load()
    }

    private fun askServer(unreachable: Boolean) {
        startActivity(Intent(this, ServerActivity::class.java).putExtra(ServerActivity.UNREACHABLE, unreachable))
        finish()
    }

    // When the page itself can't load (no network, the server down), ServerActivity shows, with
    // Retry and Change server, instead of the WebView's error page. A page the service worker
    // serves from its cache has loaded: the web app then shows that it's offline itself.
    private inner class Client(bridge: Bridge) : BridgeWebViewClient(bridge) {
        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            super.onReceivedError(view, request, error)
            // ERROR_UNKNOWN is also a load that was cancelled (a link opened in the browser instead).
            if (request.isForMainFrame && error.errorCode != WebViewClient.ERROR_UNKNOWN) unreachable()
        }

        // A proxy's "502 Bad Gateway" while Kanso restarts, say.
        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, response: WebResourceResponse) {
            super.onReceivedHttpError(view, request, response)
            if (request.isForMainFrame && response.statusCode >= 500) unreachable()
        }

        private fun unreachable() {
            if (!isFinishing) askServer(unreachable = true)
        }
    }

    // What was read, done or deleted in the app shows on the home-screen widgets once it's left.
    override fun onStop() {
        super.onStop()
        Widget.refresh(this)
    }

    // A WebView downloads nothing itself: attachment links (/api/attachments/…, with a download
    // attribute or Content-Disposition: attachment) go to Android's DownloadManager, with the
    // session cookie, into Downloads.
    private fun download(url: String, userAgent: String, disposition: String?, mimeType: String?) {
        if (!url.startsWith("http")) return // blob: or data:, which Kanso doesn't link to
        val name = fileName(disposition) ?: URLUtil.guessFileName(url, disposition, mimeType)
        val request = DownloadManager.Request(Uri.parse(url))
            .setMimeType(mimeType)
            .addRequestHeader("User-Agent", userAgent)
            .setTitle(name)
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
        CookieManager.getInstance().getCookie(url)?.let { request.addRequestHeader("Cookie", it) }
        // Before Android 10, Downloads needs the storage permission: the download manager's own
        // folder instead (the file still opens from its notification and the Downloads app).
        if (Build.VERSION.SDK_INT >= 29) request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        getSystemService(DownloadManager::class.java).enqueue(request)
        Toast.makeText(this, "Downloading $name", Toast.LENGTH_SHORT).show()
    }

    // Kanso sends filename*=UTF-8''… (RFC 6266), which URLUtil.guessFileName doesn't read.
    private fun fileName(disposition: String?): String? {
        val encoded = disposition?.let { Regex("filename\\*=UTF-8''([^;]+)", RegexOption.IGNORE_CASE).find(it) } ?: return null
        return Uri.decode(encoded.groupValues[1].trim()).replace('/', '_').ifEmpty { null }
    }

    // Also called with the launch intent once the bridge is up.
    override fun onNewIntent(intent: Intent) {
        mailtoAsView(intent)
        super.onNewIntent(intent)
    }

    // Capacitor's App plugin hands the web app VIEW intents only (appUrlOpen, src/app/native.ts),
    // but apps send mailto: as SENDTO too, often with the fields in extras: they go into the
    // link (RFC 6068), which the web app reads.
    private fun mailtoAsView(intent: Intent) {
        val link = intent.data ?: return
        if (link.scheme != "mailto") return
        val extras = buildList {
            intent.getStringArrayExtra(Intent.EXTRA_EMAIL)?.forEach { add("to" to it) }
            intent.getStringArrayExtra(Intent.EXTRA_CC)?.forEach { add("cc" to it) }
            intent.getStringArrayExtra(Intent.EXTRA_BCC)?.forEach { add("bcc" to it) }
            intent.getStringExtra(Intent.EXTRA_SUBJECT)?.let { add("subject" to it) }
            intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.let { add("body" to it.toString()) }
        }
        if (extras.isNotEmpty()) {
            val text = link.toString()
            val query = extras.joinToString("&") { (name, value) -> "$name=${Uri.encode(value)}" }
            intent.data = Uri.parse(text + (if ('?' in text) "&" else "?") + query)
        }
        intent.action = Intent.ACTION_VIEW
    }
}
