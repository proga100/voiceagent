/// The live transcript: an ordered list of conversation bubbles.
///
/// Turn logic:
///  * `stt.partial` builds/replaces the farmer's current partial bubble.
///  * The first `llm.token` of a burst finalizes the farmer bubble and opens an
///    agent bubble that accumulates tokens until `tts.finished` /
///    `agent.interrupted`.
///  * System notices (photo requests, receipts, diagnosis-started) and the
///    diagnosis card are appended as their own bubbles.
///
/// Agent tokens arrive dozens/sec while the agent speaks. Rebuilding the
/// transcript [state] per token churns the main thread (and the interview
/// ListView) right in the audio window, so tokens are coalesced in a plain
/// [String] buffer and flushed to state on a ~100 ms [Timer] (≈10 Hz instead of
/// per-token). The buffer is flushed synchronously at every turn boundary
/// ([_finalizeAgent]) so no token is ever lost or delayed at turn end. Farmer /
/// system / image / diagnosis bubbles are rare and stay immediate.
library;

import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/protocol/events.dart';
import '../chat/chat.dart';
import '../chat/strings.dart';

/// Base type for a transcript entry. [id] is stable for the widget key.
sealed class TranscriptBubble {
  const TranscriptBubble(this.id);
  final int id;
}

/// The farmer's speech. [isFinal] flips true once the agent starts replying.
class FarmerBubble extends TranscriptBubble {
  const FarmerBubble(super.id, {required this.text, required this.isFinal});
  final String text;
  final bool isFinal;

  FarmerBubble copyWith({String? text, bool? isFinal}) => FarmerBubble(
    id,
    text: text ?? this.text,
    isFinal: isFinal ?? this.isFinal,
  );
}

/// The agent's streamed reply. [isFinal] flips true when the turn ends.
class AgentBubble extends TranscriptBubble {
  const AgentBubble(super.id, {required this.text, required this.isFinal});
  final String text;
  final bool isFinal;

  AgentBubble copyWith({String? text, bool? isFinal}) => AgentBubble(
    id,
    text: text ?? this.text,
    isFinal: isFinal ?? this.isFinal,
  );
}

/// A system notice (photo request/receipt, diagnosis started, error).
class SystemBubble extends TranscriptBubble {
  const SystemBubble(super.id, {required this.text});
  final String text;
}

/// A photo the farmer uploaded, shown as a thumbnail on the farmer side.
class ImageBubble extends TranscriptBubble {
  const ImageBubble(super.id, {required this.path, required this.targetPart});

  /// Local file path of the (compressed) uploaded image.
  final String path;
  final String targetPart;
}

/// The completed diagnosis, rendered as a card.
class DiagnosisCardBubble extends TranscriptBubble {
  const DiagnosisCardBubble(
    super.id, {
    required this.caseId,
    required this.result,
    this.preparations = const [],
    this.photos = const [],
  });
  final String caseId;
  final DiagnosisResult result;

  /// Growz Agroapteka preparations riding the same `case.diagnosis` event
  /// (contract addendum P2.1). Empty when the backend found none (or is v1).
  final List<Preparation> preparations;

  /// Photos riding the same `case.diagnosis` event. Empty on a v1 backend.
  final List<PhotoRef> photos;
}

/// The finished agronom (expert) review, rendered as its own card — separate
/// from the AI [DiagnosisCardBubble] (spec §7.2, contract addendum P3.7).
class AgronomCardBubble extends TranscriptBubble {
  const AgronomCardBubble(super.id, {required this.review});
  final AgronomReview review;
}

/// Builds the transcript from routed [ServerEvent]s.
class TranscriptNotifier extends Notifier<List<TranscriptBubble>> {
  int _nextId = 0;
  int? _openFarmerId;
  int? _openAgentId;

  /// Coalescing window for agent tokens (see the library doc comment).
  static const Duration _agentFlushInterval = Duration(milliseconds: 100);

  /// Agent tokens accumulated since the last flush (empty when nothing pends).
  final StringBuffer _agentBuffer = StringBuffer();

  /// Live only while buffered tokens are waiting to flush; `null` otherwise.
  Timer? _agentFlushTimer;

