/// The heart of the pipeline: owns the socket, mic and player lifecycles and
/// routes events between them.
///
/// Flow: request mic permission -> configure the audio session -> set up the
/// player -> connect the socket -> on connect send `session.start` and turn the
/// mic on. Inbound audio goes to the player (and lipsync analyzer); inbound
/// events update the transcript and app-mode; `agent.interrupted` flushes
/// playback instantly. [SessionState] is surfaced to the UI.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:audio_session/audio_session.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';

import '../../core/audio/lipsync_analyzer.dart';
import '../../core/audio/mic_streamer.dart';
import '../../core/audio/pcm_player.dart';
import '../../core/audio_debug.dart';
import '../../core/config.dart';
import '../../core/identity/device_identity.dart';
import '../../core/protocol/events.dart';
import '../../core/ws/voice_socket.dart';
import '../chat/chat_providers.dart';
import '../chat/strings.dart';
import '../crop/crop.dart';
import '../crop/crop_providers.dart';
import '../crop/location_service.dart';
import '../interview/transcript_provider.dart';
import 'app_mode.dart';
import 'photo_request_provider.dart';

/// High-level session status for the UI.
enum SessionState { disconnected, connecting, live, error }

/// Immutable UI snapshot of the session (status + latest debug counters).
class SessionSnapshot {
  const SessionSnapshot({
    this.state = SessionState.disconnected,
    this.micLevel = 0,
    this.pttHeld = false,
    this.errorMessage,
  });

  final SessionState state;

  /// True while the farmer holds the push-to-talk button (mic streaming).
  final bool pttHeld;


  /// Latest Azure TTS character count, for the debug row.

  /// Normalised mic input level `0..1`, for the avatar ring.
  final double micLevel;

  /// Human-readable error, when [state] is [SessionState.error].
  final String? errorMessage;

  SessionSnapshot copyWith({
    SessionState? state,
    double? micLevel,
    bool? pttHeld,
    String? errorMessage,
    bool clearError = false,
  }) => SessionSnapshot(
    state: state ?? this.state,
    micLevel: micLevel ?? this.micLevel,
    pttHeld: pttHeld ?? this.pttHeld,
    errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
  );
}

/// Orchestrates the voice session.
class VoiceSessionController extends Notifier<SessionSnapshot> {
  final MicStreamer _mic = MicStreamer();
  final PcmPlayer _player = PcmPlayer();

  /// Lipsync ownership (Phase 4): the controller owns the [analyzer] and feeds
  /// it every inbound PCM chunk ([_onAudio]); it also exposes the live playback
  /// cursor ([playbackPositionMs]). The *avatar widget* owns the 33 ms ticker
  /// that reads those two and calls `analyzer.frameAt(playbackPositionMs)` to
  /// drive the WebView. On barge-in the controller only calls [analyzer.reset]
  /// (clearing the FIFO); the widget ticker observes the now-empty FIFO and
  /// snaps the mouth shut (`avatarSetOpen(0)`) within one tick, so the
  /// controller never has to reach into the WebView.
  final LipsyncAnalyzer analyzer = LipsyncAnalyzer();

  /// Approximate ms of agent audio actually played so far — the clock the
  /// avatar ticker samples to pick the on-screen lipsync frame.
  int get playbackPositionMs => _player.playbackPositionMs;

  /// Whether agent audio is audibly playing right now. The camera quality
  /// pipeline defers ML Kit work to outside this window (audio-stutter fix).
  bool get isPlaybackActive => _player.isPlaybackActive;

  VoiceSocket? _socket;
  StreamSubscription<ServerEvent>? _eventSub;
  StreamSubscription<Uint8List>? _audioSub;
  StreamSubscription<SocketConnectionState>? _connSub;
  StreamSubscription<Uint8List>? _micSub;
  bool _micStarted = false;

  /// When true the mic keeps recording but frames are NOT sent upstream — used
  /// to free the wire while a photo upload is in flight (Phase 5).
  bool _micPaused = false;

  /// Push-to-talk: mic frames go upstream ONLY while the farmer holds the
  /// button. The session (socket, playback, transcript) is untouched by the
  /// button — it lives from [start] to [stop] like a phone call.
  bool _pttHeld = false;

  /// The upload awaiting its `photo.received` ack, if any (Phase 5).
  _PendingUpload? _pendingUpload;

