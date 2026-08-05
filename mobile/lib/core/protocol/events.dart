/// Wire protocol for the single voice WebSocket.
///
/// This layer is pure Dart (no Flutter imports) so it can be unit-tested in
/// isolation. It models:
///
///  * [ServerEvent] — a sealed hierarchy of every JSON text frame the backend
///    can send, plus [UnknownEvent] for graceful forward-compatibility.
///  * [ClientEvent] — the JSON text frames the client sends (builders with
///    [ClientEvent.toJson]).
///
/// Binary frames (mic PCM up, agent PCM down) are NOT modelled here — they are
/// handled as raw bytes by the socket layer.
library;

import '../config.dart';

// ---------------------------------------------------------------------------
// Server -> Client
// ---------------------------------------------------------------------------

/// Base type for every JSON frame received from the backend.
sealed class ServerEvent {
  const ServerEvent();

  /// Dispatches a decoded JSON map to the matching [ServerEvent] subclass.
  ///
  /// Unrecognised `type` values yield an [UnknownEvent] so the caller can
  /// ignore them without crashing (forward compatibility).
  static ServerEvent fromJson(Map<String, dynamic> json) {
    final type = json['type'] as String?;
    switch (type) {
      case 'stt.partial':
        return SttPartial(text: _str(json['value']));
      case 'llm.token':
        return LlmToken(token: _str(json['token']));
      case 'tts.finished':
        return const TtsFinished();
      case 'agent.interrupted':
        return const AgentInterrupted();
      case 'error':
        return ErrorEvent(
          code: _str(json['code']),
          message: _str(json['message']),
        );
      case 'session.expired':
        return SessionExpired(message: _str(json['message']));
      case 'stt.corrected':
        return SttCorrected(text: _str(json['text']), orig: _str(json['orig']));
      case 'tool.request_photo':
        return ToolRequestPhoto(
          callId: _str(json['call_id']),
          reason: _str(json['reason']),
          targetPart: json['target_part'] == null
              ? 'leaf'
              : _str(json['target_part']),
        );
      case 'tool.cancelled':
        return ToolCancelled(callIds: _strList(json['call_ids']));
      case 'photo.received':
        return PhotoReceived(
          photoId: _str(json['photo_id']),
          count: _int(json['count']),
        );
      case 'diagnosis.started':
        return DiagnosisStarted(caseId: _str(json['case_id']));
      case 'case.diagnosis':
        return CaseDiagnosis.fromJson(json);
      case 'chat.state':
        return ChatStateEvent(
          chatId: _str(json['chat_id']),
          phase: _str(json['phase']),
          selections: _map(json['selections'])
              .map((k, v) => MapEntry(k, v?.toString() ?? '')),
        );
      case 'chat.question':
        return ChatQuestion(
          chatId: _str(json['chat_id']),
          stepId: _str(json['step_id']),
          prompt: _str(json['prompt']),
          kind: _str(json['kind']),
          options: _list(json['options'])
              .map((e) => ChatOption(
                    id: _str(_map(e)['id']),
                    label: _str(_map(e)['label']),
                  ))
              .toList(growable: false),
        );
      case 'chat.step':
        return ChatStepAck(
          chatId: _str(json['chat_id']),
          stepId: _str(json['step_id']),
          optionId: _str(json['option_id']),
          value: _str(json['value']),
          label: _str(json['label']),
        );
      default:
        return UnknownEvent(type ?? '', json);
    }
  }
}

/// Partial transcription of the farmer's speech. Each event replaces the
/// previous partial for the current turn.
class SttPartial extends ServerEvent {
  const SttPartial({required this.text});
  final String text;
}

/// A streamed token of the agent's text reply. Tokens are appended to build the
/// agent's turn.
class LlmToken extends ServerEvent {
  const LlmToken({required this.token});
  final String token;
}

/// The agent finished the current spoken turn.
class TtsFinished extends ServerEvent {
  const TtsFinished();
}

