import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_image_compress/flutter_image_compress.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:uuid/uuid.dart';

import '../../models/models.dart';
import '../config/env.dart';
import 'location_service.dart';
import 'offline_queue.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.code, this.status});

  final String message;
  final String? code;
  final int? status;

  @override
  String toString() => message;
}

const _uuid = Uuid();
const _feedCacheKey = 'feed_cache';
const _feedCacheTimeKey = 'feed_cache_at';
const _contactsCacheKey = 'contacts_cache';
const _deviceIdKey = 'device_id';
const _maxCached = 200;

class Repository {
  Repository(this.queue, this.prefs) {
    _dio = Dio(
      BaseOptions(
        baseUrl: Env.apiBaseUrl,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 20),
      ),
    );
    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = _db.auth.currentSession?.accessToken;
          if (token != null) options.headers['Authorization'] = 'Bearer $token';
          handler.next(options);
        },
      ),
    );
  }

  final OfflineQueue queue;
  final SharedPreferences prefs;
  late final Dio _dio;
  final location = LocationService();

  SupabaseClient get _db => Supabase.instance.client;

  String? get deviceId => prefs.getString(_deviceIdKey);

  Future<T> _call<T>(Future<Response<T>> Function() request) async {
    try {
      return (await request()).data as T;
    } on DioException catch (error) {
      final data = error.response?.data;
      if (data is Map && data['message'] is String) {
        throw ApiException(
          data['message'] as String,
          code: data['error'] as String?,
          status: error.response?.statusCode,
        );
      }
      throw const ApiException('Could not reach Dey Alert. Check your connection.');
    }
  }

  // Auth
  /// Sign-in methods the server allows. Email always works; admins switch phone on.
  Future<List<String>> signInMethods() async {
    try {
      final config = await _call<Map<String, dynamic>>(() => _dio.get('/v1/config'));
      final methods = (config['sign_in_methods'] as List?)?.cast<String>() ?? const [];
      return methods.contains('email') ? methods : ['email', ...methods];
    } on ApiException {
      return const ['email'];
    }
  }

  /// Gives up after this long so a slow network never leaves the button spinning.
  static const authTimeout = Duration(seconds: 30);

  Future<void> sendCode(SignInTarget target) => (target.isPhone
          ? _db.auth.signInWithOtp(phone: target.value)
          : _db.auth.signInWithOtp(email: target.value))
      .timeout(authTimeout);

  Future<void> verifyCode(SignInTarget target, String code) => (target.isPhone
          ? _db.auth.verifyOTP(phone: target.value, token: code, type: OtpType.sms)
          : _db.auth.verifyOTP(email: target.value, token: code, type: OtpType.email))
      .timeout(authTimeout);

  bool get signedIn => _db.auth.currentSession != null;

  Future<void> signOut() => _db.auth.signOut();

  // Profile
  Future<UserProfile> profile() async => UserProfile.fromJson(
    await _call<Map<String, dynamic>>(() => _dio.get('/v1/me')),
  );

  Future<UserProfile> updateProfile(Map<String, dynamic> values) async =>
      UserProfile.fromJson(
        await _call<Map<String, dynamic>>(() => _dio.patch('/v1/me', data: values)),
      );

  Future<void> deleteAccount() async {
    await _call<dynamic>(() => _dio.delete('/v1/me'));
    await _db.auth.signOut();
  }

  Future<Map<String, dynamic>> exportData() =>
      _call<Map<String, dynamic>>(() => _dio.get('/v1/me/export'));

  Future<List<Map<String, dynamic>>> myReports() async =>
      (await _call<List<dynamic>>(() => _dio.get('/v1/me/reports')))
          .cast<Map<String, dynamic>>();

  Future<String> registerDevice({String? fcmToken, LatLngPoint? point}) async {
    final fallback = prefs.getString('device_token') ?? 'no-fcm-${_uuid.v4()}';
    await prefs.setString('device_token', fcmToken ?? fallback);
    final data = await _call<Map<String, dynamic>>(
      () => _dio.post(
        '/v1/devices',
        data: {
          'fcm_token': fcmToken ?? fallback,
          'platform': Platform.isIOS ? 'ios' : 'android',
          'app_version': '1.0.0',
          if (point != null) 'lat': point.lat,
          if (point != null) 'lng': point.lng,
        },
      ),
    );
    final id = data['device_id'] as String;
    await prefs.setString(_deviceIdKey, id);
    return id;
  }

  // Feed
  Future<FeedResult> feed(double lat, double lng, FeedFilter filter) async {
    try {
      final rows = await _db.rpc(
        'nearby_incidents',
        params: {
          'p_lat': lat,
          'p_lng': lng,
          'p_radius_m': (filter.radiusKm * 1000).round(),
          'p_since': DateTime.now()
              .subtract(filter.window)
              .toUtc()
              .toIso8601String(),
          'p_include_history': filter.includesHistory,
        },
      );
      final incidents = (rows as List)
          .cast<Map<String, dynamic>>()
          .map(Incident.fromJson)
          .toList();
      await prefs.setString(
        _feedCacheKey,
        jsonEncode(incidents.take(_maxCached).map((i) => i.toJson()).toList()),
      );
      await prefs.setInt(
        _feedCacheTimeKey,
        DateTime.now().millisecondsSinceEpoch,
      );
      return FeedResult(incidents.where(filter.matches).toList());
    } on Exception {
      return _cachedFeed(filter);
    }
  }

  FeedResult _cachedFeed(FeedFilter filter) {
    final raw = prefs.getString(_feedCacheKey);
    final savedAt = prefs.getInt(_feedCacheTimeKey);
    if (raw == null || savedAt == null) {
      throw const ApiException('You are offline and nothing is saved yet.');
    }
    final incidents = (jsonDecode(raw) as List)
        .cast<Map<String, dynamic>>()
        .map(Incident.fromJson)
        .where((incident) => incident.expiresAt.isAfter(DateTime.now()))
        .where(filter.matches)
        .toList();
    return FeedResult(
      incidents,
      cachedAt: DateTime.fromMillisecondsSinceEpoch(savedAt),
    );
  }

  Future<Incident?> incident(String id) async {
    final row = await _db
        .from('incidents_public')
        .select()
        .eq('id', id)
        .maybeSingle();
    return row == null ? null : Incident.fromJson(row);
  }

  // Verifier and staff updates on an incident
  Future<List<IncidentUpdate>> incidentUpdates(String id) async => [
    for (final row in await _call<List<dynamic>>(() => _dio.get('/v1/incidents/$id/updates')))
      IncidentUpdate.fromJson(row as Map<String, dynamic>),
  ];

  Future<void> postIncidentUpdate(String id, String body) =>
      _call<dynamic>(() => _dio.post('/v1/incidents/$id/updates', data: {'body': body}));

  // Watch zones
  Future<List<WatchZone>> zones() async => [
    for (final row in await _call<List<dynamic>>(() => _dio.get('/v1/me/zones')))
      WatchZone.fromJson(row as Map<String, dynamic>),
  ];

  Future<void> addZone(Map<String, dynamic> zone) =>
      _call<dynamic>(() => _dio.post('/v1/me/zones', data: zone));

  Future<void> updateZone(int id, Map<String, dynamic> zone) =>
      _call<dynamic>(() => _dio.put('/v1/me/zones/$id', data: zone));

  Future<void> deleteZone(int id) => _call<dynamic>(() => _dio.delete('/v1/me/zones/$id'));

  // Announcements from staff, for everyone or this area
  Future<List<Announcement>> announcements(LatLngPoint? at) async => [
    for (final row in await _call<List<dynamic>>(
      () => _dio.get(
        '/v1/announcements',
        queryParameters: at == null ? null : {'lat': at.lat, 'lng': at.lng},
      ),
    ))
      Announcement.fromJson(row as Map<String, dynamic>),
  ];

  // Support
  Future<List<SupportTicket>> tickets() async => [
    for (final row in await _call<List<dynamic>>(() => _dio.get('/v1/support')))
      SupportTicket.fromJson(row as Map<String, dynamic>),
  ];

  Future<void> openTicket(String category, String message, String? appInfo) => _call<dynamic>(
    () => _dio.post(
      '/v1/support',
      data: {'category': category, 'message': message, 'app_info': appInfo},
    ),
  );

  Future<void> replyTicket(int id, String message) =>
      _call<dynamic>(() => _dio.post('/v1/support/$id/reply', data: {'message': message}));

  // Verifier applications
  Future<VerifierApplicationInfo> verifierApplication() async => VerifierApplicationInfo.fromJson(
    await _call<Map<String, dynamic>>(() => _dio.get('/v1/verifier-application')),
  );

  Future<List<Map<String, dynamic>>> wards(int lgaId) async {
    final rows = await _db.from('wards').select('id, name').eq('lga_id', lgaId).order('name');
    return List<Map<String, dynamic>>.from(rows);
  }

  Future<void> applyToVerify(Map<String, dynamic> application) =>
      _call<dynamic>(() => _dio.post('/v1/verifier-application', data: application));

  // Sanctions, appeals and data correction
  Future<List<Sanction>> sanctions() async => [
    for (final row in await _call<List<dynamic>>(() => _dio.get('/v1/me/sanctions')))
      Sanction.fromJson(row as Map<String, dynamic>),
  ];

  Future<void> appeal(int sanctionId, String message) => _call<dynamic>(
    () => _dio.post('/v1/me/sanctions/$sanctionId/appeal', data: {'message': message}),
  );

  Future<void> requestCorrection(String details) =>
      _call<dynamic>(() => _dio.post('/v1/me/privacy-request', data: {'details': details}));

  Future<List<TimelineEvent>> timeline(String id) async {
    final rows = await _db
        .from('incident_events_public')
        .select('event_type, created_at')
        .eq('incident_id', id)
        .order('created_at', ascending: false);
    return rows
        .map(
          (row) => TimelineEvent(
            row['event_type'] as String,
            DateTime.parse(row['created_at'] as String),
          ),
        )
        .toList();
  }

  Future<List<IncidentMedia>> media(String id) async {
    final rows = await _db
        .from('incident_media_public')
        .select('kind, storage_path, thumb_path, moderation_status')
        .eq('incident_id', id);
    return rows
        .map(
          (row) => IncidentMedia(
            kind: row['kind'] as String,
            path: row['storage_path'] as String,
            thumbPath: row['thumb_path'] as String?,
            blurred: row['moderation_status'] == 'blurred',
          ),
        )
        .toList();
  }

  String mediaUrl(String path) =>
      _db.storage.from('incident-media').getPublicUrl(path);

  // Report
  Future<bool> submitReport({
    required String typeCode,
    required double lat,
    required double lng,
    required bool anonymous,
    String? description,
    String? locationName,
    String? mediaPath,
    String? mediaKind,
  }) async {
    final clientId = _uuid.v4();
    final item = QueueItem(
      clientId: clientId,
      kind: 'incident',
      path: '/v1/incidents',
      mediaPath: mediaPath,
      mediaKind: mediaKind,
      body: {
        'client_id': clientId,
        'type_code': typeCode,
        'lat': lat,
        'lng': lng,
        'occurred_at': DateTime.now().toUtc().toIso8601String(),
        'description': description,
        'location_name': locationName,
        'is_anonymous': anonymous,
        'device_id': deviceId,
      },
    );
    await queue.enqueue(item);
    await flushQueue();
    return (await queue.store.all()).every((q) => q.clientId != clientId);
  }

  Future<int> flushQueue() => queue.flush(_send);

  Future<SendOutcome> _send(QueueItem item) async {
    try {
      final response = await _dio.post<Map<String, dynamic>>(
        item.path,
        data: item.body,
      );
      if (item.mediaPath != null) {
        await _uploadMedia(
          response.data!['id'] as String,
          item.mediaPath!,
          item.mediaKind ?? 'image',
        );
      }
      return SendOutcome.sent;
    } on DioException catch (error) {
      return outcomeForStatus(error.response?.statusCode);
    }
  }

  Future<void> _uploadMedia(String incidentId, String path, String kind) async {
    final user = _db.auth.currentUser!;
    var file = File(path);
    if (kind == 'image') {
      final compressed = await FlutterImageCompress.compressAndGetFile(
        path,
        '$path.compressed.jpg',
        minWidth: 1280,
        minHeight: 1280,
        quality: 70,
      );
      if (compressed != null) file = File(compressed.path);
    }
    final extension = kind == 'image' ? 'jpg' : 'mp4';
    final storagePath = '${user.id}/${_uuid.v4()}.$extension';
    await _db.storage.from('quarantine').upload(storagePath, file);
    await _dio.post<dynamic>(
      '/v1/incidents/$incidentId/media',
      data: {'kind': kind, 'storage_path': storagePath},
    );
  }

  Future<Map<String, dynamic>> corroborate(String id, LatLngPoint point) =>
      _call<Map<String, dynamic>>(
        () => _dio.post(
          '/v1/incidents/$id/corroborate',
          data: {'lat': point.lat, 'lng': point.lng, 'device_id': deviceId},
        ),
      );

  Future<void> flag(
    String id,
    String reason,
    String? notes,
    LatLngPoint? point,
  ) => _call<dynamic>(
    () => _dio.post(
      '/v1/incidents/$id/flag',
      data: {
        'reason': reason,
        'notes': notes,
        if (point != null) 'lat': point.lat,
        if (point != null) 'lng': point.lng,
      },
    ),
  );

  Future<void> verify(
    String id,
    String action, {
    String? note,
    int? extendMinutes,
  }) => _call<dynamic>(
    () => _dio.patch(
      '/v1/incidents/$id/verify',
      data: {
        'action': action,
        'note': note,
        'extend_minutes': ?extendMinutes,
      },
    ),
  );

  // Notifications
  Future<List<Map<String, dynamic>>> notifications() => _db
      .from('notifications')
      .select()
      .order('created_at', ascending: false)
      .limit(100);

  Future<void> markRead(String id) => _db
      .from('notifications')
      .update({'read_at': DateTime.now().toUtc().toIso8601String()})
      .eq('id', id);

  // Trusted contacts
  List<TrustedContact> cachedContacts() {
    final raw = prefs.getString(_contactsCacheKey);
    if (raw == null) return const [];
    return (jsonDecode(raw) as List)
        .cast<Map<String, dynamic>>()
        .map(TrustedContact.fromJson)
        .toList();
  }

  Future<List<TrustedContact>> contacts() async {
    try {
      final rows = await _db.from('trusted_contacts').select().order('created_at');
      final contacts = rows.map(TrustedContact.fromJson).toList();
      await prefs.setString(
        _contactsCacheKey,
        jsonEncode(contacts.map((c) => c.toJson()).toList()),
      );
      return contacts;
    } on Exception {
      return cachedContacts();
    }
  }

  Future<void> addContact(String name, String phone, String? relationship) =>
      _call<dynamic>(
        () => _dio.post(
          '/v1/trusted-contacts',
          data: {'name': name, 'phone': phone, 'relationship': relationship},
        ),
      );

  Future<void> updateContact(String id, String name, String? relationship) =>
      _call<dynamic>(
        () => _dio.patch(
          '/v1/trusted-contacts/$id',
          data: {'name': name, 'relationship': relationship},
        ),
      );

  Future<void> deleteContact(String id) =>
      _call<dynamic>(() => _dio.delete('/v1/trusted-contacts/$id'));

  // SOS
  Future<String?> startSos(LatLngPoint point) async {
    try {
      final data = await _call<Map<String, dynamic>>(
        () => _dio.post(
          '/v1/sos',
          data: {'client_id': _uuid.v4(), 'lat': point.lat, 'lng': point.lng},
        ),
      );
      return data['id'] as String;
    } on ApiException {
      return null;
    }
  }

  Future<void> pingSos(String id, LatLngPoint point) => _call<dynamic>(
    () => _dio.post(
      '/v1/sos/$id/ping',
      data: {'lat': point.lat, 'lng': point.lng},
    ),
  );

  Future<void> endSos(String id, String action) =>
      _call<dynamic>(() => _dio.post('/v1/sos/$id/$action'));

  Future<Map<String, dynamic>> startCheckin(int minutes, LatLngPoint point) =>
      _call<Map<String, dynamic>>(
        () => _dio.post(
          '/v1/sos/checkins',
          data: {
            'client_id': _uuid.v4(),
            'minutes': minutes,
            'lat': point.lat,
            'lng': point.lng,
          },
        ),
      );
}
