/// Microphone capture, reframed into the exact frame size the backend wants.
///
/// The `record` plugin emits PCM16 in arbitrary chunk sizes; the backend
/// requires EXACTLY 3200-byte frames (100 ms @ 16 kHz mono). [Reframer] buffers
/// the remainder between chunks; [MicStreamer] wires it to the recorder.
library;

import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

import '../config.dart';

/// Pure, testable reframer: accumulates arbitrary byte chunks and emits
/// fixed-size frames, buffering any leftover bytes for the next chunk.
class Reframer {
  Reframer({this.frameSize = micFrameBytes});

  /// Exact size of each emitted frame, in bytes.
  final int frameSize;

  final BytesBuilder _buffer = BytesBuilder(copy: false);

  /// Bytes currently buffered awaiting a full frame.
  int get pending => _buffer.length;

  /// Adds a chunk and returns any newly-completed frames (may be empty).
  ///
  /// Emitted frames are independent copies, so downstream consumers may retain
  /// them even though the recorder reuses its internal buffers.
  List<Uint8List> addChunk(Uint8List chunk) {
    _buffer.add(chunk);
    if (_buffer.length < frameSize) return const [];

    final all = _buffer.takeBytes(); // drains and clears the builder
    final frames = <Uint8List>[];
    var offset = 0;
    while (all.length - offset >= frameSize) {
      frames.add(
        Uint8List.fromList(
          Uint8List.sublistView(all, offset, offset + frameSize),
        ),
      );
      offset += frameSize;
    }
    if (offset < all.length) {
      _buffer.add(Uint8List.sublistView(all, offset));
    }
    return frames;
  }

  /// Discards any buffered remainder.
  void reset() => _buffer.clear();
}

/// Captures the mic and exposes a stream of exact [micFrameBytes] frames.
class MicStreamer {
  final AudioRecorder _recorder = AudioRecorder();
  final Reframer _reframer = Reframer();

  StreamSubscription<Uint8List>? _rawSub;
  StreamController<Uint8List>? _frames;

  /// Whether capture is currently active.
  bool get isRecording => _frames != null;

  /// Starts capture and returns a stream of exact 3200-byte PCM16 frames.
  ///
  /// The caller is responsible for microphone permission before calling this.
  Future<Stream<Uint8List>> start() async {
    await stop();
    _reframer.reset();
    final controller = StreamController<Uint8List>.broadcast();
    _frames = controller;

    final raw = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: micSampleRate,
        numChannels: 1,
        autoGain: false,
        echoCancel: true,
        noiseSuppress: true,
        androidConfig: AndroidRecordConfig(
          audioSource: AndroidAudioSource.voiceCommunication,
        ),
      ),
    );

    _rawSub = raw.listen(
      (chunk) {
        final out = _frames;
        if (out == null) return;
        for (final frame in _reframer.addChunk(chunk)) {
          out.add(frame);
        }
      },
      onError: (Object e, StackTrace st) => _frames?.addError(e, st),
      cancelOnError: false,
    );

    return controller.stream;
  }

  /// Stops capture and releases the recorder stream.
  Future<void> stop() async {
    await _rawSub?.cancel();
    _rawSub = null;
    if (await _recorder.isRecording()) {
      await _recorder.stop();
    }
    await _frames?.close();
    _frames = null;
  }

  /// Fully disposes the recorder.
  Future<void> dispose() async {
    await stop();
    await _recorder.dispose();
  }
}
