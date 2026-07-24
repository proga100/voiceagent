import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/audio/lipsync_analyzer.dart';

/// Synthesises `samples` of a mono PCM16 LE sine at [freq] Hz (24 kHz).
Uint8List sinePcm(double freq, int samples, {double amp = 0.6}) {
  final out = Uint8List(samples * 2);
  for (var i = 0; i < samples; i++) {
    final s = (amp * math.sin(2 * math.pi * freq * i / 24000) * 32767).round();
    final u = s & 0xFFFF; // two's-complement 16-bit, little-endian
    out[i * 2] = u & 0xFF;
    out[i * 2 + 1] = (u >> 8) & 0xFF;
  }
  return out;
}

Uint8List silencePcm(int samples) => Uint8List(samples * 2);

/// Feeds `windows` worth of a signal and returns the last analysed frame.
LipsyncFrame lastFrameOf(LipsyncAnalyzer a, Uint8List pcm) {
  a.ingest(pcm);
  // Drain everything; frameAt at a huge time returns the newest frame.
  final f = a.frameAt(1e12);
  expect(f, isNotNull, reason: 'expected at least one analysed window');
  return f!;
}

void main() {
  group('LipsyncAnalyzer — loudness / openness', () {
    test('a tone opens the mouth (open > 0)', () {
      final a = LipsyncAnalyzer();
      // 10 windows so the attack smoother ramps up.
      final f = lastFrameOf(a, sinePcm(200, 480 * 10));
      expect(f.open, greaterThan(0.2));
    });

    test('silence keeps the mouth shut (open ≈ 0)', () {
      final a = LipsyncAnalyzer();
      final f = lastFrameOf(a, silencePcm(480 * 10));
      expect(f.open, lessThan(0.02));
      expect(f.low + f.mid + f.high, lessThan(0.02)); // no spectral energy
    });
  });

  group('LipsyncAnalyzer — spectral band shares', () {
    test('a 200 Hz tone skews the low band', () {
      final a = LipsyncAnalyzer();
      final f = lastFrameOf(a, sinePcm(200, 480 * 10));
      expect(f.low, greaterThan(f.mid));
      expect(f.low, greaterThan(f.high));
    });

    test('a 3 kHz tone skews the high band', () {
      final a = LipsyncAnalyzer();
      final f = lastFrameOf(a, sinePcm(3000, 480 * 10));
      expect(f.high, greaterThan(f.mid));
      expect(f.high, greaterThan(f.low));
    });

    test('shares are a normalised distribution (sum ≈ 1 under a tone)', () {
      final a = LipsyncAnalyzer();
      final f = lastFrameOf(a, sinePcm(1000, 480 * 10));
      expect(f.low + f.mid + f.high, closeTo(1.0, 1e-6));
    });
  });

  group('LipsyncAnalyzer — frameAt time alignment', () {
    test('frames are 20 ms apart and picked by playback time', () {
      final a = LipsyncAnalyzer();
      a.ingest(sinePcm(500, 480 * 5)); // 5 windows → 20,40,60,80,100 ms

      // Nothing has reached t=0 yet (first frame is at 20 ms).
      expect(a.frameAt(0), isNull);

      // At 50 ms the newest frame ≤ 50 is the 40 ms one; older is dropped.
      final at50 = a.frameAt(50);
      expect(at50, isNotNull);
      expect(at50!.mediaTimeMs, closeTo(40.0, 1e-6));

      // Jumping past the end returns the last frame (100 ms), draining the FIFO.
      final atEnd = a.frameAt(1000);
      expect(atEnd, isNotNull);
      expect(atEnd!.mediaTimeMs, closeTo(100.0, 1e-6));

      // FIFO now empty.
      expect(a.frameAt(1000), isNull);
    });

    test('frameAt updates the mouthOpen notifier to the returned frame', () {
      final a = LipsyncAnalyzer();
      a.ingest(sinePcm(400, 480 * 6));
      final f = a.frameAt(1e12);
      expect(f, isNotNull);
      expect(a.mouthOpen.value, f!.open);
    });
  });

  group('LipsyncAnalyzer — FIFO capacity (jitter lead headroom)', () {
    test('a long turn streamed ahead of playback keeps its earliest frames', () {
      final a = LipsyncAnalyzer();
      // 1200 windows = 24 s of speech ingested ahead of playback — more than the
      // old 1024-frame cap. With the jitter prebuffer lead the FIFO must retain
      // them all, so the very first (20 ms) frame is still present, not dropped.
      a.ingest(sinePcm(300, 480 * 1200));
      final first = a.frameAt(20.0);
      expect(first, isNotNull);
      expect(first!.mediaTimeMs, closeTo(20.0, 1e-6));
    });
  });

  group('LipsyncAnalyzer — reset / carry', () {
    test('reset clears the FIFO and snaps mouthOpen to 0', () {
      final a = LipsyncAnalyzer();
      a.ingest(sinePcm(600, 480 * 4));
      a.frameAt(1e12); // pushes a non-zero openness into the notifier
      expect(a.mouthOpen.value, greaterThan(0));

      a.reset();
      expect(a.mouthOpen.value, 0);
      expect(a.frameAt(1e12), isNull); // FIFO emptied
    });

    test('odd-byte splits across chunks reassemble into whole samples', () {
      final full = sinePcm(300, 480 * 3); // 3 full windows
      // Split at an odd byte boundary so a sample straddles the chunk edge.
      final a1 = full.sublist(0, 481); // odd length → carries 1 byte
      final a2 = full.sublist(481);

      final split = LipsyncAnalyzer();
      split.ingest(a1);
      split.ingest(a2);

      final whole = LipsyncAnalyzer();
      whole.ingest(full);

      // Identical framing/analysis regardless of how bytes were chunked.
      final fSplit = split.frameAt(1e12);
      final fWhole = whole.frameAt(1e12);
      expect(fSplit, isNotNull);
      expect(fWhole, isNotNull);
      expect(fSplit!.mediaTimeMs, fWhole!.mediaTimeMs);
      expect(fSplit.open, closeTo(fWhole.open, 1e-9));
      expect(fSplit.low, closeTo(fWhole.low, 1e-9));
      expect(fSplit.mid, closeTo(fWhole.mid, 1e-9));
      expect(fSplit.high, closeTo(fWhole.high, 1e-9));
    });
  });
}
