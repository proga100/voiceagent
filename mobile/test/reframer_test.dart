import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/audio/mic_streamer.dart';

Uint8List seq(int start, int len) =>
    Uint8List.fromList(List<int>.generate(len, (i) => (start + i) & 0xff));

void main() {
  group('Reframer → exact 3200-byte frames', () {
    test('emits nothing until a full frame is buffered', () {
      final r = Reframer();
      expect(r.addChunk(seq(0, 100)), isEmpty);
      expect(r.pending, 100);
      expect(r.addChunk(seq(100, 3099)), isEmpty); // total 3199
      expect(r.pending, 3199);
      final frames = r.addChunk(seq(0, 1)); // total 3200
      expect(frames.length, 1);
      expect(frames.single.length, 3200);
      expect(r.pending, 0);
    });

    test('splits a large chunk into multiple exact frames + remainder', () {
      final r = Reframer();
      // 3200 * 2 + 50 bytes in one odd-sized chunk.
      final frames = r.addChunk(seq(0, 3200 * 2 + 50));
      expect(frames.length, 2);
      expect(frames.every((f) => f.length == 3200), isTrue);
      expect(r.pending, 50);
    });

    test('odd-sized chunks reassemble into contiguous 3200-byte frames', () {
      final r = Reframer();
      final produced = <int>[];
      var next = 0;
      // Feed 1000 odd/awkward chunks; verify byte-for-byte continuity.
      final sizes = [7, 13, 101, 999, 3201, 5, 4096, 1, 2, 6400];
      var total = 0;
      for (final s in sizes) {
        final chunk = Uint8List.fromList(
          List<int>.generate(s, (i) => (next + i) & 0xff),
        );
        next += s;
        total += s;
        for (final frame in r.addChunk(chunk)) {
          expect(frame.length, 3200);
          produced.addAll(frame);
        }
      }
      final expectedFrames = total ~/ 3200;
      expect(produced.length, expectedFrames * 3200);
      expect(r.pending, total - expectedFrames * 3200);
      // Continuity: produced bytes are exactly the first N of the input stream.
      for (var i = 0; i < produced.length; i++) {
        expect(produced[i], i & 0xff);
      }
    });

    test('reset() clears the remainder', () {
      final r = Reframer();
      r.addChunk(seq(0, 100));
      expect(r.pending, 100);
      r.reset();
      expect(r.pending, 0);
    });

    test('honours a custom frame size', () {
      final r = Reframer(frameSize: 4);
      final frames = r.addChunk(seq(0, 10));
      expect(frames.length, 2);
      expect(frames.every((f) => f.length == 4), isTrue);
      expect(r.pending, 2);
    });
  });
}
