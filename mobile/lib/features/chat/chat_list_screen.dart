/// Home screen: the device's chat list + "Yangi chat" to start a new guided
/// conversation. Replaces the old crop-gate as the app's landing screen —
/// see `lib/main.dart` (`docs/multichat_contract.md` §5.1–§5.3).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/identity/device_identity.dart';
import 'chat.dart';
import 'chat_providers.dart';
import 'chat_service.dart';
import 'strings.dart';

class ChatListScreen extends ConsumerWidget {
  const ChatListScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final chatsAsync = ref.watch(chatListProvider);

    return Scaffold(
      appBar: AppBar(title: const Text(S.homeTitle)),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: chatsAsync.when(
                loading: () =>
                    const Center(child: CircularProgressIndicator()),
                error: (e, _) => _ErrorState(
                  onRetry: () => ref.invalidate(chatListProvider),
                ),
                data: (chats) => chats.isEmpty
                    ? const _EmptyState()
                    : _ChatCards(
                        chats: chats,
                        onOpen: (c) => _openChat(context, ref, c),
                      ),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () => _newChat(ref),
                  style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 18),
                  ),
                  icon: const Icon(Icons.add),
                  label: const Text(S.newChat),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// `POST /chats` then mount the interview. On failure — fail-open — the
  /// interview still mounts with a blank (unsaved) chat: `session.start`
  /// omits `chat_id` and a plain, chatless voice session runs, exactly like
  /// before multichat existed. The button never dead-ends.
  Future<void> _newChat(WidgetRef ref) async {
    try {
      final userId = await getOrCreateDeviceId();
      final summary = await const ChatService().createChat(userId);
      ref.read(activeChatProvider.notifier).open(summary);
    } catch (_) {
      ref.read(activeChatProvider.notifier).open(ChatSummary.blank());
    }
  }

  /// `GET /chats/{id}` then mount the interview with the resumed history. On
  /// failure the chat list stays put and shows a snackbar.
  Future<void> _openChat(
    BuildContext context,
    WidgetRef ref,
    ChatSummary summary,
  ) async {
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => const Center(child: CircularProgressIndicator()),
    );
    try {
      final userId = await getOrCreateDeviceId();
      final detail = await const ChatService().getChat(summary.id, userId);
      if (!context.mounted) return;
      Navigator.of(context, rootNavigator: true).pop();
      ref
          .read(activeChatProvider.notifier)
          .open(detail.summary, history: detail.messages);
    } catch (_) {
      if (!context.mounted) return;
      Navigator.of(context, rootNavigator: true).pop();
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text(S.chatLoadFailed)));
    }
  }
}

class _ChatCards extends StatelessWidget {
  const _ChatCards({required this.chats, required this.onOpen});

  final List<ChatSummary> chats;
  final void Function(ChatSummary) onOpen;

  @override
  Widget build(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.all(12),
      itemCount: chats.length,
      itemBuilder: (context, i) => _ChatCard(chat: chats[i], onTap: onOpen),
    );
  }
}

class _ChatCard extends StatelessWidget {
  const _ChatCard({required this.chat, required this.onTap});

  final ChatSummary chat;
  final void Function(ChatSummary) onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final last = chat.lastMessage;
    final preview = last == null
        ? null
        : (last.text == S.photoMarker ? S.photoBubble : last.text);

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 16,
          vertical: 8,
        ),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Spec §7 (contract addendum P3.7): a finished agronom review
            // marks the tile on the list — no polling, this is simply what
            // the normal `GET /chats` fetch already carries.
            if (chat.agronomReview.status == 'done') ...[
              const Icon(Icons.verified_user, size: 16, color: Colors.green),
              const SizedBox(width: 4),
            ],
            Flexible(
              child: Text(
                chat.title,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ],
        ),
        subtitle: preview == null
            ? null
            : Text(
                preview,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
        trailing: Text(
          _dateLabel(chat.updatedAt),
          style: theme.textTheme.labelSmall?.copyWith(
            color: theme.colorScheme.outline,
          ),
        ),
        onTap: () => onTap(chat),
      ),
    );
  }

  static String _dateLabel(DateTime? dt) {
    if (dt == null) return '';
    final local = dt.toLocal();
    final now = DateTime.now();
    final today = DateTime(now.year, now.month, now.day);
    final that = DateTime(local.year, local.month, local.day);
    if (that == today) return S.today;
    if (that == today.subtract(const Duration(days: 1))) return S.yesterday;
    return '${_pad(local.day)}.${_pad(local.month)}.${local.year}';
  }

  static String _pad(int n) => n.toString().padLeft(2, '0');
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Text(
          S.emptyList,
          textAlign: TextAlign.center,
          style: theme.textTheme.bodyLarge?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      ),
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.cloud_off,
            size: 48,
            color: theme.colorScheme.onSurfaceVariant,
          ),
          const SizedBox(height: 12),
          Text(S.listLoadFailed, style: theme.textTheme.titleMedium),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onRetry, child: const Text(S.retry)),
        ],
      ),
    );
  }
}