  @override
  List<TranscriptBubble> build() {
    // A pending timer that fires after disposal would touch [state] and throw,
    // so drop it (and any buffered text) when the provider is torn down.
    ref.onDispose(() {
      _agentFlushTimer?.cancel();
      _agentFlushTimer = null;
      _agentBuffer.clear();
    });
    return const [];
  }

  int _id() => _nextId++;

  void _append(TranscriptBubble bubble) => state = [...state, bubble];

  void _replace(int id, TranscriptBubble bubble) => state = [
    for (final b in state)
      if (b.id == id) bubble else b,
  ];

  /// Handles `stt.partial`: replaces the farmer's current partial text.
  void onSttPartial(String text) {
    _finalizeAgent();
    final open = _openFarmerId;
    if (open == null) {
      final id = _id();
      _openFarmerId = id;
      _append(FarmerBubble(id, text: text, isFinal: false));
      return;
    }
    final idx = state.indexWhere((b) => b.id == open);
    if (idx < 0) {
      _openFarmerId = null;
      onSttPartial(text);
      return;
    }
    _replace(open, (state[idx] as FarmerBubble).copyWith(text: text));
  }

  /// Handles `llm.token`: finalizes the farmer turn, then buffers the token.
  ///
  /// Finalizing the farmer bubble stays immediate (it only fires work on the
  /// first token of a burst). The token itself is appended to [_agentBuffer]
  /// and lands in [state] on the next [_agentFlushTimer] tick, so a fast token
  /// stream rebuilds the transcript ≈10 Hz instead of per token.
  void onLlmToken(String token) {
    _finalizeFarmer();
    _agentBuffer.write(token);
    _agentFlushTimer ??= Timer(_agentFlushInterval, _flushAgentBuffer);
  }

  /// Drains [_agentBuffer] into the open agent bubble (opening one if needed)
  /// and cancels the pending flush timer. Called on the timer tick and — so no
  /// text is lost — synchronously at every turn boundary (see [_finalizeAgent]).
  void _flushAgentBuffer() {
    _agentFlushTimer?.cancel();
    _agentFlushTimer = null;
    if (_agentBuffer.isEmpty) return;
    final text = _agentBuffer.toString();
    _agentBuffer.clear();

    final open = _openAgentId;
    if (open == null) {
      final id = _id();
      _openAgentId = id;
      _append(AgentBubble(id, text: text, isFinal: false));
      return;
    }
    final idx = state.indexWhere((b) => b.id == open);
    if (idx < 0) {
      // The open bubble vanished (unexpected): reopen with the buffered text.
      final id = _id();
      _openAgentId = id;
      _append(AgentBubble(id, text: text, isFinal: false));
      return;
    }
    final cur = state[idx] as AgentBubble;
    _replace(open, cur.copyWith(text: cur.text + text));
  }

  /// Handles `tts.finished` / `agent.interrupted`: closes the agent turn.
  void onAgentDone() => _finalizeAgent();

  /// Handles `stt.corrected`: the backend re-transcribed a finished farmer
  /// turn's actual audio (parallel Scribe STT). Patches the newest farmer
  /// bubble belonging to that turn — matched by content, not "last bubble",
  /// so a late correction can never clobber a newer turn. The bubble may hold
  /// only the TAIL of the turn (stt.partial replaces text per fragment),
  /// hence the suffix match. No match → correction silently dropped.
  void correctFarmer(String orig, String corrected) {
    final o = orig.trim();
    final c = corrected.trim();
    if (o.isEmpty || c.isEmpty) return;
    for (var i = state.length - 1; i >= 0; i--) {
      final b = state[i];
      if (b is! FarmerBubble) continue;
      final t = b.text.trim();
      if (t.isNotEmpty && (t == o || o.endsWith(t))) {
        _replace(b.id, b.copyWith(text: c));
        return;
      }
    }
  }

  /// Appends a final farmer bubble for a TYPED message (`text.input`) — typed
  /// turns never produce stt.partial events, so the client adds the bubble.
  void addFarmerText(String text) {
    _finalizeFarmer();
    _finalizeAgent();
    _append(FarmerBubble(_id(), text: text, isFinal: true));
  }

