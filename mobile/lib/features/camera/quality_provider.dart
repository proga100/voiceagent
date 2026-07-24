/// Turns the camera preview's [CameraImage] stream into a live quality "gate"
/// for the guided camera.
///
/// Per analysed frame it runs blur + exposure (pure, off the UI isolate via a
/// long-lived worker isolate). The gate is **green when the frame is in focus
/// (sharp) and well-exposed** — that is the only guidance the farmer needs to
/// aim. It is purely visual (the green/red border + Uzbek hint); capture is
/// manual (a shutter button) and nothing here triggers a photo or any voice.
///
/// Deliberately silent: it does NOT emit `photo.quality` events, so the agent
/// never coaches by voice while the farmer is lining up a shot — the border is
/// the whole guidance.
///
/// The ML Kit "is a plant in frame?" check is disabled ([plantCheckEnabled] =
/// false): it kept a perfectly sharp close-up leaf red because the generic
/// labeler didn't call it a "plant", and the real plant/no-plant decision now
/// lives in the backend diagnosis (`is_plant`). The code path is retained,
/// dormant, behind the flag.
library;

import 'dart:async';
import 'dart:io' show Platform;
import 'dart:ui' show Size;

import 'package:camera/camera.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_mlkit_image_labeling/google_mlkit_image_labeling.dart';

import '../session/voice_session_controller.dart';
import 'analysis_worker.dart';
import 'quality/frame_quality.dart';

/// Master switch for the ML Kit plant check. OFF: the border reflects focus +
/// exposure only, and plant-vs-not is decided by the backend at diagnosis.
const bool plantCheckEnabled = false;

/// Minimum gap between frame analyses when the agent is silent.
const int _analysisGapIdleMs = 300;

/// Minimum gap between frame analyses while the agent is speaking — the
/// per-frame downsample+Laplacian is cheap, but backing off during playback
/// leaves more headroom for audio delivery on budget phones.
const int _analysisGapSpeakingMs = 700;

const int _minPlantGapMs = 1000;
const double _plantConfidence = 0.6;

const Set<String> _plantLabels = {
  'Plant',
  'Flower',
  'Leaf',
  'Tree',
  'Houseplant',
  'Grass',
};

/// Immutable snapshot of the combined quality gate for the UI.
class GateState {
  const GateState({
    this.green = false,
    this.reason,
    this.plantCheckDisabled = false,
  });

  /// True iff sharp && exposure ok && (plant ok || plant check disabled).
  final bool green;

  /// `null` when green, else `blur` | `too_dark` | `too_bright` | `no_plant`.
  final String? reason;

  /// True once the ML plant check was disabled after a conversion failure.
  final bool plantCheckDisabled;
}

/// Analyses preview frames and exposes the [GateState].
class CameraQualityNotifier extends Notifier<GateState> {
  /// Long-lived analysis isolate (spawned lazily on the first frame, killed in
  /// dispose). `null` until first use / after dispose; a spawn/run failure makes
  /// [_runAnalysis] fall back to `compute` transparently.
  AnalysisWorker? _worker;

  ImageLabeler? _labeler;

  bool _isAndroid = true;
  InputImageRotation _rotation = InputImageRotation.rotation0deg;

  bool _analyzing = false;
  int _lastAnalysisMs = 0;
  int _lastPlantMs = -1 << 30;
  bool _plantOk = false;
  bool _plantDisabled = false;
  bool _disposed = false;

  @override
  GateState build() {
    ref.onDispose(() {
      _disposed = true;
      _labeler?.close();
      _worker?.dispose();
      _worker = null;
    });
    return const GateState();
  }

  /// Resets all per-session state for a fresh camera open.
  void configure({required String targetPart, required int sensorOrientation}) {
    // targetPart kept in the signature for the caller; the border no longer
    // reports it (no photo.quality). Referenced to keep intent explicit:
    assert(targetPart.isNotEmpty || true);
    _isAndroid = Platform.isAndroid;
    _rotation = _rotationFromSensor(sensorOrientation);
    _analyzing = false;
    _lastAnalysisMs = 0;
    _lastPlantMs = -1 << 30;
    _plantOk = false;
    _plantDisabled = false;
    state = const GateState();
  }

  /// Feeds one preview frame; heavy work is throttled and off-loaded. The
  /// throttle gap widens while the agent is speaking so audio delivery keeps
  /// priority on budget phones (a cheap `isPlaybackActive` read per frame).
  void onFrame(CameraImage image) {
    if (_disposed || _analyzing) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    final speaking = ref.read(voiceSessionProvider.notifier).isPlaybackActive;
    final gap = speaking ? _analysisGapSpeakingMs : _analysisGapIdleMs;
    if (now - _lastAnalysisMs < gap) return;
    _analyzing = true;
    _lastAnalysisMs = now;
    unawaited(_process(image, now));
  }

  /// Runs [analyzeFrame] on the long-lived worker isolate, transparently
  /// falling back to a one-shot `compute` if the worker is unavailable.
  Future<QualityResult> _runAnalysis(FrameAnalysisRequest req) async {
    try {
      return await (_worker ??= AnalysisWorker()).analyze(req);
    } catch (_) {
      return compute(analyzeFrame, req);
    }
  }