  /// Stable per-install id sent as `user_id` in session.start — keys Alomat's
  /// per-farmer memory. Fetched once in [start] (SharedPreferences is async);
  /// null only if the fetch failed, which degrades to a memoryless session.
  String? _deviceId;

  /// Enrichment carried in session.start: the crop the farmer picked and their
  /// GPS (both resolved once in [start], both best-effort/optional).
  Crop? _crop;
  ({double lat, double lon})? _latLon;

  /// The chat this session is bound to (from [activeChatProvider], resolved
  /// once in [start]). `null` → a plain session, `session.start` omits
  /// `chat_id` — exactly today's behaviour.
  String? _chatId;

  /// Set by [_suspendAfterExpiry] (the session.expired safety-net) so the next
  /// [start] resumes the SAME chat WITHOUT wiping the live transcript: the
  /// on-screen transcript (with the just-delivered diagnosis card) is fresher
  /// than the [ActiveChat.history] snapshot taken at chat-open. Consumed and
  /// reset to false in [start].
  bool _resumeSameChat = false;

  @override
  SessionSnapshot build() {
    ref.onDispose(() {
      unawaited(_teardown());
      analyzer.dispose();
    });
    // Leaving the interview (camera / confirm opens) → immediately silence any
    // agent speech that is mid-play or queued, so the photo phase is quiet.
    // Inbound audio is also dropped while out of the interview (see _onAudio).
    ref.listen(appModeProvider, (prev, next) {
      if (next is! InterviewMode) _flushPlayback();
    });
    return const SessionSnapshot();
  }

  /// Starts a session: permission, audio session, player, socket, mic.
  Future<void> start() async {
    if (state.state == SessionState.connecting ||
        state.state == SessionState.live) {
      return;
    }

    final granted = await _ensureMicPermission();
    if (!granted) {
      state = state.copyWith(
        state: SessionState.error,
        errorMessage: 'Mikrofon uchun ruxsat berilmadi',
      );
      return;
    }

    await _configureAudioSession();
    await _player.setup();
    try {
      _deviceId ??= await getOrCreateDeviceId();
    } catch (_) {
      // No id → the backend simply runs a memoryless session.
    }

    // The chat this session is bound to (opened from the home chat list —
    // `features/chat/chat_list_screen.dart`). An empty id means the
    // fail-open "offline" placeholder (POST /chats failed): the interview
    // still runs, just without a chat_id (a plain, chatless session).
    final active = ref.read(activeChatProvider);
    final resuming = active != null && active.summary.id.isNotEmpty;
    _chatId = resuming ? active.summary.id : null;
    // A resumed chat with a stored crop carries it into session.start (the
    // server also falls back to the stored chat's crop independently); a
    // brand-new chat picks its crop later, inside the guided flow.
    if (resuming &&
        active.summary.cropId.isNotEmpty &&
        ref.read(selectedCropProvider) == null) {
      ref
          .read(selectedCropProvider.notifier)
          .select(Crop(id: active.summary.cropId, name: active.summary.cropName));
    }

    // Enrichment (both optional, both best-effort): the picked crop and GPS.
    // Resolved before the socket connects so they're ready for session.start.
    _crop = ref.read(selectedCropProvider);
    _latLon = await const LocationService().currentLatLon();

    // Resuming after a session.expired soft-stop: the live transcript on screen
    // (with the just-delivered diagnosis card) is fresher than active.history,
    // so keep it as-is — skip the clear + history reload. Fresh starts clear
    // and seed from the chat-open snapshot as before.
    if (_resumeSameChat) {
      _resumeSameChat = false;
    } else {
      ref.read(transcriptProvider.notifier).clear();
      if (active != null && active.history.isNotEmpty) {
        ref.read(transcriptProvider.notifier).addHistory(active.history);
      }
    }
    if (active != null && active.summary.id.isEmpty) {
      ref.read(transcriptProvider.notifier).addSystem(S.offlineChat);
    }
    // Spec §7 poll-on-open (contract addendum P3.7): a chat-bound session
    // seeds the agronom review state from the just-fetched chat summary and
    // surfaces it once — pending as a system note, done as its own card. No
    // FCM/timers anywhere; the list-side poll is `stop()`'s
    // `chatListProvider` invalidation below.
    if (active != null && active.summary.id.isNotEmpty) {
      final review = active.summary.agronomReview;
      ref.read(agronomReviewProvider.notifier).set(review);
      final transcript = ref.read(transcriptProvider.notifier);
      if (review.status == 'pending') transcript.addSystem(S.agronomPending);
      if (review.status == 'done') transcript.addAgronomReview(review);
    }
    ref.read(appModeProvider.notifier).toInterview();
    ref.read(pendingPhotoRequestProvider.notifier).clear();
    ref.read(guideQuestionProvider.notifier).clear();
    ref.read(guidePhaseProvider.notifier).clear();
    ref.read(guideSelectionsProvider.notifier).clear();

    state = state.copyWith(state: SessionState.connecting, clearError: true);
    _micStarted = false;
    _micPaused = false;
    _pttHeld = false;

    final socket = VoiceSocket(baseUrl: wsUrl, token: wsToken);
    _socket = socket;
    if (kAudioDebug) {
      // Route flag-gated audio telemetry to the backend over this socket.
      AudioDebug.sink = (msg) => socket.sendRaw({'type': 'debug.log', 'msg': msg});
    }
    _connSub = socket.connectionState.listen(_onConnState);
    _eventSub = socket.events.listen(_onEvent);
    _audioSub = socket.audio.listen(_onAudio);
    await socket.connect();
  }

