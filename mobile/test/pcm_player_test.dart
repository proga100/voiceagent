import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/audio/pcm_player.dart';

Uint8List bytes(List<int> xs) => Uint8List.fromList(xs);

void main() {
  group('PcmByteCarry — odd-byte carry across chunk boundaries', () {
    test('even chunk passes through unchanged, no carry', () {
      final c = PcmByteCarry();
      final out = c.absorb(bytes([1, 2, 3, 4]));
      expect(out, [1, 2, 3, 4]);
      expect(c.hasCarry, isFalse);
    });

    test('odd chunk holds the last byte, plays the rest', () {
      final c = PcmByteCarry();
      final out = c.absorb(bytes([1, 2, 3]));
      expect(out, [1, 2]);
      expect(c.hasCarry, isTrue);
    });

    test('held byte is prepended to the next chunk', () {
      final c = PcmByteCarry();
      expect(c.absorb(bytes([1, 2, 3])), [1, 2]); // carries 3
      final out = c.absorb(
        bytes([4, 5]),
      ); // -> [3,4,5] odd -> play [3,4], carry 5
      expect(out, [3, 4]);
      expect(c.hasCarry, isTrue);
      final out2 = c.absorb(bytes([6])); // -> [5,6] even -> play both
      expect(out2, [5, 6]);
      expect(c.hasCarry, isFalse);
    });

    test('a lone byte with no prior carry yields empty output', () {
      final c = PcmByteCarry();
      final out = c.absorb(bytes([9]));
      expect(out, isEmpty);
      expect(c.hasCarry, isTrue);
    });

    test('full split stream reassembles to the original even byte stream', () {
      final c = PcmByteCarry();
      // Original PCM: 12 bytes (6 samples). Delivered in awkward odd splits.
      final original = List<int>.generate(12, (i) => i + 1);
      final chunks = [
        [1, 2, 3], // 3
        [4], // 1
        [5, 6, 7, 8, 9], // 5
        [10, 11], // 2
        [12], // 1
      ];
      final played = <int>[];
      for (final ch in chunks) {
        played.addAll(c.absorb(bytes(ch)));
      }
      // Every byte except a possible trailing carry has been played, in order.
      final expectedPlayed = original.length.isEven
          ? original
          : original.sublist(0, original.length - 1);
      expect(played, expectedPlayed);
      expect(c.hasCarry, original.length.isOdd);
    });

    test('reset() drops the held byte', () {
      final c = PcmByteCarry();
      c.absorb(bytes([1]));
      expect(c.hasCarry, isTrue);
      c.reset();
      expect(c.hasCarry, isFalse);
      // After reset the next even chunk is untouched (no stale prepend).
      expect(c.absorb(bytes([2, 3])), [2, 3]);
    });
  });
}
