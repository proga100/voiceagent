/// Photo confirmation + upload, shown full-screen under the PiP avatar.
///
/// Sequence: flash a big checkmark over the captured photo, compress it in the
/// background (long side already bounded by the camera preset / gallery picker;
/// this re-encodes to JPEG q85, strips EXIF, auto-rotates), then pause the mic,
/// send `photo.upload`, and spin until `photo.received` (15s timeout → retry).
/// On success it adds the thumbnail to the transcript and returns to the
/// interview.
library;

import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_image_compress/flutter_image_compress.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:uuid/uuid.dart';

import '../interview/transcript_provider.dart';
import '../session/app_mode.dart';
import '../session/photo_request_provider.dart';
import '../session/voice_session_controller.dart';

enum _Stage { preparing, uploading, error }

/// Confirms and uploads one captured/picked photo.
class ConfirmOverlay extends ConsumerStatefulWidget {
  const ConfirmOverlay({
    super.key,
    required this.path,
    required this.targetPart,
    required this.origin,
  });

  final String path;
  final String targetPart;
  final String origin;

  @override
  ConsumerState<ConfirmOverlay> createState() => _ConfirmOverlayState();
}

class _ConfirmOverlayState extends ConsumerState<ConfirmOverlay>
    with SingleTickerProviderStateMixin {
  static const Duration _minCheckmark = Duration(milliseconds: 1400);

  final String _photoId = const Uuid().v4();
  late final AnimationController _check;

  _Stage _stage = _Stage.preparing;
  Uint8List? _compressed;
  String? _thumbPath;
  int _width = 0;
  int _height = 0;

  @override
  void initState() {
    super.initState();
    _check = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 500),
    )..forward();
    WidgetsBinding.instance.addPostFrameCallback((_) => _run());
  }

  @override
  void dispose() {
    _check.dispose();
    super.dispose();
  }

  Future<void> _run() async {
    final started = DateTime.now();
    final bytes = await _compress();
    if (!mounted) return;
    if (bytes == null) {
      setState(() => _stage = _Stage.error);
      return;
    }
    _compressed = bytes;

    // Keep the checkmark on screen for a beat before the upload spinner.
    final elapsed = DateTime.now().difference(started);
    if (elapsed < _minCheckmark) {
      await Future<void>.delayed(_minCheckmark - elapsed);
      if (!mounted) return;
    }
    await _upload();
  }

  Future<Uint8List?> _compress() async {
    try {
      final out = await FlutterImageCompress.compressWithFile(
        widget.path,
        minWidth: 1536,
        minHeight: 1536,
        quality: 85,
        keepExif: false,
        autoCorrectionAngle: true,
        format: CompressFormat.jpeg,
      );
      if (out == null) return null;

      // Read back the encoded dimensions for the upload metadata.
      try {
        final codec = await ui.instantiateImageCodec(out);
        final frame = await codec.getNextFrame();
        _width = frame.image.width;
        _height = frame.image.height;
        frame.image.dispose();
      } catch (_) {
        // dimensions are optional in the protocol
      }

      final thumb = File('${Directory.systemTemp.path}/plantdoc_$_photoId.jpg');
      await thumb.writeAsBytes(out, flush: true);
      _thumbPath = thumb.path;
      debugPrint(
        '[confirm] compressed ${widget.path} -> ${out.length} bytes '
        '(${_width}x$_height)',
      );
      return out;
    } catch (_) {
      return null;
    }
  }

  Future<void> _upload() async {
    final bytes = _compressed;
    if (bytes == null) {
      setState(() => _stage = _Stage.error);
      return;
    }
    setState(() => _stage = _Stage.uploading);

    try {
      // 2026-08-05 protocol: bytes go over REST, the socket carries the URL.
      await ref
          .read(voiceSessionProvider.notifier)
          .uploadPhoto(photoId: _photoId, bytes: bytes);
      if (!mounted) return;
      ref
          .read(transcriptProvider.notifier)
          .addImage(_thumbPath ?? widget.path, widget.targetPart);
      // The photo request is now satisfied — retire the CTA banner.
      ref.read(pendingPhotoRequestProvider.notifier).clear();
      ref.read(appModeProvider.notifier).toInterview();
    } catch (_) {
      if (mounted) setState(() => _stage = _Stage.error);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.black,
      child: Stack(
        fit: StackFit.expand,
        children: [
          Image.file(File(widget.path), fit: BoxFit.contain),
          Container(color: Colors.black.withValues(alpha: 0.35)),
          Center(child: _center()),
        ],
      ),
    );
  }

  Widget _center() {
    switch (_stage) {
      case _Stage.preparing:
        return ScaleTransition(
          scale: CurvedAnimation(parent: _check, curve: Curves.elasticOut),
          child: const _CheckBadge(),
        );
      case _Stage.uploading:
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: const [
            SizedBox(
              width: 44,
              height: 44,
              child: CircularProgressIndicator(color: Colors.white),
            ),
            SizedBox(height: 16),
            Text(
              'Sending…',
              style: TextStyle(color: Colors.white, fontSize: 16),
            ),
          ],
        );
      case _Stage.error:
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.error_outline, color: Colors.redAccent, size: 48),
            const SizedBox(height: 12),
            const Text(
              'Send failed',
              style: TextStyle(color: Colors.white, fontSize: 16),
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 12,
              children: [
                FilledButton.icon(
                  onPressed: _upload,
                  icon: const Icon(Icons.refresh),
                  label: const Text('Retry'),
                ),
                TextButton(
                  onPressed: () =>
                      ref.read(appModeProvider.notifier).toInterview(),
                  child: const Text(
                    'Cancel',
                    style: TextStyle(color: Colors.white70),
                  ),
                ),
              ],
            ),
          ],
        );
    }
  }
}

class _CheckBadge extends StatelessWidget {
  const _CheckBadge();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 108,
      height: 108,
      decoration: const BoxDecoration(
        color: Colors.greenAccent,
        shape: BoxShape.circle,
      ),
      child: const Icon(Icons.check, color: Colors.black, size: 64),
    );
  }
}