/// Barge-in signal: the agent was interrupted. The client MUST flush audio
/// playback instantly and close the avatar mouth.
class AgentInterrupted extends ServerEvent {
  const AgentInterrupted();
}

/// A server-side error. Surfaced to the transcript as a system bubble.
/// The provider closed the conversation (Gemini Live session duration limit).
/// The client ends the session cleanly and offers a restart — memory makes
/// the next greeting continue where this one stopped.
/// An accurate farmer subtitle from the parallel Scribe STT: the backend
/// re-transcribed a finished turn's actual audio. `orig` is the rough text
/// as originally shown, so the client can patch exactly that bubble.
class SttCorrected extends ServerEvent {
  const SttCorrected({required this.text, required this.orig});

  final String text;
  final String orig;
}

class SessionExpired extends ServerEvent {
  const SessionExpired({required this.message});

  final String message;
}

class ErrorEvent extends ServerEvent {
  const ErrorEvent({required this.code, required this.message});
  final String code;
  final String message;
}

/// The model wants a photo of a plant part (Phase 5 opens the guided camera).
class ToolRequestPhoto extends ServerEvent {
  const ToolRequestPhoto({
    required this.callId,
    required this.reason,
    required this.targetPart,
  });
  final String callId;
  final String reason;
  final String targetPart;
}

/// Outstanding tool calls were cancelled (e.g. the camera request expired).
class ToolCancelled extends ServerEvent {
  const ToolCancelled({required this.callIds});
  final List<String> callIds;
}

/// The backend acknowledged an uploaded photo.
class PhotoReceived extends ServerEvent {
  const PhotoReceived({required this.photoId, required this.count});
  final String photoId;
  final int count;
}

/// The diagnosis pipeline started for a case.
class DiagnosisStarted extends ServerEvent {
  const DiagnosisStarted({required this.caseId});
  final String caseId;
}

/// The completed diagnosis verdict. Rendered as a card bubble in the transcript.
class CaseDiagnosis extends ServerEvent {
  const CaseDiagnosis({
    required this.caseId,
    required this.result,
    required this.summary,
    this.preparations = const [],
    this.photos = const [],
  });
  final String caseId;
  final DiagnosisResult result;
  final Map<String, dynamic> summary;

  /// Growz Agroapteka preparations for `result.likelyDisease` (contract
  /// addendum P2.1). Absent key (v1 backend) parses to `const []` so the
  /// card simply hides the section — fully backward-compatible.
  final List<Preparation> preparations;

  /// Photos the pipeline stored for this case (empty on a v1 backend —
  /// `photos` absent parses to `const []`, fully backward-compatible).
  final List<PhotoRef> photos;

  factory CaseDiagnosis.fromJson(Map<String, dynamic> json) => CaseDiagnosis(
    caseId: _str(json['case_id']),
    result: DiagnosisResult.fromJson(_map(json['result'])),
    summary: _map(json['summary']),
    preparations: _list(json['preparations'])
        .map((e) => Preparation.fromJson(_map(e)))
        .toList(growable: false),
    photos: _list(json['photos'])
        .map((e) => PhotoRef.fromJson(_map(e)))
        .toList(growable: false),
  );
}

/// Guided-flow phase + accumulated selections. Emitted right after the Live
/// session is up for a chat-bound session, on every phase transition, and
/// when the guide degrades on error.
class ChatStateEvent extends ServerEvent {
  const ChatStateEvent({
    required this.chatId,
    required this.phase,
    required this.selections,
  });
  final String chatId;

  /// `guide` | `symptom` | `general` | `consult` (v2,
  /// `docs/multichat_contract.md` §1.2). Only `consult` hides the guided
  /// options bar / clears the pending question; `symptom` and `general` are
  /// stored as-is and simply drive nothing else client-side (forward
  /// compatible with a v1 app, which stores the string harmlessly too).
  final String phase;

  /// Selection keys are always present, `""` when not yet made.
  final Map<String, String> selections;
}

