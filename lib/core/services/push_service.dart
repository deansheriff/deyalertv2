import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';

final _notifications = FlutterLocalNotificationsPlugin();

const _channels = {
  'normal': AndroidNotificationChannel(
    'alerts_normal',
    'Nearby alerts',
    importance: Importance.defaultImportance,
  ),
  'high': AndroidNotificationChannel(
    'alerts_high',
    'High severity alerts',
    importance: Importance.high,
  ),
  'critical': AndroidNotificationChannel(
    'alerts_critical',
    'Confirmed critical alerts',
    importance: Importance.max,
    enableVibration: true,
  ),
};

Future<void> _show(Map<String, dynamic> data) async {
  final channel = _channels[data['priority']] ?? _channels['normal']!;
  await _notifications.show(
    // Broadcasts carry their own id so one doesn't replace another.
    id: (data['incident_id'] as String? ?? data['broadcast_id'] as String? ?? '').hashCode,
    title: data['title'] as String?,
    body: data['body'] as String?,
    notificationDetails: NotificationDetails(
      android: AndroidNotificationDetails(
        channel.id,
        channel.name,
        importance: channel.importance,
        priority: Priority.high,
        ongoing: data['priority'] == 'critical',
        autoCancel: data['priority'] != 'critical',
      ),
    ),
    payload: data['incident_id'] as String?,
  );
}

@pragma('vm:entry-point')
Future<void> firebaseBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  await _initLocal(null);
  await _show(message.data);
}

Future<void> _initLocal(void Function(String incidentId)? onOpen) async {
  await _notifications.initialize(
    settings: const InitializationSettings(
      android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      iOS: DarwinInitializationSettings(requestAlertPermission: false),
    ),
    onDidReceiveNotificationResponse: (response) {
      if (response.payload != null) onOpen?.call(response.payload!);
    },
  );
  final android = _notifications
      .resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin
      >();
  for (final channel in _channels.values) {
    await android?.createNotificationChannel(channel);
  }
}

class PushService {
  bool _ready = false;

  bool get isReady => _ready;

  /// Returns false when Firebase is not configured for this build, so the rest
  /// of the app keeps working without push.
  Future<bool> init(void Function(String incidentId) onOpen) async {
    try {
      await Firebase.initializeApp();
      await _initLocal(onOpen);
      FirebaseMessaging.onBackgroundMessage(firebaseBackgroundHandler);
      FirebaseMessaging.onMessage.listen((m) => unawaited(_show(m.data)));
      FirebaseMessaging.onMessageOpenedApp.listen((m) {
        final id = m.data['incident_id'] as String?;
        if (id != null) onOpen(id);
      });
      _ready = true;
    } on Exception catch (error) {
      debugPrint('Push unavailable: $error');
    }
    return _ready;
  }

  Future<bool> requestPermission() async {
    if (!_ready) return false;
    final settings = await FirebaseMessaging.instance.requestPermission();
    return settings.authorizationStatus == AuthorizationStatus.authorized;
  }

  Future<String?> token() async =>
      // ignore: deprecated_member_use
      _ready ? FirebaseMessaging.instance.getToken() : null;
}
