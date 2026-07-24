/// Stable per-install device identity for Alomat's per-farmer memory.
///
/// A UUID is minted on first use and persisted in SharedPreferences; it rides
/// `session.start` as `user_id` so the backend can load/save this farmer's
/// profile. It survives app restarts but NOT a reinstall — long-term identity
/// is recovered server-side via the phone number Alomat asks for during
/// onboarding.
library;

import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

const String _kDeviceIdKey = 'device_id';

String? _cached;

/// Returns this install's stable device id, minting and persisting one on
/// first call. Safe to call repeatedly (memoized).
Future<String> getOrCreateDeviceId() async {
  final cached = _cached;
  if (cached != null) return cached;
  final prefs = await SharedPreferences.getInstance();
  var id = prefs.getString(_kDeviceIdKey);
  if (id == null || id.isEmpty) {
    id = const Uuid().v4();
    await prefs.setString(_kDeviceIdKey, id);
  }
  _cached = id;
  return id;
}

/// Test hook: clears the in-memory memoized id (storage is untouched).
void resetDeviceIdCacheForTest() => _cached = null;