  /// Cleanly ends the session. The transcript is left on screen — it clears
  /// only when the next conversation starts.
  ///
  /// Also the app's "go home" action: clearing [activeChatProvider] is what
  /// the top-level gate (`main.dart`) watches to swap back to the chat list.
  /// The crop pick and guide state are per-conversation and must not leak
  /// into the next chat; the chat list is invalidated so it reflects this
  /// conversation's final state (title, last message, updated_at).
  Future<void> stop() async {
    // "session.end" was removed (2026-08-05): closing the socket IS the
    // hangup signal — the backend finalizes in its teardown either way.
    await _teardown();
    state = state.copyWith(
      state: SessionState.disconnected,
      pttHeld: false,
      micLevel: 0.0,
      clearError: true,
    );
    ref.read(activeChatProvider.notifier).close();
    ref.read(selectedCropProvider.notifier).clear();
    ref.read(guideQuestionProvider.notifier).clear();
    ref.read(guidePhaseProvider.notifier).clear();
    ref.read(guideSelectionsProvider.notifier).clear();
    ref.read(agronomReviewProvider.notifier).clear();
    ref.invalidate(chatListProvider);
  }

  /// Soft-stop for the `session.expired` safety-net: tears the socket/mic/player
  /// down but leaves the app state intact. Unlike [stop] it does NOT clear
  /// [activeChatProvider] (so `_HomeGate` keeps [InterviewScreen] mounted with
  /// the transcript + diagnosis on screen), nor the crop/guide/agronom state or
  /// the chat list — those stay exclusive to [stop]. Closing the socket lets
  /// the backend loop finalize (chat fold-in, memory) exactly as after [stop].
  /// [_teardown] cancels `_eventSub`/`_connSub` before this returns, so no late
  /// `SocketConnectionState.failed` can overwrite `disconnected` with an error.
  Future<void> _suspendAfterExpiry() async {
    await _teardown();
    state = state.copyWith(
      state: SessionState.disconnected,
      pttHeld: false,
      micLevel: 0.0,
      clearError: true,
    );
    _resumeSameChat = true;
  }

  /// Barge-in: tell the server to stop, and flush local playback immediately.
  void interrupt() {
    _socket?.send(const UserInterrupt());
    _flushPlayback();
    ref.read(transcriptProvider.notifier).onAgentDone();
  }

  /// Sends an arbitrary client event (Phase 5 camera: `photo.quality`,
  /// `camera.cancelled`).
  void sendClient(ClientEvent event) => _socket?.send(event);

  /// Sends a typed farmer message. The bubble is added locally (typed turns
  /// produce no stt.partial), then the text goes upstream where the agent
  /// answers with voice + text as usual.
  void sendText(String text) {
    final t = text.trim();
    if (t.isEmpty || state.state != SessionState.live) return;
    ref.read(transcriptProvider.notifier).addFarmerText(t);
    _socket?.send(TextInputRequest(text: t));
  }

  /// Sends a tapped guided-flow answer (`chat.answer`). A spoken answer takes
  /// a different path entirely — captured server-side by the Live
  /// `select_option` tool — so this is only called from [GuideOptionsBar]
  /// taps. A no-op outside a bound, live session.
  void sendChatAnswer(
    String stepId, {
    String optionId = '',
    String value = '',
    String? cropId,
    String? cropName,
  }) {
    final chatId = _chatId;
    if (chatId == null || state.state != SessionState.live) return;
    _socket?.send(
      ChatAnswerRequest(
        chatId: chatId,
        stepId: stepId,
        optionId: optionId,
        value: value,
        cropId: cropId,
        cropName: cropName,
      ),
    );
  }

