/// Best-effort farmer GPS for weather enrichment.
///
/// Returns `(lat, lon)` or null — null on ANY obstacle (permission denied,
/// location services off, timeout, plugin error). The backend falls back to
/// Tashkent when it receives no coordinates, so this never blocks a call.
library;

import 'package:geolocator/geolocator.dart';

class LocationService {
  const LocationService();

  Future<({double lat, double lon})?> currentLatLon() async {
    try {
      if (!await Geolocator.isLocationServiceEnabled()) return null;

      var perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        perm = await Geolocator.requestPermission();
      }
      if (perm == LocationPermission.denied ||
          perm == LocationPermission.deniedForever) {
        return null;
      }

      final pos = await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.low, // town-level is plenty for weather
          timeLimit: Duration(seconds: 6),
        ),
      );
      return (lat: pos.latitude, lon: pos.longitude);
    } catch (_) {
      return null; // fail-open: no GPS → server uses the Tashkent default
    }
  }
}
