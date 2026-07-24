import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/identity/device_identity.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(resetDeviceIdCacheForTest);

  test('mints a UUID once and persists it', () async {
    SharedPreferences.setMockInitialValues({});
    final id = await getOrCreateDeviceId();
    // UUID v4 shape — matches the backend's ^[A-Za-z0-9-]{8,64}$ gate.
    expect(RegExp(r'^[A-Za-z0-9-]{8,64}$').hasMatch(id), true);
    expect(id.length, 36);

    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('device_id'), id);
  });

  test('stable across calls', () async {
    SharedPreferences.setMockInitialValues({});
    final a = await getOrCreateDeviceId();
    final b = await getOrCreateDeviceId();
    expect(a, b);
  });

  test('reuses an already-stored id', () async {
    SharedPreferences.setMockInitialValues({'device_id': 'existing-id-1234'});
    expect(await getOrCreateDeviceId(), 'existing-id-1234');
  });
}
