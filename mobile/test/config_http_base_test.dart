/// The REST base is derived from the WebSocket URL, and that derivation has to
/// survive the Phase 4 merge: the voice agent now runs mounted under `/voice`
/// inside growz-ai, so anything that strips the whole path aims `/chats` and
/// `/crops` at growz-ai's own root, where they are 404 — the app would connect
/// its socket fine and then fail to create a chat, so the interview never
/// starts. These cases pin both deployment shapes.
import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/config.dart';

void main() {
  group('httpBaseFromWs', () {
    test('keeps a mount prefix (growz: agent lives under /voice)', () {
      expect(
        httpBaseFromWs('wss://test-ai.growz.io/voice/ws/voice'),
        'https://test-ai.growz.io/voice',
      );
    });

    test('root-mounted backend keeps no path (flance)', () {
      expect(
        httpBaseFromWs('wss://voi.flance.info/ws/voice'),
        'https://voi.flance.info',
      );
    });

    test('ws:// maps to http:// and the port is preserved (emulator default)',
        () {
      expect(
        httpBaseFromWs('ws://10.0.2.2:8012/ws/voice'),
        'http://10.0.2.2:8012',
      );
    });

    test('a deeper prefix is kept whole', () {
      expect(
        httpBaseFromWs('wss://example.org/a/b/ws/voice'),
        'https://example.org/a/b',
      );
    });

    test('a URL without the /ws/voice suffix is left alone', () {
      expect(
        httpBaseFromWs('wss://example.org/voice'),
        'https://example.org/voice',
      );
    });
  });
}
