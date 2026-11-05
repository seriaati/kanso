package app.kanso

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

// FCM's data messages from Kanso (backend sync/notify.py). Runs on a background thread, even
// with the app closed; the notification is built in Notifications.
class MessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        // mail, summary and clear all change the account's unread inbox: its widgets redraw.
        Widget.refresh(this, message.data["account_id"]?.toIntOrNull())
        // Off in Settings: Kanso may not have dropped the token yet.
        if (Notifications.token(this) == null) return
        Notifications.show(this, message.data, message.sentTime)
    }

    // Firebase replaced the app's token: Kanso gets the new one (the old one is dropped when FCM
    // reports it gone). Nothing to do while notifications are off. If Kanso can't be reached now,
    // KansoPlugin tries again when the app next starts.
    // Deprecated since firebase-messaging 25.1 for FID registration (register() and
    // onRegistered), which is new and barely documented yet; tokens still work. See PLAN.md.
    @Suppress("OVERRIDE_DEPRECATION")
    override fun onNewToken(token: String) {
        Notifications.renew(this, token)
    }
}