  /// Stops forwarding mic frames upstream (recording continues).
  void pauseMicFrames() => _micPaused = true;

  /// Resumes forwarding mic frames upstream.
  void resumeMicFrames() => _micPaused = false;

  /// Push-to-talk press/release. Press while the agent is speaking barges in
  /// immediately (local flush + user.interrupt) so she goes quiet the moment
  /// the farmer wants to talk. On release a short silence tail is streamed so
  /// the server-side VAD sees end-of-speech and commits the turn — otherwise
  /// the hard cut of the audio stream can stall turn detection.
  void setPttHeld(bool held) {
    if (_pttHeld == held) return;
    _pttHeld = held;
    if (held && _player.isPlaybackActive) interrupt();
    if (!held) unawaited(_sendSilenceTail());
    state = state.copyWith(pttHeld: held, micLevel: held ? null : 0.0);
  }

  /// ~1.2 s of zero-PCM (digital silence) after release, paced near realtime.
  /// Aborts if the farmer presses again (real audio resumes) or the socket is
  /// gone. 3200 bytes = 100 ms at 16 kHz mono 16-bit, same framing as the mic.
  Future<void> _sendSilenceTail() async {
    final silence = Uint8List(3200);
    for (var i = 0; i < 12; i++) {
      if (_pttHeld || _socket == null || state.state != SessionState.live) {
        return;
      }
      _socket?.sendAudio(silence);
      await Future.delayed(const Duration(milliseconds: 90));
    }
  }

  /// Uploads a photo (2026-08-05 protocol): the bytes go to `POST /photos`
  /// over REST first, then the returned public URL travels over the socket as
  /// `photo.upload {value}`. Resolves with the server's photo count once the
  /// matching `photo.received` arrives. Mic frames are paused for the WS leg;
  /// they resume on success, timeout or error. Throws [TimeoutException] if
  /// no ack arrives within [timeout], [http.ClientException]/[StateError] if
  /// the REST leg fails.
  Future<int> uploadPhoto({
    required String photoId,
    required Uint8List bytes,
    String mime = 'image/jpeg',
    Duration timeout = const Duration(seconds: 15),
  }) async {
    // REST leg — the bytes leave the device exactly once.
    final resp = await http
        .post(
          Uri.parse('$httpBaseUrl/photos'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'user_id': _deviceId ?? '',
            'chat_id': _chatId ?? '',
            'photo_id': photoId,
            'mime': mime,
            'data': base64Encode(bytes),
          }),
        )
        .timeout(timeout);
    if (resp.statusCode != 200) {
      throw StateError('photo upload failed: HTTP ${resp.statusCode}');
    }
    final url =
        ((jsonDecode(resp.body) as Map)['data'] as Map)['url'] as String;

