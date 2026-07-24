/// Pure, unit-testable frame-quality analysis for the guided camera.
///
/// No Flutter imports so it can run in a plain isolate (via `compute`) and be
/// exercised from unit tests with synthetic luma grids. Two concerns:
///
///  * Sharpness — variance of a 3x3 Laplacian over the luma grid. Low variance
///    means a blurry / featureless frame.
///  * Exposure  — a 256-bin luma histogram; too many very-dark or very-bright
///    pixels means the frame is under/over exposed.
///
/// The camera plugin hands us a full-resolution luma plane with a row stride and
/// pixel stride; [downsampleLuma] strides it down to a ~320px-wide grid so the
/// analysis stays cheap. [analyzeFrame] chains the two for a single `compute`
/// call.
library;

import 'dart:typed_data';

/// Minimum Laplacian variance for a frame to count as sharp.
const double blurVarMin = 800.0;

/// Luma below this counts as "very dark" for the exposure histogram.
const int _darkCutoff = 16;

/// Luma above this counts as "very bright" for the exposure histogram.
const int _brightCutoff = 239;

/// Fraction of pixels beyond a cutoff that trips the exposure verdict.
const double _exposureFrac = 0.40;

/// Verdict of the exposure check.
enum ExposureVerdict { ok, tooDark, tooBright }

/// Immutable result of analysing one luma grid.
class QualityResult {
  const QualityResult({
    required this.sharp,
    required this.blurVariance,
    required this.exposure,
    required this.darkFrac,
    required this.brightFrac,
  });

  /// True when [blurVariance] `>= blurVarMin`.
  final bool sharp;

  /// Variance of the Laplacian response over the grid.
  final double blurVariance;

  /// Exposure verdict from the luma histogram.
  final ExposureVerdict exposure;

  /// Fraction of pixels `< _darkCutoff`.
  final double darkFrac;

  /// Fraction of pixels `> _brightCutoff`.
  final double brightFrac;
}

/// A downsampled luma grid: [bytes] is `width * height` single-byte samples.
class LumaGrid {
  const LumaGrid(this.bytes, this.width, this.height);
  final Uint8List bytes;
  final int width;
  final int height;
}

/// `compute()`-friendly payload: a raw luma/BGRA plane plus its geometry.
///
/// On Android this is the YUV_420_888 Y plane (`pixelStride == 1`); on iOS the
/// BGRA plane (`pixelStride == 4`, so striding by it lands on the B channel — a
/// usable luma approximation for the sharpness/exposure gate).
class FrameAnalysisRequest {
  const FrameAnalysisRequest({
    required this.plane,
    required this.width,
    required this.height,
    required this.rowStride,
    required this.pixelStride,
    this.targetWidth = 320,
  });

  final Uint8List plane;
  final int width;
  final int height;
  final int rowStride;
  final int pixelStride;
  final int targetWidth;
}

/// Top-level entry point for `compute`: downsample then analyse.
QualityResult analyzeFrame(FrameAnalysisRequest req) {
  final grid = downsampleLuma(
    req.plane,
    req.width,
    req.height,
    req.rowStride,
    req.pixelStride,
    targetWidth: req.targetWidth,
  );
  return analyzeLuma(grid.bytes, grid.width, grid.height);
}

/// Strides a full-resolution plane down to a `~targetWidth`-wide luma grid.
///
/// Pure: samples one byte per output cell at `y*stride*rowStride +
/// x*stride*pixelStride`. `stride` is chosen so the output is close to
/// [targetWidth] wide; when the source is already narrow it is left untouched.
LumaGrid downsampleLuma(
  Uint8List bytes,
  int width,
  int height,
  int rowStride,
  int pixelStride, {
  int targetWidth = 320,
}) {
  if (width <= 0 || height <= 0) return LumaGrid(Uint8List(0), 0, 0);
  final stride = width <= targetWidth ? 1 : (width / targetWidth).ceil();
  final outW = width ~/ stride;
  final outH = height ~/ stride;
  if (outW <= 0 || outH <= 0) return LumaGrid(Uint8List(0), 0, 0);
  final out = Uint8List(outW * outH);
  var i = 0;
  for (var y = 0; y < outH; y++) {
    final rowBase = y * stride * rowStride;
    for (var x = 0; x < outW; x++) {
      out[i++] = bytes[rowBase + x * stride * pixelStride];
    }
  }
  return LumaGrid(out, outW, outH);
}

/// Runs the sharpness + exposure checks on a downsampled luma grid.
QualityResult analyzeLuma(Uint8List luma, int width, int height) {
  final blur = _laplacianVariance(luma, width, height);
  final hist = _histogram(luma);
  final n = luma.isEmpty ? 1 : luma.length;

  var dark = 0;
  for (var v = 0; v < _darkCutoff; v++) {
    dark += hist[v];
  }
  var bright = 0;
  for (var v = _brightCutoff + 1; v < 256; v++) {
    bright += hist[v];
  }
  final darkFrac = dark / n;
  final brightFrac = bright / n;

  final ExposureVerdict exposure;
  if (darkFrac > _exposureFrac) {
    exposure = ExposureVerdict.tooDark;
  } else if (brightFrac > _exposureFrac) {
    exposure = ExposureVerdict.tooBright;
  } else {
    exposure = ExposureVerdict.ok;
  }

  return QualityResult(
    sharp: blur >= blurVarMin,
    blurVariance: blur,
    exposure: exposure,
    darkFrac: darkFrac,
    brightFrac: brightFrac,
  );
}

/// Variance of a 3x3 Laplacian over the interior of the grid.
double _laplacianVariance(Uint8List g, int w, int h) {
  if (w < 3 || h < 3) return 0;
  var sum = 0.0;
  var sumSq = 0.0;
  var n = 0;
  for (var y = 1; y < h - 1; y++) {
    final row = y * w;
    for (var x = 1; x < w - 1; x++) {
      final c = g[row + x];
      final lap =
          4 * c - g[row + x - 1] - g[row + x + 1] - g[row - w + x] - g[row + w + x];
      final v = lap.toDouble();
      sum += v;
      sumSq += v * v;
      n++;
    }
  }
  if (n == 0) return 0;
  final mean = sum / n;
  final variance = sumSq / n - mean * mean;
  return variance < 0 ? 0 : variance;
}

/// 256-bin luma histogram.
List<int> _histogram(Uint8List g) {
  final hist = List<int>.filled(256, 0);
  for (final v in g) {
    hist[v]++;
  }
  return hist;
}
