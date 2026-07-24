/// REST models for the multichat feature — mirror `GET/POST /chats` and
/// `GET /chats/{id}` field names exactly (`docs/multichat_contract.md` §2).
library;

import '../../core/protocol/events.dart';
import 'strings.dart';

/// One turn of a chat's stored history (`messages[]` in `GET /chats/{id}`,
/// and the `last_message` summary field).
class ChatMessage {
  const ChatMessage({
    required this.role,
    required this.text,
    required this.ts,
    required this.kind,
  });

  /// `farmer` | `rais` | `system`.
  final String role;
  final String text;
  final DateTime? ts;

  /// `text` | `question` | `answer` | `photo` | `diagnosis` | `agronom_review`.
  final String kind;

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    role: (json['role'] ?? '') as String,
    text: (json['text'] ?? '') as String,
    ts: DateTime.tryParse((json['ts'] ?? '') as String? ?? ''),
    kind: (json['kind'] ?? '') as String,
  );
}

/// A blank, never-requested review — the default for [ChatSummary] fields
/// built without an `agronom_review` key (old backend / [ChatSummary.blank]).
const AgronomReview _noneAgronomReview = AgronomReview(
  status: 'none',
  requestedAt: null,
  reviewedAt: null,
  isMock: false,
  verdict: '',
  expertSummary: '',
  expertNotes: [],
  adjustedPreparations: [],
);

/// Spec §7 agronom review, mirrored from `agronom_review` on the chat
/// summary/detail (contract Phase 3, P3.1). `null` on the wire → [none].
class AgronomReview {
  const AgronomReview({
    required this.status,
    required this.requestedAt,
    required this.reviewedAt,
    required this.isMock,
    required this.verdict,
    required this.expertSummary,
    required this.expertNotes,
    required this.adjustedPreparations,
  });

  /// `none` | `pending` | `done`.
  final String status;
  final DateTime? requestedAt;
  final DateTime? reviewedAt;
  final bool isMock;

  /// `''` | `confirmed` | `adjusted`.
  final String verdict;
  final String expertSummary;
  final List<String> expertNotes;

  /// The same P2.1 [Preparation] shape. `[]` means the AI list stands.
  final List<Preparation> adjustedPreparations;

  factory AgronomReview.none() => _noneAgronomReview;

  factory AgronomReview.fromJson(Map<String, dynamic>? json) {
    if (json == null) return AgronomReview.none();
    return AgronomReview(
      status: (json['status'] ?? 'none') as String,
      requestedAt: DateTime.tryParse(
        (json['requested_at'] ?? '') as String? ?? '',
      ),
      reviewedAt: DateTime.tryParse(
        (json['reviewed_at'] ?? '') as String? ?? '',
      ),
      isMock: json['is_mock'] == true,
      verdict: (json['verdict'] ?? '') as String,
      expertSummary: (json['expert_summary'] ?? '') as String,
      expertNotes: (json['expert_notes'] as List<dynamic>? ?? const [])
          .map((e) => e.toString())
          .toList(growable: false),
      adjustedPreparations:
          (json['adjusted_preparations'] as List<dynamic>? ?? const [])
              .map((e) => Preparation.fromJson(e as Map<String, dynamic>))
              .toList(growable: false),
    );
  }
}

/// A chat as listed by `GET /chats` / returned by `POST /chats`, and the
/// shared prefix of `GET /chats/{id}` (see [ChatDetail]).
class ChatSummary {
  const ChatSummary({
    required this.id,
    required this.userId,
    required this.title,
    required this.queryType,
    required this.cropId,
    required this.cropName,
    required this.plantPart,
    required this.createdAt,
    required this.updatedAt,
    required this.finished,
    required this.messageCount,
    this.lastMessage,
    this.agronomReview = _noneAgronomReview,
  });

  final String id;
  final String userId;
  final String title;

  /// `""` | `disease_pest` | `weed`.
  final String queryType;
  final String cropId;
  final String cropName;

  /// `""` or one of the 9-value plant-part enum.
  final String plantPart;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final bool finished;
  final int messageCount;
  final ChatMessage? lastMessage;

  /// Spec §7 agronom verification state (contract Phase 3, P3.1). Absent key
  /// (old backend) parses to [AgronomReview.none] — fully backward compatible.
  final AgronomReview agronomReview;

  factory ChatSummary.fromJson(Map<String, dynamic> json) => ChatSummary(
    id: (json['id'] ?? '') as String,
    userId: (json['user_id'] ?? '') as String,
    title: (json['title'] ?? '') as String,
    queryType: (json['query_type'] ?? '') as String,
    cropId: (json['crop_id'] ?? '') as String,
    cropName: (json['crop_name'] ?? '') as String,
    plantPart: (json['plant_part'] ?? '') as String,
    createdAt: DateTime.tryParse((json['created_at'] ?? '') as String? ?? ''),
    updatedAt: DateTime.tryParse((json['updated_at'] ?? '') as String? ?? ''),
    finished: json['finished'] == true,
    messageCount: (json['message_count'] as num?)?.toInt() ?? 0,
    lastMessage: json['last_message'] == null
        ? null
        : ChatMessage.fromJson(json['last_message'] as Map<String, dynamic>),
    agronomReview: AgronomReview.fromJson(
      json['agronom_review'] as Map<String, dynamic>?,
    ),
  );

  /// A blank, unsaved chat used when `POST /chats` fails (fail-open, contract
  /// §5.2 step 3): [id] is empty, so `session.start` omits `chat_id` and the
  /// interview simply runs a plain, chatless voice session.
  factory ChatSummary.blank() => const ChatSummary(
    id: '',
    userId: '',
    title: S.newChatTitle,
    queryType: '',
    cropId: '',
    cropName: '',
    plantPart: '',
    createdAt: null,
    updatedAt: null,
    finished: false,
    messageCount: 0,
    lastMessage: null,
    agronomReview: _noneAgronomReview,
  );
}

/// The full `GET /chats/{id}` response: [ChatSummary]'s fields plus the
/// message history and last diagnosis.
class ChatDetail {
  const ChatDetail({
    required this.summary,
    required this.messages,
    this.lastDiagnosis,
  });

  final ChatSummary summary;
  final List<ChatMessage> messages;
  final Map<String, dynamic>? lastDiagnosis;

  factory ChatDetail.fromJson(Map<String, dynamic> json) => ChatDetail(
    // The detail payload is a superset of the summary shape, so this reuse
    // is exact — `message_count`/`last_message` are simply absent and
    // default to 0/null.
    summary: ChatSummary.fromJson(json),
    messages: (json['messages'] as List<dynamic>? ?? const [])
        .map((e) => ChatMessage.fromJson(e as Map<String, dynamic>))
        .toList(growable: false),
    lastDiagnosis: json['last_diagnosis'] as Map<String, dynamic>?,
  );
}
