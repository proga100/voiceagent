/// The guided camera: a full-screen preview with a live quality gate that
/// advises the farmer while they frame the shot, plus a manual shutter button.
///
/// Rendered *inside* the interview [Stack] (under the PiP avatar), so the voice
/// session keeps running. The camera opens with `enableAudio: false` so the
/// plugin never grabs the microphone the voice pipeline is using.
///
/// The border is green when the gate passes and red otherwise, with a short
/// Uzbek hint — purely advisory guidance for aiming; capture happens only when
/// the farmer taps the shutter. A gallery button offers a fallback picker (same
/// quality checks), and the X simply returns to the
/// interview.
library;

import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import 'package:permission_handler/permission_handler.dart';

import '../interview/transcript_provider.dart';
import '../session/app_mode.dart';
import 'quality/frame_quality.dart';
import 'quality_provider.dart';

/// How long the preview runs alone before the quality pipeline
/// ([CameraController.startImageStream]) kicks in. Giving the camera bring-up
/// the CPU to itself for a beat keeps the analysis (blur/exposure/ML Kit) from
/// stalling the UI isolate and scrambling the streaming agent audio on budget
/// SoCs (Helio G85).
const int analysisStartDelayMs = 800;

/// Guided camera for one pending `request_photo` call.
class CameraScreen extends ConsumerStatefulWidget {
  const CameraScreen({
    super.key,
    required this.callId,
    required this.targetPart,
    required this.reason,
  });

  final String callId;
  final String targetPart;
  final String reason;

  @override
  ConsumerState<CameraScreen> createState() => _CameraScreenState();
}

class _CameraScreenState extends ConsumerState<CameraScreen> {
  CameraController? _controller;
  bool _ready = false;