/// One tappable option of a pending [ChatQuestion].
class ChatOption {
  const ChatOption({required this.id, required this.label});
  final String id;
  final String label;
}

/// The single pending guided-flow step. Replaces any previously shown
/// question — at most one is pending at a time.
class ChatQuestion extends ServerEvent {
  const ChatQuestion({
    required this.chatId,
    required this.stepId,
    required this.prompt,
    required this.kind,
    required this.options,
  });
  final String chatId;

  /// `query_type` | `crop` | `plant_part` | `symptom` | `photo` | `general` |
  /// `diag_offer` (v2, `docs/multichat_contract.md` §1.3). `symptom` and
  /// `general` are NEW pseudo/persistent steps; `diag_offer` is a NEW overlay
  /// question inside the general phase.
  final String stepId;
  final String prompt;

  /// `buttons` | `crop_picker` | `photo` | `symptom` | `free` — how to render
  /// the input surface. `symptom` (v2 NEW) renders identically to `buttons`;
  /// `free` (v2 NEW) renders the caption with no input widgets. Both fall
  /// back correctly through the `default:` buttons branch on an old client.
  final String kind;
  final List<ChatOption> options;
}

/// Acknowledges a step's answer was accepted and recorded, regardless of
/// whether it arrived by tap (`chat.answer`) or by voice (the server-side
/// `select_option` tool). The client clears the pending question and shows a
/// compact `✓ <label>` confirmation chip.
class ChatStepAck extends ServerEvent {
  const ChatStepAck({
    required this.chatId,
    required this.stepId,
    required this.optionId,
    required this.value,
    required this.label,
  });
  final String chatId;
  final String stepId;
  final String optionId;
  final String value;
  final String label;
}

/// Any frame with an unrecognised `type`. Ignored gracefully by consumers.
class UnknownEvent extends ServerEvent {
  const UnknownEvent(this.type, this.raw);
  final String type;
  final Map<String, dynamic> raw;
}

// ---- Nested diagnosis models ----------------------------------------------

/// One alternative diagnosis with a one-line justification.
class Differential {
  const Differential({required this.name, required this.why});
  final String name;
  final String why;

  factory Differential.fromJson(Map<String, dynamic> json) =>
      Differential(name: _str(json['name']), why: _str(json['why']));
}

/// The structured diagnosis result nested inside [CaseDiagnosis].
class DiagnosisResult {
  const DiagnosisResult({
    required this.likelyDisease,
    required this.confidence,
    required this.differentials,
    required this.immediateTreatment,
    required this.prevention,
    required this.spokenSummary,
    required this.language,
    this.isPlant = true,
  });

  /// False when the photo wasn't a plant (e.g. a keyboard). The disease fields
  /// are then empty and [spokenSummary] politely asks for a plant photo, so the
  /// card renders a neutral "not a plant" state instead of a diagnosis.
  /// Defaults to true (backward-compatible with payloads lacking `is_plant`).
  final bool isPlant;

  final String likelyDisease;

  /// `high` | `medium` | `low`.
  final String confidence;
  final List<Differential> differentials;
  final List<String> immediateTreatment;
  final List<String> prevention;
  final String spokenSummary;
  final String language;

  factory DiagnosisResult.fromJson(Map<String, dynamic> json) =>
      DiagnosisResult(
        isPlant: _bool(json['is_plant'], true),
        likelyDisease: _str(json['likely_disease']),
        confidence: _str(json['confidence']),
        differentials: _list(
          json['differentials'],
        ).map((e) => Differential.fromJson(_map(e))).toList(growable: false),
        immediateTreatment: _strList(json['immediate_treatment']),
        prevention: _strList(json['prevention']),
        spokenSummary: _str(json['spoken_summary']),
        language: _str(json['language']),
      );
}

