import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/audio/jitter_buffer.dart';

/// 24 kHz PCM16 mono ⇒ 48 bytes per millisecond.
Uint8List pcmMs(int ms) => Uint8List(ms * 48);

void main() {
  group('JitterBuffer — prebuffer threshold path', () {
    test('withholds until prebufferMs, then releases the whole cushion', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);

      // Starts idle; first feed begins buffering.
      expect(jb.state, JitterState.idle);
      jb.feed(pcmMs(250)); // 250 ms < 500 ms
      expect(jb.state, JitterState.buffering);
      expect(jb.drain(), isNull);

      jb.feed(pcmMs(250)); // now 500 ms total → threshold met
      final out = jb.drain();
      expect(out, isNotNull);
      expect(out!.length, pcmMs(500).length); // one concatenated 500 ms buffer
      expect(jb.state, JitterState.playing);
      // Cushion emptied on release.
      expect(jb.bufferedMs, 0);
    });
  });

  group('JitterBuffer — maxWait path', () {
    test('a slow trickle starts at maxWaitMs even below prebufferMs', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);

      jb.feed(pcmMs(100)); // only 100 ms buffered
      expect(jb.drain(), isNull); // waited 0, buffered < 500

      clock = 699;
      expect(jb.drain(), isNull); // still just under maxWait

      clock = 700; // maxWait reached
      final out = jb.drain();
      expect(out, isNotNull);
      expect(out!.length, pcmMs(100).length);
      expect(jb.state, JitterState.playing);
    });

    test('maxWait counts from the first buffered chunk, not construction', () {
      var clock = 1000;
      final jb = JitterBuffer(nowMs: () => clock);
      jb.feed(pcmMs(50)); // firstChunkAt = 1000
      clock = 1699;
      expect(jb.drain(), isNull);
      clock = 1700;
      expect(jb.drain(), isNotNull);
    });
  });

  group('JitterBuffer — passthrough while playing', () {
    test('once playing, feeds pass straight through', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);
      jb.feed(pcmMs(500));
      expect(jb.drain(), isNotNull); // → playing
      expect(jb.isPlaying, isTrue);

      jb.feed(pcmMs(20));
      final a = jb.drain();
      expect(a, isNotNull);
      expect(a!.length, pcmMs(20).length);

      jb.feed(pcmMs(40));
      final b = jb.drain();
      expect(b!.length, pcmMs(40).length);

      // Nothing new fed → nothing to drain.
      expect(jb.drain(), isNull);
    });
  });

  group('JitterBuffer — underrun → rebuffer → resume', () {
    test('underrun re-cushions with the smaller rebufferMs threshold', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);
      jb.feed(pcmMs(500));
      jb.drain(); // playing
      expect(jb.isPlaying, isTrue);

      jb.playbackUnderrun();
      expect(jb.state, JitterState.buffering);

      jb.feed(pcmMs(125)); // 125 ms < rebuffer 250 ms
      expect(jb.drain(), isNull);

      jb.feed(pcmMs(125)); // now 250 ms → rebuffer threshold met
      final out = jb.drain();
      expect(out, isNotNull);
      expect(out!.length, pcmMs(250).length);
      expect(jb.isPlaying, isTrue);
    });

    test('playbackUnderrun is a no-op unless playing', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);
      jb.playbackUnderrun(); // idle
      expect(jb.state, JitterState.idle);
      jb.feed(pcmMs(100));
      jb.playbackUnderrun(); // buffering
      expect(jb.state, JitterState.buffering);
    });
  });

  group('JitterBuffer — reset', () {
    test('reset drops everything and re-earns the full prebuffer', () {
      var clock = 0;
      final jb = JitterBuffer(nowMs: () => clock);
      jb.feed(pcmMs(500));
      jb.drain(); // playing
      jb.feed(pcmMs(300)); // pending pass-through not drained yet

      jb.reset();
      expect(jb.state, JitterState.idle);
      expect(jb.bufferedMs, 0);
      expect(jb.drain(), isNull);

      // A fresh turn must again accumulate the full 500 ms (not rebuffer 250).
      jb.feed(pcmMs(300));
      expect(jb.drain(), isNull); // 300 < 500
      jb.feed(pcmMs(200));
      expect(jb.drain(), isNotNull); // 500 reached
    });
  });

  group('JitterBuffer — edge cases', () {
    test('empty feeds are ignored', () {
      final jb = JitterBuffer(nowMs: () => 0);
      jb.feed(Uint8List(0));
      expect(jb.state, JitterState.idle);
      expect(jb.drain(), isNull);
    });
  });
}