  /// True once the delayed quality pipeline is actually running. Until then the
  /// border stays neutral and the hint reads "Tayyorlanmoqda…".
  bool _streaming = false;
  bool _capturing = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _bootstrap());
  }

  Future<void> _bootstrap() async {
    final status = await Permission.camera.request();
    if (!mounted) return;
    if (!status.isGranted) {
      _abort('Camera permission denied');
      return;
    }

    try {
      final cameras = await availableCameras();
      if (cameras.isEmpty) {
        _abort('No camera found');
        return;
      }
      final back = cameras.firstWhere(
        (c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );
      final controller = CameraController(
        back,
        ResolutionPreset.high,
        enableAudio: false,
        imageFormatGroup: Platform.isAndroid
            ? ImageFormatGroup.yuv420
            : ImageFormatGroup.bgra8888,
      );
      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }
      _controller = controller;

      final notifier = ref.read(cameraQualityProvider.notifier);
      notifier.configure(
        targetPart: widget.targetPart,
        sensorOrientation: back.sensorOrientation,
      );

      // Show the preview immediately, but hold the analysis stream back so the
      // camera bring-up owns the CPU alone for ~800ms — otherwise the quality
      // pipeline starts mid-bring-up and audibly scrambles the agent audio.
      setState(() => _ready = true);
      await Future<void>.delayed(
        const Duration(milliseconds: analysisStartDelayMs),
      );
      if (!mounted) return;
      final active = _controller;
      if (active == null) return;
      await active.startImageStream(notifier.onFrame);
      if (!mounted) return;
      setState(() => _streaming = true);
    } catch (_) {
      _abort("Couldn't open camera");
    }
  }

  /// Emits a system bubble and returns to the interview. `camera.cancelled`
  /// was removed from the protocol (2026-08-05): request_photo is acked
  /// immediately server-side, so nothing waits on the camera — closing it is
  /// purely a local UI action.
  void _abort(String message) {
    ref.read(transcriptProvider.notifier).addSystem(message);
    ref.read(appModeProvider.notifier).toInterview();
  }

  void _cancel() {
    ref.read(appModeProvider.notifier).toInterview();
  }

  /// Manual shutter: takes the picture and moves to Confirm. Always available
  /// regardless of the quality gate (full manual control); guarded against
  /// double-taps and taps before the preview stream is live.
  Future<void> _capture() async {
    final controller = _controller;
    if (_capturing ||
        !_ready ||
        !_streaming ||
        !mounted ||
        controller == null) {
      return;
    }
    _capturing = true;
    try {
      await controller.stopImageStream();
      final file = await controller.takePicture();
      if (!mounted) return;
      ref
          .read(appModeProvider.notifier)
          .toConfirm(
            path: file.path,
            targetPart: widget.targetPart,
            origin: 'camera',
          );
    } catch (_) {
      _capturing = false;
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Failed to take photo')),
        );
      }
    }
  }

  Future<void> _pickFromGallery() async {
    final XFile? picked;
    try {
      picked = await ImagePicker().pickImage(
        source: ImageSource.gallery,
        maxWidth: 1536,
        maxHeight: 1536,
        imageQuality: 90,
      );
    } catch (_) {
      return;
    }
    if (picked == null || !mounted) return;

    final result = await _analyzeFile(picked.path);
    if (!mounted) return;

    void proceed() => ref
        .read(appModeProvider.notifier)
        .toConfirm(
          path: picked!.path,
          targetPart: widget.targetPart,
          origin: 'gallery',
        );

    if (result != null && !(result.sharp && result.exposure == ExposureVerdict.ok)) {
      final reason = !result.sharp
          ? 'blur'
          : result.exposure == ExposureVerdict.tooDark
          ? 'too_dark'
          : 'too_bright';
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(_reasonHint(reason)),
          action: SnackBarAction(label: 'Send anyway', onPressed: proceed),
        ),
      );
      return;
    }
    proceed();
  }

  /// Runs the same pure blur/exposure checks on a picked file: decode to a
  /// ~320px grid, derive luma from RGBA, then [analyzeLuma].
  Future<QualityResult?> _analyzeFile(String path) async {
    try {
      final bytes = await File(path).readAsBytes();
      final codec = await ui.instantiateImageCodec(bytes, targetWidth: 320);
      final frame = await codec.getNextFrame();
      final image = frame.image;
      final w = image.width;
      final h = image.height;
      final data = await image.toByteData(format: ui.ImageByteFormat.rawRgba);
      image.dispose();
      if (data == null) return null;
      final rgba = data.buffer.asUint8List();
      final luma = Uint8List(w * h);
      for (var i = 0; i < w * h; i++) {
        final r = rgba[i * 4];
        final g = rgba[i * 4 + 1];
        final b = rgba[i * 4 + 2];
        luma[i] = (r * 77 + g * 150 + b * 29) >> 8;
      }
      return analyzeLuma(luma, w, h);
    } catch (_) {
      return null;
    }
  }

  @override
  void dispose() {
    final controller = _controller;
    _controller = null;
    if (controller != null) {
      () async {
        try {
          if (controller.value.isStreamingImages) {
            await controller.stopImageStream();
          }
        } catch (_) {
          // ignore
        }
        await controller.dispose();
      }();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final gate = ref.watch(cameraQualityProvider);

    final green = gate.green;
    // Before the delayed analysis stream starts, keep the frame neutral (not
    // red/green) with a "getting ready" hint so nothing flashes during bring-up.
    final Color borderColor;
    final String hint;
    if (!_streaming) {
      borderColor = Colors.white54;
      hint = 'Getting ready…';
    } else if (green) {
      borderColor = Colors.greenAccent;
      hint = _framingHint(widget.targetPart);
    } else {
      borderColor = Colors.redAccent;
      hint = _reasonHint(gate.reason);
    }

    return Material(
      color: Colors.black,
      child: Stack(
        fit: StackFit.expand,
        children: [
          if (_ready && _controller != null)
            _CoverPreview(controller: _controller!)
          else
            const Center(
              child: CircularProgressIndicator(color: Colors.white),
            ),

          // Quality border.
          IgnorePointer(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 180),
              decoration: BoxDecoration(
                border: Border.all(color: borderColor, width: 5),
              ),
            ),
          ),

          // Agent's spoken reason, small, near the top.
          if (widget.reason.isNotEmpty)
            Positioned(
              top: 12,
              left: 16,
              right: 16,
              child: _Pill(
                text: widget.reason,
                background: Colors.black.withValues(alpha: 0.45),
                textColor: Colors.white70,
                fontSize: 12,
              ),
            ),

          // Bottom stack: advisory hint, then the manual camera controls
          // (gallery · shutter · cancel).
          Positioned(
            left: 0,
            right: 0,
            bottom: 24,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: _Pill(
                    text: hint,
                    background: (green ? Colors.green : Colors.red).withValues(
                      alpha: 0.72,
                    ),
                    textColor: Colors.white,
                    fontSize: 16,
                  ),
                ),
                const SizedBox(height: 20),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 32),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _RoundButton(
                        icon: Icons.photo_library_outlined,
                        tooltip: 'Pick from gallery',
                        onPressed: _pickFromGallery,
                      ),
                      _ShutterButton(onPressed: _capture),
                      _RoundButton(
                        icon: Icons.close,
                        tooltip: 'Cancel',
                        onPressed: _cancel,
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  static String _framingHint(String targetPart) {
    switch (targetPart) {
      case 'leaf':
        return 'Show the leaf up close';
      case 'fruit':
        return 'Show the fruit up close';
      case 'stem':
      case 'branch':
      case 'bark':
        return 'Show the stem up close';
      case 'root':
        return 'Show the root';
      case 'soil':
        return 'Show the soil';
      case 'whole_plant':
        return 'Show the whole plant';
      case 'flower':
        return 'Show the flower up close';
      default:
        return 'Show the plant';
    }
  }

  static String _reasonHint(String? reason) {
    switch (reason) {
      case 'blur':
        return 'Hold the phone steady';
      case 'too_dark':
        return 'Too dark — move to a brighter spot';
      case 'too_bright':
        return 'Too bright — move to the shade';
      case 'no_plant':
        return 'Point at the plant';
      default:
        return 'Aim the camera at the plant';
    }
  }
}

/// Fills the screen with the preview using a BoxFit.cover recipe.
class _CoverPreview extends StatelessWidget {
  const _CoverPreview({required this.controller});

  final CameraController controller;

  @override
  Widget build(BuildContext context) {
    final mq = MediaQuery.of(context);
    var scale = controller.value.aspectRatio * mq.size.aspectRatio;
    if (scale < 1) scale = 1 / scale;
    return Transform.scale(
      scale: scale,
      child: Center(child: CameraPreview(controller)),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({
    required this.text,
    required this.background,
    required this.textColor,
    required this.fontSize,
  });

  final String text;
  final Color background;
  final Color textColor;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(20),
      ),
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: TextStyle(
          color: textColor,
          fontSize: fontSize,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// The manual shutter: a large classic camera button centred between the
/// gallery and cancel controls. Always tappable — the quality gate never
/// disables it.
class _ShutterButton extends StatelessWidget {
  const _ShutterButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: 'Take photo',
      child: GestureDetector(
        onTap: onPressed,
        behavior: HitTestBehavior.opaque,
        child: Container(
          width: 76,
          height: 76,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: Colors.white24,
            border: Border.all(color: Colors.white, width: 4),
          ),
          child: Padding(
            padding: const EdgeInsets.all(6),
            child: Container(
              decoration: const BoxDecoration(
                shape: BoxShape.circle,
                color: Colors.white,
              ),
              child: const Icon(
                Icons.camera_alt,
                color: Colors.black87,
                size: 30,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black.withValues(alpha: 0.5),
      shape: const CircleBorder(),
      child: IconButton(
        onPressed: onPressed,
        tooltip: tooltip,
        iconSize: 28,
        padding: const EdgeInsets.all(16),
        color: Colors.white,
        icon: Icon(icon),
      ),
    );
  }
}
