import 'package:flutter/material.dart';

class IncidentType {
  const IncidentType(this.code, this.label, this.icon);

  final String code;
  final String label;
  final IconData icon;
}

const incidentTypes = <IncidentType>[
  IncidentType('kidnapping', 'Kidnapping', Icons.person_off_outlined),
  IncidentType('armed_robbery', 'Armed robbery', Icons.gpp_maybe_outlined),
  IncidentType('banditry', 'Banditry', Icons.groups_3_outlined),
  IncidentType('cult_clash', 'Cult clash', Icons.sports_kabaddi_outlined),
  IncidentType('gunshots', 'Gunshots', Icons.campaign_outlined),
  IncidentType('mob_action', 'Mob action', Icons.diversity_3_outlined),
  IncidentType('roadblock', 'Roadblock', Icons.block_outlined),
  IncidentType(
    'fire_outbreak',
    'Fire',
    Icons.local_fire_department_outlined,
  ),
  IncidentType('flooding', 'Flooding', Icons.water_outlined),
  IncidentType('accident', 'Accident', Icons.car_crash_outlined),
  IncidentType(
    'suspicious_activity',
    'Suspicious activity',
    Icons.visibility_outlined,
  ),
  IncidentType('other', 'Other', Icons.more_horiz),
];

IncidentType incidentType(String code) => incidentTypes.firstWhere(
  (type) => type.code == code,
  orElse: () => incidentTypes.last,
);

class Incident {
  const Incident({
    required this.id,
    required this.typeCode,
    required this.lat,
    required this.lng,
    required this.status,
    required this.severity,
    required this.corroborationCount,
    required this.occurredAt,
    required this.expiresAt,
    this.description,
    this.locationName,
    this.confirmedByLabel,
    this.wardId,
    this.lgaId,
  });

  factory Incident.fromJson(Map<String, dynamic> json) => Incident(
    id: json['id'] as String,
    typeCode: json['type_code'] as String,
    lat: (json['lat'] as num).toDouble(),
    lng: (json['lng'] as num).toDouble(),
    status: json['status'] as String,
    severity: json['severity'] as String,
    corroborationCount: json['corroboration_count'] as int? ?? 0,
    occurredAt: DateTime.parse(json['occurred_at'] as String),
    expiresAt: DateTime.parse(json['expires_at'] as String),
    description: json['description'] as String?,
    locationName: json['location_name'] as String?,
    confirmedByLabel: json['confirmed_by_label'] as String?,
    wardId: json['ward_id'] as int?,
    lgaId: json['lga_id'] as int?,
  );

  final String id;
  final String typeCode;
  final double lat;
  final double lng;
  final String status;
  final String severity;
  final int corroborationCount;
  final DateTime occurredAt;
  final DateTime expiresAt;
  final String? description;
  final String? locationName;
  final String? confirmedByLabel;
  final int? wardId;
  final int? lgaId;

  Map<String, dynamic> toJson() => {
    'id': id,
    'type_code': typeCode,
    'lat': lat,
    'lng': lng,
    'status': status,
    'severity': severity,
    'corroboration_count': corroborationCount,
    'occurred_at': occurredAt.toIso8601String(),
    'expires_at': expiresAt.toIso8601String(),
    'description': description,
    'location_name': locationName,
    'confirmed_by_label': confirmedByLabel,
    'ward_id': wardId,
    'lga_id': lgaId,
  };

  String get title => incidentType(typeCode).label;
  bool get isLive => status == 'unconfirmed' ||
      status == 'corroborated' ||
      status == 'confirmed';
}

class TimelineEvent {
  const TimelineEvent(this.type, this.at);

  final String type;
  final DateTime at;
}

class IncidentMedia {
  const IncidentMedia({
    required this.kind,
    required this.path,
    required this.blurred,
    this.thumbPath,
  });

  final String kind;
  final String path;
  final String? thumbPath;
  final bool blurred;
}

class UserProfile {
  const UserProfile({
    required this.role,
    required this.language,
    required this.alertRadiusKm,
    required this.smsAlertsOptIn,
    this.phone,
    this.email,
    this.displayName,
    this.homeLat,
    this.homeLng,
    this.homeWardId,
    this.homeLgaId,
    this.quietStart,
    this.quietEnd,
  });

  factory UserProfile.fromJson(Map<String, dynamic> json) => UserProfile(
    phone: json['phone'] as String?,
    email: json['email'] as String?,
    role: json['role'] as String,
    language: json['language'] as String,
    alertRadiusKm: (json['alert_radius_km'] as num).toDouble(),
    smsAlertsOptIn: json['sms_alerts_opt_in'] as bool? ?? false,
    displayName: json['display_name'] as String?,
    homeLat: (json['home_lat'] as num?)?.toDouble(),
    homeLng: (json['home_lng'] as num?)?.toDouble(),
    homeWardId: json['home_ward_id'] as int?,
    homeLgaId: json['home_lga_id'] as int?,
    quietStart: json['quiet_hours_start'] as String?,
    quietEnd: json['quiet_hours_end'] as String?,
  );

  final String? phone;
  final String? email;
  final String role;
  final String language;
  final double alertRadiusKm;
  final bool smsAlertsOptIn;
  final String? displayName;
  final double? homeLat;
  final double? homeLng;
  final int? homeWardId;
  final int? homeLgaId;
  final String? quietStart;
  final String? quietEnd;

  bool get hasHome => homeLat != null && homeLng != null;
  bool get canVerify =>
      role == 'verifier' || role == 'moderator' || role == 'admin';
}

class TrustedContact {
  const TrustedContact({
    required this.id,
    required this.name,
    required this.phone,
    required this.consentStatus,
    this.relationship,
  });

  factory TrustedContact.fromJson(Map<String, dynamic> json) => TrustedContact(
    id: json['id'] as String,
    name: json['name'] as String,
    phone: json['phone'] as String,
    consentStatus: json['consent_status'] as String,
    relationship: json['relationship'] as String?,
  );

  final String id;
  final String name;
  final String phone;
  final String consentStatus;
  final String? relationship;

  bool get accepted => consentStatus == 'accepted';

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'phone': phone,
    'consent_status': consentStatus,
    'relationship': relationship,
  };
}

class FeedFilter {
  const FeedFilter({
    this.types = const {},
    this.statuses = const {},
    this.radiusKm = 5,
    this.window = const Duration(hours: 24),
  });

  final Set<String> types;
  final Set<String> statuses;
  final double radiusKm;
  final Duration window;

  bool get includesHistory => window > const Duration(hours: 24);

  bool matches(Incident incident) =>
      (types.isEmpty || types.contains(incident.typeCode)) &&
      (statuses.isEmpty || statuses.contains(incident.status));

  FeedFilter copyWith({
    Set<String>? types,
    Set<String>? statuses,
    double? radiusKm,
    Duration? window,
  }) => FeedFilter(
    types: types ?? this.types,
    statuses: statuses ?? this.statuses,
    radiusKm: radiusKm ?? this.radiusKm,
    window: window ?? this.window,
  );
}

class FeedResult {
  const FeedResult(this.incidents, {this.cachedAt});

  final List<Incident> incidents;
  final DateTime? cachedAt;

  bool get isCached => cachedAt != null;
}

/// Where a sign-in code goes: an email address, or a phone number when admins allow it.
class SignInTarget {
  const SignInTarget.email(this.value) : isPhone = false;
  const SignInTarget.phone(this.value) : isPhone = true;

  final String value;
  final bool isPhone;
}
