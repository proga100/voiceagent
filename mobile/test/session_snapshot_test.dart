import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/features/session/voice_session_controller.dart';

void main() {
  group('SessionSnapshot.pttHeld', () {
    test('defaults to false', () {
      expect(const SessionSnapshot().pttHeld, false);
    });

    test('copyWith sets and preserves it', () {
      const snap = SessionSnapshot();
      final held = snap.copyWith(pttHeld: true);
      expect(held.pttHeld, true);
      // Untouched by unrelated copies.
      expect(held.copyWith(micLevel: 0.5).pttHeld, true);
      expect(held.copyWith(pttHeld: false).pttHeld, false);
    });
  });
}