  Future<void> _process(CameraImage image, int now) async {
    try {
      final q = await _runAnalysis(_requestFor(image));
      if (_disposed) return;

      String? reason;
      if (!q.sharp) {
        reason = 'blur';
      } else if (q.exposure == ExposureVerdict.tooDark) {
        reason = 'too_dark';
      } else if (q.exposure == ExposureVerdict.tooBright) {
        reason = 'too_bright';
      }

      if (reason == null && plantCheckEnabled && !_plantDisabled) {
        // Keep ML Kit (NV21 interleave + inference) out of the speech window:
        // on budget SoCs it stalls the UI isolate and starves audio delivery.
        // While the agent talks, the last plant verdict simply persists.
        final speaking =
            ref.read(voiceSessionProvider.notifier).isPlaybackActive;
        if (!speaking && now - _lastPlantMs >= _minPlantGapMs) {
          _lastPlantMs = now;
          _plantOk = await _checkPlant(image);
          if (_disposed) return;
        }
        if (!_plantOk && !_plantDisabled) reason = 'no_plant';
      }

      _emit(now, reason == null, reason);
    } catch (_) {
      // Analysis failed for this frame — leave the gate state unchanged.
    } finally {
      _analyzing = false;
    }
  }

  /// Updates the advisory gate state only. No `photo.quality` is sent — the
  /// agent stays silent while the farmer aims; the border is the only guidance.
  void _emit(int now, bool green, String? reason) {
    if (_disposed) return;
    state = GateState(
      green: green,
      reason: reason,
      plantCheckDisabled: _plantDisabled,
    );
  }

  FrameAnalysisRequest _requestFor(CameraImage image) {
    final plane = image.planes[0];
    return FrameAnalysisRequest(
      plane: plane.bytes,
      width: image.width,
      height: image.height,
      rowStride: plane.bytesPerRow,
      pixelStride: plane.bytesPerPixel ?? 1,
    );
  }

  /// Runs ML Kit image labeling. Returns true (PASS) and disables further
  /// attempts if the conversion or the labeler throws.
  Future<bool> _checkPlant(CameraImage image) async {
    try {
      final input = _toInputImage(image);
      if (input == null) {
        _plantDisabled = true;
        return true;
      }
      final labels = await (_labeler ??= ImageLabeler(
        options: ImageLabelerOptions(confidenceThreshold: 0.5),
      )).processImage(input);
      for (final label in labels) {
        if (_plantLabels.contains(label.label) &&
            label.confidence >= _plantConfidence) {
          return true;
        }
      }
      return false;
    } catch (_) {
      _plantDisabled = true;
      return true;
    }
  }

  InputImage? _toInputImage(CameraImage image) {
    if (_isAndroid) {
      final nv21 = _yuv420ToNv21(image);
      if (nv21 == null) return null;
      return InputImage.fromBytes(
        bytes: nv21,
        metadata: InputImageMetadata(
          size: Size(image.width.toDouble(), image.height.toDouble()),
          rotation: _rotation,
          format: InputImageFormat.nv21,
          bytesPerRow: image.width,
        ),
      );
    }
    final plane = image.planes[0];
    return InputImage.fromBytes(
      bytes: plane.bytes,
      metadata: InputImageMetadata(
        size: Size(image.width.toDouble(), image.height.toDouble()),
        rotation: _rotation,
        format: InputImageFormat.bgra8888,
        bytesPerRow: plane.bytesPerRow,
      ),
    );
  }

  /// Interleaves a YUV_420_888 [CameraImage] into a contiguous NV21 buffer
  /// (full-res Y, then V/U interleaved), honouring each plane's row/pixel
  /// stride. Returns null when the image is not the expected 3-plane layout.
  Uint8List? _yuv420ToNv21(CameraImage image) {
    if (image.planes.length < 3) return null;
    final width = image.width;
    final height = image.height;
    final yPlane = image.planes[0];
    final uPlane = image.planes[1];
    final vPlane = image.planes[2];
    final ySize = width * height;
    final nv21 = Uint8List(ySize + ySize ~/ 2);

    final yRowStride = yPlane.bytesPerRow;
    final yPixelStride = yPlane.bytesPerPixel ?? 1;
    var o = 0;
    for (var y = 0; y < height; y++) {
      final row = y * yRowStride;
      for (var x = 0; x < width; x++) {
        nv21[o++] = yPlane.bytes[row + x * yPixelStride];
      }
    }

    final uRowStride = uPlane.bytesPerRow;
    final uPixelStride = uPlane.bytesPerPixel ?? 2;
    final vRowStride = vPlane.bytesPerRow;
    final vPixelStride = vPlane.bytesPerPixel ?? 2;
    final cw = width ~/ 2;
    final ch = height ~/ 2;
    for (var y = 0; y < ch; y++) {
      final uRow = y * uRowStride;
      final vRow = y * vRowStride;
      for (var x = 0; x < cw; x++) {
        nv21[o++] = vPlane.bytes[vRow + x * vPixelStride];
        nv21[o++] = uPlane.bytes[uRow + x * uPixelStride];
      }
    }
    return nv21;
  }

  InputImageRotation _rotationFromSensor(int sensorOrientation) {
    switch (sensorOrientation) {
      case 90:
        return InputImageRotation.rotation90deg;
      case 180:
        return InputImageRotation.rotation180deg;
      case 270:
        return InputImageRotation.rotation270deg;
      default:
        return InputImageRotation.rotation0deg;
    }
  }
}

/// Provider for the guided-camera quality gate.
final cameraQualityProvider =
    NotifierProvider<CameraQualityNotifier, GateState>(
      CameraQualityNotifier.new,
    );
