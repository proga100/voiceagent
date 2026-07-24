import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/features/camera/quality/frame_quality.dart';

/// Builds a `w*h` luma grid where `(x+y).isEven` picks [a], else [b].
Uint8List _checker(int w, int h, int a, int b) {
  final g = Uint8List(w * h);
  for (var y = 0; y < h; y++) {
    for (var x = 0; x < w; x++) {
      g[y * w + x] = ((x + y).isEven) ? a : b;
    }
  }
  return g;
}

Uint8List _flat(int w, int h, int v) => Uint8List(w * h)..fillRange(0, w * h, v);

void main() {
  group('analyzeLuma — sharpness (Laplacian variance)', () {
    test('flat grey grid is NOT sharp (blur fail)', () {
      final r = analyzeLuma(_flat(20, 20, 128), 20, 20);
      expect(r.blurVariance, 0);
      expect(r.sharp, isFalse);
    });

    test('mid-grey checkerboard is sharp (high variance, exposure ok)', () {
      final r = analyzeLuma(_checker(20, 20, 100, 160), 20, 20);
      expect(r.blurVariance, greaterThan(blurVarMin));
      expect(r.sharp, isTrue);
      expect(r.exposure, ExposureVerdict.ok);
    });

    test('Laplacian variance grows monotonically with contrast', () {
      final flat = analyzeLuma(_flat(20, 20, 128), 20, 20).blurVariance;
      final low = analyzeLuma(_checker(20, 20, 120, 136), 20, 20).blurVariance;
      final high = analyzeLuma(_checker(20, 20, 100, 160), 20, 20).blurVariance;
      expect(flat, 0);
      expect(low, greaterThan(flat));
      expect(high, greaterThan(low));
    });
  });

  group('analyzeLuma — exposure histogram', () {
    test('mostly-black grid is tooDark', () {
      final r = analyzeLuma(_flat(20, 20, 5), 20, 20);
      expect(r.exposure, ExposureVerdict.tooDark);
      expect(r.darkFrac, greaterThan(0.4));
    });

    test('mostly-white grid is tooBright', () {
      final r = analyzeLuma(_flat(20, 20, 250), 20, 20);
      expect(r.exposure, ExposureVerdict.tooBright);
      expect(r.brightFrac, greaterThan(0.4));
    });

    test('well-lit mid grid is ok', () {
      final r = analyzeLuma(_flat(20, 20, 128), 20, 20);
      expect(r.exposure, ExposureVerdict.ok);
      expect(r.darkFrac, 0);
      expect(r.brightFrac, 0);
    });

    test('cutoffs: 16 is not dark, 15 is; 239 is not bright, 240 is', () {
      expect(analyzeLuma(_flat(10, 10, 16), 10, 10).darkFrac, 0);
      expect(analyzeLuma(_flat(10, 10, 15), 10, 10).darkFrac, 1);
      expect(analyzeLuma(_flat(10, 10, 239), 10, 10).brightFrac, 0);
      expect(analyzeLuma(_flat(10, 10, 240), 10, 10).brightFrac, 1);
    });
  });

  group('downsampleLuma — stride correctness', () {
    test('tightly-packed plane, stride 2 (targetWidth 4 of width 8)', () {
      // 8x4, rowStride 8, pixelStride 1, bytes[i] == i.
      final bytes = Uint8List.fromList(List<int>.generate(8 * 4, (i) => i));
      final grid = downsampleLuma(bytes, 8, 4, 8, 1, targetWidth: 4);
      expect(grid.width, 4);
      expect(grid.height, 2);
      // stride = ceil(8/4) = 2 -> samples at 16*y + 2*x.
      expect(grid.bytes, [0, 2, 4, 6, 16, 18, 20, 22]);
    });

    test('honours pixelStride (BGRA-style plane, take every 4th byte)', () {
      // 8x2, pixelStride 4, rowStride 32, bytes[i] == i, no downscale.
      final bytes = Uint8List.fromList(List<int>.generate(8 * 2 * 4, (i) => i));
      final grid = downsampleLuma(bytes, 8, 2, 32, 4, targetWidth: 8);
      expect(grid.width, 8);
      expect(grid.height, 2);
      expect(grid.bytes, [0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60]);
    });

    test('honours rowStride padding (skips per-row padding bytes)', () {
      // width 4, rowStride 6 (2 padding bytes/row), height 3, no downscale.
      final bytes = Uint8List.fromList(List<int>.generate(6 * 3, (i) => i));
      final grid = downsampleLuma(bytes, 4, 3, 6, 1, targetWidth: 4);
      expect(grid.width, 4);
      expect(grid.height, 3);
      expect(grid.bytes, [0, 1, 2, 3, 6, 7, 8, 9, 12, 13, 14, 15]);
    });
  });

  group('analyzeFrame — end to end via request', () {
    test('downsamples then analyses a dark BGRA-ish plane', () {
      // 16x8 plane, pixelStride 4, rowStride 64, all luma bytes = 5 (dark).
      final bytes = Uint8List(16 * 8 * 4);
      for (var i = 0; i < bytes.length; i += 4) {
        bytes[i] = 5;
      }
      final r = analyzeFrame(
        FrameAnalysisRequest(
          plane: bytes,
          width: 16,
          height: 8,
          rowStride: 64,
          pixelStride: 4,
        ),
      );
      expect(r.exposure, ExposureVerdict.tooDark);
      expect(r.sharp, isFalse);
    });
  });
}