  /// Appends a system notice bubble.
  void addSystem(String text) {
    _finalizeFarmer();
    _finalizeAgent();
    _append(SystemBubble(_id(), text: text));
  }

  /// Appends the diagnosis card bubble.
  void addDiagnosis(
    String caseId,
    DiagnosisResult result, {
    List<Preparation> preparations = const [],
    List<PhotoRef> photos = const [],
  }) {
    _finalizeAgent();
    _append(
      DiagnosisCardBubble(
        _id(),
        caseId: caseId,
        result: result,
        preparations: preparations,
        photos: photos,
      ),
    );
  }

  /// Appends an uploaded-photo thumbnail bubble (farmer side).
  void addImage(String path, String targetPart) {
    _finalizeFarmer();
    _finalizeAgent();
    _append(ImageBubble(_id(), path: path, targetPart: targetPart));
  }

  /// Appends the finished agronom review card (spec §7.2, contract addendum
  /// P3.7).
  void addAgronomReview(AgronomReview review) {
    _finalizeFarmer();
    _finalizeAgent();
    _append(AgronomCardBubble(_id(), review: review));
  }

  /// Preloads a resumed chat's stored history (`GET /chats/{id}.messages`)
  /// into the transcript, once, before the live session starts streaming new
  /// turns. `role: farmer` becomes a (final) farmer bubble, `role: rais` an
  /// agent bubble — EXCEPT `kind: photo` (the photo bytes themselves were
  /// never stored, so it renders as a system "📷 Rasm" notice) and
  /// `kind: diagnosis` (rendered as a system notice: the structured
  /// [DiagnosisResult] isn't persisted, only its summary text).
  void addHistory(List<ChatMessage> messages) {
    if (messages.isEmpty) return;
    final bubbles = <TranscriptBubble>[];
    for (final m in messages) {
      if (m.kind == 'photo') {
        bubbles.add(SystemBubble(_id(), text: S.photoBubble));
        continue;
      }
      if (m.kind == 'diagnosis') {
        bubbles.add(SystemBubble(_id(), text: m.text));
        continue;
      }
      // Spec §7 (contract addendum P3.7): the agronom review's content is
      // carried by the AgronomCard (seeded separately from the chat summary
      // on open), not by this stored message text — skip it here so it isn't
      // ALSO shown as a plain system bubble.
      if (m.kind == 'agronom_review') continue;
      switch (m.role) {
        case 'farmer':
          bubbles.add(FarmerBubble(_id(), text: m.text, isFinal: true));
        case 'rais':
          bubbles.add(AgentBubble(_id(), text: m.text, isFinal: true));
        default:
          bubbles.add(SystemBubble(_id(), text: m.text));
      }
    }
    state = [...state, ...bubbles];
  }

  /// Clears the transcript (new session).
  void clear() {
    _agentFlushTimer?.cancel();
    _agentFlushTimer = null;
    _agentBuffer.clear();
    _openFarmerId = null;
    _openAgentId = null;
    state = const [];
  }

  void _finalizeFarmer() {
    final id = _openFarmerId;
    _openFarmerId = null;
    if (id == null) return;
    final idx = state.indexWhere((b) => b.id == id);
    if (idx < 0) return;
    _replace(id, (state[idx] as FarmerBubble).copyWith(isFinal: true));
  }

  void _finalizeAgent() {
    // Land any coalesced tokens before the turn closes so nothing is dropped or
    // deferred past turn end (tts.finished / agent.interrupted / a new farmer
    // partial all route through here).
    _flushAgentBuffer();
    final id = _openAgentId;
    _openAgentId = null;
    if (id == null) return;
    final idx = state.indexWhere((b) => b.id == id);
    if (idx < 0) return;
    _replace(id, (state[idx] as AgentBubble).copyWith(isFinal: true));
  }
}

/// Provider for the transcript bubble list.
final transcriptProvider =
    NotifierProvider<TranscriptNotifier, List<TranscriptBubble>>(
      TranscriptNotifier.new,
    );