/// One recommended preparation from the Growz Agroapteka catalogue, riding
/// `case.diagnosis.preparations` (contract addendum P2.1). A separate lookup
/// from the Gemini `DiagnosisResult` — different source, independently
/// fail-open (`[]` on any backend-side failure).
class Preparation {
  const Preparation({
    required this.name,
    required this.doseMin,
    required this.doseMax,
    required this.unit,
    required this.type,
    required this.description,
  });

  final String name;
  final double? doseMin; // null when Growz has no dose
  final double? doseMax;
  final String unit; // e.g. 'l/ga'; '' when absent
  final String type; // 'disease' | 'pest' | 'weed' | '' | anything else
  final String description;

  factory Preparation.fromJson(Map<String, dynamic> json) => Preparation(
    name: _str(json['name']),
    doseMin: _numOrNull(json['dose_min']),
    doseMax: _numOrNull(json['dose_max']),
    unit: _str(json['unit']),
    type: _str(json['type']),
    description: _str(json['description']),
  );
}

/// One photo the diagnosis pipeline stored for a case, riding
/// `case.diagnosis.photos`. `selected` marks the top-3 actually sent to the
/// model; the rest were kept but unused. [storedPath] is a full https CDN URL
/// when Spaces is on, but can be a SERVER-LOCAL path on fallback — render it
/// only when [isRenderable].
class PhotoRef {
  const PhotoRef({
    required this.photoId,
    required this.storedPath,
    required this.selected,
    required this.imageConfidence,
    required this.duplicateOf,
    required this.perImageAnalysis,
  });

  final String photoId;
  final String storedPath;      // '' when backend sends null
  final bool selected;
  final String imageConfidence; // 'ok' | 'low' | ''
  final String duplicateOf;     // '' when not a duplicate
  final Map<String, dynamic> perImageAnalysis; // {} when null

  /// True only for a real network URL — the only case mobile can display.
  bool get isRenderable => storedPath.startsWith('http');

  factory PhotoRef.fromJson(Map<String, dynamic> json) => PhotoRef(
    photoId: _str(json['photo_id']),
    storedPath: _str(json['stored_path']),
    selected: _bool(json['selected'], false),
    imageConfidence: _str(json['image_confidence']),
    duplicateOf: _str(json['duplicate_of']),
    perImageAnalysis: _map(json['per_image_analysis']),
  );
}

// ---------------------------------------------------------------------------
// Client -> Server
// ---------------------------------------------------------------------------

/// Base type for every JSON frame the client sends. [toJson] produces the wire
/// map that the socket layer serialises.
sealed class ClientEvent {
  const ClientEvent();

  /// The JSON map sent on the wire.
  Map<String, dynamic> toJson();
}

/// Opens a session. Sent once immediately after the socket connects, before any
/// mic audio.
class ChatStartRequest extends ClientEvent {
  const ChatStartRequest({
    this.language = defaultLanguage,
    this.userId,
    this.cropId,
    this.cropName,
    this.lat,
    this.lon,
    this.chatId,
  });
  /// Mic rate and TTS voice are SERVER settings now (2026-08-05) — the app
  /// no longer sends them. Only the language tag stays client-side.
  final String language;

  /// Stable per-install device id (see `core/identity/device_identity.dart`).
  /// Keys Alomat's per-farmer memory server-side; omitted when unknown.
  final String? userId;

  /// The crop the farmer picked before the call (real Growz UUID + Uzbek name).
  /// Enriches the conversation; omitted when the picker was skipped.
  final String? cropId;
  final String? cropName;

  /// Farmer GPS for local weather; omitted when permission was denied (the
  /// server then falls back to Tashkent).
  final double? lat;
  final double? lon;

  /// Binds this session to a stored chat (`features/chat/`). Empty/absent/
  /// unknown → a plain session, byte-identical to today's behaviour.
  final String? chatId;