    // WS leg — only the URL rides the socket.
    final request =
        PhotoUploadRequest(photoId: photoId, value: url, chatId: _chatId);
    final prev = _pendingUpload;
    if (prev != null && !prev.completer.isCompleted) {
      prev.completer.completeError(StateError('superseded'));
    }
    final completer = Completer<int>();
    _pendingUpload = _PendingUpload(request.photoId, completer);
    pauseMicFrames();
    _socket?.send(request);
    try {
      return await completer.future.timeout(timeout);
    } finally {
      resumeMicFrames();
      _pendingUpload = null;
    }
  }

  // --- socket wiring -------------------------------------------------------

  void _onConnState(SocketConnectionState cs) {
    switch (cs) {
      case SocketConnectionState.connecting:
      case SocketConnectionState.reconnecting:
        state = state.copyWith(state: SessionState.connecting);
      case SocketConnectionState.connected:
        // (Re)arm the session and ensure the mic is running.
        _socket?.send(ChatStartRequest(
          userId: _deviceId,
          cropId: _crop?.id,
          cropName: _crop?.name,
          lat: _latLon?.lat,
          lon: _latLon?.lon,
          chatId: _chatId,
        ));
        unawaited(_ensureMicRunning());
        state = state.copyWith(state: SessionState.live, clearError: true);
      case SocketConnectionState.disconnected:
        state = state.copyWith(state: SessionState.disconnected);
      case SocketConnectionState.failed:
        state = state.copyWith(
          state: SessionState.error,
          errorMessage: 'Ulanish uzildi',
        );
    }
  }

  void _onAudio(Uint8List bytes) {
    // During the photo phase (camera/confirm) the agent must stay silent, so
    // drop inbound audio entirely — nothing plays and the lipsync FIFO is not
    // fed. Playback resumes when we return to the interview.
    if (ref.read(appModeProvider) is! InterviewMode) return;
    analyzer.ingest(bytes);
    unawaited(_player.feed(bytes));
  }

  void _onEvent(ServerEvent event) {
    final transcript = ref.read(transcriptProvider.notifier);
    switch (event) {
      case SttPartial(:final text):
        transcript.onSttPartial(text);
      case LlmToken(:final token):
        transcript.onLlmToken(token);
      case TtsStarted():
        break;
      case TtsFinished():
        transcript.onAgentDone();
      case AgentInterrupted():
        _flushPlayback();
        transcript.onAgentDone();
      case ErrorEvent(:final code, :final message):
        transcript.addSystem('Xatolik: $message ($code)');
      case SttCorrected(:final text, :final orig):
        transcript.correctFarmer(orig, text);
      case SessionExpired():
        // Provider-side duration limit safety-net: soft-stop only. We do NOT
        // call stop() and do NOT clear activeChatProvider, so _HomeGate keeps
        // InterviewScreen mounted — the farmer stays put with the full
        // transcript + diagnosis visible and taps `Qayta boshlash` to reconnect
        // the SAME chat. Alomat's greeting picks the conversation back up.
        transcript.addSystem(
          'Suhbat vaqti tugadi — davom etish uchun qayta boshlang.',
        );
        unawaited(_suspendAfterExpiry());
      case ToolRequestPhoto(:final callId, :final targetPart, :final reason):
        // Manual capture (budget-phone fix): do NOT switch to the camera here.
        // Park the request so the interview shows a "Rasm olish" CTA banner and
        // the farmer opens the camera when the agent has finished speaking. A
        // new request simply replaces the pending one; the transcript notice
        // stays as the durable record of the ask.
        transcript.addSystem('Rasm kerak: $targetPart — $reason');
        ref
            .read(pendingPhotoRequestProvider.notifier)
            .set(callId: callId, targetPart: targetPart, reason: reason);
      case ToolCancelled():
        // The server withdrew the request — drop the banner. Mode is left
        // alone: in the manual flow screen changes are farmer-driven only
        // (the model often cancels+reissues while the camera is open, and
        // yanking the farmer out mid-aim is disorienting).
        ref.read(pendingPhotoRequestProvider.notifier).clear();
      case PhotoReceived(:final photoId, :final count):
        final pending = _pendingUpload;
        if (pending != null &&
            !pending.completer.isCompleted &&
            (pending.photoId == photoId || photoId.isEmpty)) {
          // The confirm/upload flow adds the image bubble on success, so no
          // system notice here.
          pending.completer.complete(count);
        } else {
          transcript.addSystem('Rasm qabul qilindi ($count)');
        }
      case DiagnosisStarted():
        transcript.addSystem('Tashxis boshlandi…');
      case CaseDiagnosis(
        :final caseId,
        :final result,
        :final preparations,
        :final photos,
      ):
        transcript.addDiagnosis(
          caseId,
          result,
          preparations: preparations,
          photos: photos,
        );
      case ChatStateEvent(:final phase, :final selections):
        ref.read(guidePhaseProvider.notifier).set(phase);
        ref.read(guideSelectionsProvider.notifier).setAll(selections);
        if (phase == 'consult' || phase == 'crop_context') {
          // crop_context anketa sends NO chat.question (the question arrives
          // as voice + subtitles) — clear the bar so the previous step's
          // buttons don't linger.
          ref.read(guideQuestionProvider.notifier).clear();
        }
      case ChatQuestion q:
        ref.read(guideQuestionProvider.notifier).set(q);
      case ChatStepAck(:final stepId, :final optionId, :final label):
        ref.read(guideQuestionProvider.notifier).clear();
        ref.read(guideSelectionsProvider.notifier).put(stepId, optionId);
        if (label.isNotEmpty) transcript.addSystem('✓ $label');
      case UnknownEvent():
        break;
    }
  }

  // --- mic -----------------------------------------------------------------

  Future<void> _ensureMicRunning() async {
    if (_micStarted || _mic.isRecording) return;
    _micStarted = true;
    try {
      final frames = await _mic.start();
      _micSub = frames.listen((frame) {
        // Forward mic audio ONLY while the push-to-talk button is held AND
        // during the interview. While the camera or confirm screen is up (the
        // photo-taking phase) the mic is muted so the agent hears nothing and
        // stays silent while the farmer aims and shoots. (_micPaused still
        // covers the in-flight photo upload.) The recorder itself keeps
        // running between holds so AEC/AGC stay warmed up.
        final inInterview = ref.read(appModeProvider) is InterviewMode;
        if (_pttHeld && !_micPaused && inInterview) _socket?.sendAudio(frame);
        // The ring lights up only while she can actually hear the farmer.
        state = state.copyWith(micLevel: _pttHeld ? _rms(frame) : 0.0);
      });
    } catch (_) {
      _micStarted = false;
      state = state.copyWith(
        state: SessionState.error,
        errorMessage: 'Mikrofonni ishga tushirib bo\'lmadi',
      );
    }
  }

  void _flushPlayback() {
    // Flush native playback and clear the lipsync FIFO together so both restart
    // from the same zero. reset() snaps mouthOpen→0; the avatar widget's ticker
    // sees the empty FIFO next tick and closes the mouth (avatarSetOpen(0)).
    unawaited(_player.flush());
    analyzer.reset();
  }

  /// RMS amplitude of a PCM16 frame, normalised to `0..1`.
  double _rms(Uint8List frame) {
    final samples = Int16List.view(
      frame.buffer,
      frame.offsetInBytes,
      frame.length ~/ 2,
    );
    if (samples.isEmpty) return 0;
    var sumSq = 0.0;
    for (final s in samples) {
      final v = s / 32768.0;
      sumSq += v * v;
    }
    final rms = math.sqrt(sumSq / samples.length);
    return (rms * 3).clamp(
      0.0,
      1.0,
    ); // gentle boost so speech reads on the ring
  }

  // --- permission / audio session -----------------------------------------

  Future<bool> _ensureMicPermission() async {
    final status = await Permission.microphone.request();
    return status.isGranted;
  }

  Future<void> _configureAudioSession() async {
    final session = await AudioSession.instance;
    await session.configure(
      AudioSessionConfiguration(
        avAudioSessionCategory: AVAudioSessionCategory.playAndRecord,
        avAudioSessionCategoryOptions:
            AVAudioSessionCategoryOptions.defaultToSpeaker |
            AVAudioSessionCategoryOptions.allowBluetooth,
        avAudioSessionMode: AVAudioSessionMode.voiceChat,
        // Media usage, NOT voiceCommunication: comm usage flips Android into
        // the in-call routing where playback rides the quiet voice-call
        // volume stream. Echo cancellation is unaffected — it comes from the
        // recorder's own AEC (AndroidAudioSource.voiceCommunication +
        // echoCancel on the RecordConfig), not from the output session.
        androidAudioAttributes: const AndroidAudioAttributes(
          contentType: AndroidAudioContentType.speech,
          usage: AndroidAudioUsage.media,
        ),
        androidAudioFocusGainType: AndroidAudioFocusGainType.gain,
        androidWillPauseWhenDucked: true,
      ),
    );
    await session.setActive(true);
  }

  // --- teardown ------------------------------------------------------------

  Future<void> _teardown() async {
    if (kAudioDebug) AudioDebug.sink = null;
    final pending = _pendingUpload;
    if (pending != null && !pending.completer.isCompleted) {
      pending.completer.completeError(StateError('session ended'));
    }
    _pendingUpload = null;
    _micPaused = false;
    _pttHeld = false;
    _chatId = null;
    await _micSub?.cancel();
    _micSub = null;
    _micStarted = false;
    await _mic.stop();
    await _eventSub?.cancel();
    _eventSub = null;
    await _audioSub?.cancel();
    _audioSub = null;
    await _connSub?.cancel();
    _connSub = null;
    await _socket?.dispose();
    _socket = null;
    await _player.dispose();
    analyzer.reset();
  }
}

/// A photo upload awaiting its `photo.received` acknowledgement.
class _PendingUpload {
  _PendingUpload(this.photoId, this.completer);
  final String photoId;
  final Completer<int> completer;
}

/// Provider for the voice session controller.
final voiceSessionProvider =
    NotifierProvider<VoiceSessionController, SessionSnapshot>(
      VoiceSessionController.new,
    );
