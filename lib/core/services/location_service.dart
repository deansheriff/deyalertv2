import 'dart:async';

import 'package:geolocator/geolocator.dart';

class LatLngPoint {
  const LatLngPoint(this.lat, this.lng);

  final double lat;
  final double lng;
}

class LocationService {
  Future<bool> hasPermission() async {
    final permission = await Geolocator.checkPermission();
    return permission == LocationPermission.always ||
        permission == LocationPermission.whileInUse;
  }

  Future<LocationPermission> requestPermission() =>
      Geolocator.requestPermission();

  Future<LatLngPoint?> current({
    Duration timeout = const Duration(seconds: 8),
  }) async {
    if (!await hasPermission()) return null;
    try {
      final position = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(accuracy: LocationAccuracy.high),
      ).timeout(timeout);
      return LatLngPoint(position.latitude, position.longitude);
    } on Exception {
      final last = await Geolocator.getLastKnownPosition();
      return last == null ? null : LatLngPoint(last.latitude, last.longitude);
    }
  }

  /// Keeps reporting while the screen is off, via an Android foreground service.
  Stream<LatLngPoint> sharingStream() => Geolocator.getPositionStream(
    locationSettings: AndroidSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 25,
      intervalDuration: const Duration(seconds: 30),
      foregroundNotificationConfig: const ForegroundNotificationConfig(
        notificationTitle: 'Dey Alert is sharing your location',
        notificationText: 'Open the app to stop sharing.',
        enableWakeLock: true,
      ),
    ),
  ).map((position) => LatLngPoint(position.latitude, position.longitude));
}
