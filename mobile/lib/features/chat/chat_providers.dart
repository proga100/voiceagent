/// Riverpod state for the multichat + guided-flow UI
/// (`docs/multichat_contract.md` §5.5).
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/identity/device_identity.dart';
import '../../core/protocol/events.dart';
import 'chat.dart';
import 'chat_service.dart';

/// The device's chats (`GET /chats`), newest-updated first. Invalidate
/// (`ref.invalidate(chatListProvider)`) to refresh — e.g. after a call ends.
final chatListProvider = FutureProvider<List<ChatSummary>>((ref) async {
  final userId = await getOrCreateDeviceId();
  return const ChatService().listChats(userId);
});

/// The chat the interview is currently bound to, plus its preloaded history
/// (empty for a brand-new chat). `null` means "no active chat" — the
/// top-level gate then shows the chat list (`lib/main.dart`).
class ActiveChat {
  const ActiveChat({required this.summary, this.history = const []});
  final ChatSummary summary;
  final List<ChatMessage> history;
}

class ActiveChatController extends Notifier<ActiveChat?> {
  @override
  ActiveChat? build() => null;

  /// Mounts the interview bound to [summary]. [history] preloads the
  /// transcript for a resumed chat; omit for a brand-new one.
  void open(ChatSummary summary, {List<ChatMessage> history = const []}) =>
      state = ActiveChat(summary: summary, history: history);

  /// Returns home to the chat list.
  void close() => state = null;
}

final activeChatProvider = NotifierProvider<ActiveChatController, ActiveChat?>(
  ActiveChatController.new,
);

/// The pending guided question (from `chat.question`), `null` when none is
/// outstanding — the interview's [GuideOptionsBar] hides itself accordingly.
class GuideQuestionController extends Notifier<ChatQuestion?> {
  @override
  ChatQuestion? build() => null;

  void set(ChatQuestion question) => state = question;
  void clear() => state = null;
}

final guideQuestionProvider =
    NotifierProvider<GuideQuestionController, ChatQuestion?>(
      GuideQuestionController.new,
    );

/// `'guide'` | `'consult'` | `null` (no bound chat) — mirrors `chat.state`.
class GuidePhaseController extends Notifier<String?> {
  @override
  String? build() => null;

  void set(String phase) => state = phase;
  void clear() => state = null;
}

final guidePhaseProvider = NotifierProvider<GuidePhaseController, String?>(
  GuidePhaseController.new,
);

/// The guide's accumulated selections (`step_id` → accepted `option_id`),
/// mirrored from `chat.state.selections` and kept fresh by every accepted
/// `chat.step` in between (`chat.state` itself is only re-emitted at phase
/// boundaries — see contract §1.2/§1.4). Used by the guided-flow photo step
/// to target the camera at the plant part the farmer already picked.
class GuideSelectionsController extends Notifier<Map<String, String>> {
  @override
  Map<String, String> build() => const {};

  void setAll(Map<String, String> selections) => state = selections;
  void put(String stepId, String optionId) =>
      state = {...state, stepId: optionId};
  void clear() => state = const {};
}

final guideSelectionsProvider =
    NotifierProvider<GuideSelectionsController, Map<String, String>>(
      GuideSelectionsController.new,
    );

/// The bound chat's agronom review state (contract Phase 3, P3.7). Seeded
/// from the chat summary on open; updated by a successful request. `null` =
/// nothing known yet (treated as `'none'` for a chat-bound session).
class AgronomReviewController extends Notifier<AgronomReview?> {
  @override
  AgronomReview? build() => null;

  void set(AgronomReview review) => state = review;
  void clear() => state = null;

  /// `POST /chats/{id}/agronom-request`. Returns false on ANY failure
  /// (fail-open — the caller shows `S.agronomRequestFailed` and nothing else
  /// changes; the button stays available).
  Future<bool> request(String chatId) async {
    try {
      final userId = await getOrCreateDeviceId();
      final summary = await const ChatService().requestAgronomReview(
        chatId,
        userId,
      );
      state = summary.agronomReview;
      return true;
    } catch (_) {
      return false;
    }
  }
}

final agronomReviewProvider =
    NotifierProvider<AgronomReviewController, AgronomReview?>(
      AgronomReviewController.new,
    );