  @override
  Map<String, dynamic> toJson() => {
    'type': 'chat.start',
    'language': language,
    if (userId != null && userId!.isNotEmpty) 'user_id': userId,
    if (cropId != null && cropId!.isNotEmpty) 'crop_id': cropId,
    if (cropName != null && cropName!.isNotEmpty) 'crop_name': cropName,
    if (lat != null && lon != null) 'lat': lat,
    if (lat != null && lon != null) 'lon': lon,
    if (chatId != null && chatId!.isNotEmpty) 'chat_id': chatId,
  };
}

/// Barge-in request. The client also flushes local playback immediately.
class UserInterrupt extends ClientEvent {
  const UserInterrupt();

  @override
  Map<String, dynamic> toJson() => {'type': 'user.interrupt'};
}

/// A typed farmer message — the quiet/noisy-environment fallback for voice.
/// The agent answers with voice + text exactly like a spoken turn.
class TextInputRequest extends ClientEvent {
  const TextInputRequest({required this.text});

  final String text;

  @override
  Map<String, dynamic> toJson() => {'type': 'text.input', 'value': text};
}

/// Phase 5: uploads a captured photo (base64) for a plant part. Defined now so
/// the protocol is complete; the camera UI that produces it lands in Phase 5.
/// The plant part is NOT sent: the server resolves it from what the model last
/// asked for via `tool.request_photo`, falling back to the interview's own
/// `plant_part`. It knows both, and one session can collect several different
/// shots while `plant_part` stays a single value — so echoing the app's guess
/// back only risked disagreeing with the request the server actually made.
class PhotoUploadRequest extends ClientEvent {
  /// Reworked 2026-08-05: the bytes are POSTed to `/photos` over REST first;
  /// this WS event carries only the returned public URL in `value`.
  const PhotoUploadRequest({
    required this.photoId,
    required this.value,
    this.chatId,
  });
  final String photoId;

  /// Public photo URL returned by `POST /photos`.
  final String value;

  /// Guards against stale events from another chat; omitted when unbound.
  final String? chatId;

  @override
  Map<String, dynamic> toJson() => {
    'type': 'photo.upload',
    if (chatId != null && chatId!.isNotEmpty) 'chat_id': chatId,
    'photo_id': photoId,
    'value': value,
  };
}

/// Sent when the farmer TAPS a guided-flow option (or picks a crop in the
/// sheet). A *spoken* answer instead reaches the server via the Live
/// `select_option` tool — there is no client event for that path.
class ChatAnswerRequest extends ClientEvent {
  const ChatAnswerRequest({
    required this.chatId,
    required this.stepId,
    this.optionId = '',
    this.value = '',
    this.cropId,
    this.cropName,
  });
  final String chatId, stepId, optionId, value;

  /// Crop step only: the picked crop travels as its own `crop: {id, name}`
  /// object, so `option_id` keeps naming the tapped button
  /// (`open_crop_picker` / a saved chip) instead of doubling as data.
  final String? cropId, cropName;

  @override
  Map<String, dynamic> toJson() => {
    'type': 'chat.answer',
    'chat_id': chatId,
    'step_id': stepId,
    'option_id': optionId,
    'value': value,
    if (cropId != null) 'crop': {'id': cropId, 'name': cropName ?? ''},
  };
}

// ---------------------------------------------------------------------------
// JSON coercion helpers (defensive against loose backend typing)
// ---------------------------------------------------------------------------

String _str(dynamic v) => v == null ? '' : v.toString();

bool _bool(dynamic v, bool fallback) => v is bool ? v : fallback;

int _int(dynamic v) =>
    v is num ? v.toInt() : (v is String ? int.tryParse(v) ?? 0 : 0);

Map<String, dynamic> _map(dynamic v) => v is Map
    ? v.map((k, val) => MapEntry(k.toString(), val))
    : <String, dynamic>{};

List<dynamic> _list(dynamic v) => v is List ? v : const [];

List<String> _strList(dynamic v) =>
    v is List ? v.map((e) => e.toString()).toList(growable: false) : const [];

double? _numOrNull(dynamic v) =>
    v is num ? v.toDouble() : (v is String ? double.tryParse(v) : null);
