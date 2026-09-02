/// The single Phase-3 screen: avatar, live transcript and voice controls.
///
/// Top   — the [AvatarWebView] placeholder with a mic-level ring.
/// Middle— an auto-scrolling transcript of [TranscriptBubble]s.
/// Bottom— an always-available camera button and the big mic start/stop
///         button, plus a connection status chip.
///
/// When the backend asks for a photo (`tool.request_photo`) the screen does NOT
/// jump to the camera — that stalled the UI isolate mid-speech on budget phones.
/// Instead a "Rasm olish" CTA banner appears above the controls; the farmer taps
/// it to open the guided camera manually.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'dart:io';

import '../camera/camera_screen.dart';
import '../camera/confirm_overlay.dart';
import '../chat/chat_providers.dart';
import '../chat/guide_options_bar.dart';
import '../chat/strings.dart';
import '../diagnosis/agronom_card.dart';
import '../diagnosis/diagnosis_card.dart';
import '../session/app_mode.dart';
import '../session/photo_request_provider.dart';
import '../session/voice_session_controller.dart';
import 'avatar/avatar_image.dart';
import 'transcript_provider.dart';

/// Root screen of the app.
class InterviewScreen extends ConsumerStatefulWidget {
  const InterviewScreen({super.key});

  @override
  ConsumerState<InterviewScreen> createState() => _InterviewScreenState();
}

class _InterviewScreenState extends ConsumerState<InterviewScreen> {
  final ScrollController _scroll = ScrollController();
  final TextEditingController _textCtrl = TextEditingController();

  /// Typed-message mode: shows the text field row above the controls. A
  /// fallback for places where speaking is impractical (noise, night, exact
  /// data like phone numbers) — voice stays the primary input.
  bool _textMode = false;

  @override
  void initState() {
    super.initState();
    // Call model: the conversation starts by itself when the app opens —
    // Alomat greets first (memory kickoff) with no taps needed. The farmer
    // answers by holding the push-to-talk button.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(voiceSessionProvider.notifier).start();
    });
  }

  @override
  void dispose() {
    _scroll.dispose();
    _textCtrl.dispose();
    super.dispose();
  }

  void _sendTyped() {
    final t = _textCtrl.text.trim();
    if (t.isEmpty) return;
    ref.read(voiceSessionProvider.notifier).sendText(t);
    _textCtrl.clear();
  }

  void _autoScroll() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 220),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(voiceSessionProvider);
    final bubbles = ref.watch(transcriptProvider);
    final mode = ref.watch(appModeProvider);
    final pending = ref.watch(pendingPhotoRequestProvider);

    // Auto-scroll on new bubbles.
    ref.listen<List<TranscriptBubble>>(transcriptProvider, (prev, next) {
      if ((prev?.length ?? 0) != next.length) _autoScroll();
    });

    final live = session.state == SessionState.live;
    final busy = session.state == SessionState.connecting;

    // Interview: the avatar is a full-width band. It's ~35% on the empty start
    // screen, then shrinks to ~20% once the conversation begins so the
    // transcript / chips / input get the room. Camera/Confirm (Phase 5): it
    // shrinks to a 96 px circle, bottom-right.
    final pip = mode is! InterviewMode;

    return Scaffold(
      body: SafeArea(
        child: LayoutBuilder(
          builder: (context, constraints) {
            final w = constraints.maxWidth;
            final h = constraints.maxHeight;
            const pipSide = 96.0;
            const pipInset = 16.0;
            final fullH = h * (bubbles.isEmpty ? 0.35 : 0.20);
            final avatarW = pip ? pipSide : w;
            final avatarH = pip ? pipSide : fullH;
            final left = pip ? w - pipSide - pipInset : 0.0;
            final top = pip ? h - pipSide - pipInset : 0.0;
            final radius = pip ? pipSide / 2 : 24.0;

            return Stack(
              children: [
                // Main content. In interview mode it reserves the avatar's top
                // band; in Camera/Confirm modes the guided-camera pipeline fills
                // the screen and the avatar floats over it as a PiP. The camera
                // widgets render UNDER the (always-mounted) avatar below.
                Positioned.fill(
                  child: switch (mode) {
                    InterviewMode() => Column(
                      children: [
                        SizedBox(height: pip ? 0 : fullH),
                        _StatusChip(
                          state: session.state,
                          error: session.errorMessage,
                        ),
                        const Divider(height: 1),
                        Expanded(
                          child: bubbles.isEmpty
                              ? _EmptyHint(state: session.state)
                              : ListView.builder(
                                  controller: _scroll,
                                  padding: const EdgeInsets.all(12),
                                  itemCount: bubbles.length,
                                  itemBuilder: (_, i) =>
                                      _BubbleView(bubble: bubbles[i]),
                                ),
                        ),
                        // Manual-capture CTA: when a photo is pending the farmer
                        // taps here to open the camera (never auto-switched).
                        if (pending != null)
                          _PhotoCtaBanner(
                            targetPart: pending.targetPart,
                            onTap: () => ref
                                .read(appModeProvider.notifier)
                                .toCamera(
                                  callId: pending.callId,
                                  targetPart: pending.targetPart,
                                  reason: pending.reason,
                                ),
                            onDismiss: () {
                              // camera.cancelled left the protocol
                              // (2026-08-05) — dismissing is local-only.
                              ref
                                  .read(pendingPhotoRequestProvider.notifier)
                                  .clear();
                            },
                          ),
                        // Guided-flow options (chips / crop sheet / photo
                        // buttons) for the pending `chat.question`, if any —
                        // hides itself entirely once the guide has none. Kept
                        // ABOVE the text input so answer chips (e.g. the
                        // trigger-offer «Ha, aniqlaymiz») sit right under the
                        // transcript, not below the composer.
                        const GuideOptionsBar(),
                        if (live && _textMode)
                          Padding(
                            padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                            child: Row(
                              children: [
                                Expanded(
                                  child: TextField(
                                    controller: _textCtrl,
                                    minLines: 1,
                                    maxLines: 3,
                                    textInputAction: TextInputAction.send,
                                    onSubmitted: (_) => _sendTyped(),
                                    decoration: InputDecoration(
                                      hintText: 'Type a message…',
                                      isDense: true,
                                      contentPadding:
                                          const EdgeInsets.symmetric(
                                            horizontal: 16,
                                            vertical: 10,
                                          ),
                                      border: OutlineInputBorder(
                                        borderRadius: BorderRadius.circular(
                                          22,
                                        ),
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 8),
                                IconButton.filled(
                                  onPressed: _sendTyped,
                                  tooltip: 'Send',
                                  icon: const Icon(Icons.send),
                                ),
                              ],
                            ),
                          ),
                        _Controls(
                          live: live,
                          busy: busy,
                          pttHeld: session.pttHeld,
                          textMode: _textMode,
                          onToggleText: live
                              ? () => setState(() => _textMode = !_textMode)
                              : null,
                          muted: session.agentMuted,
                          onToggleMute: live
                              ? () => ref
                                    .read(voiceSessionProvider.notifier)
                                    .toggleAgentMute()
                              : null,
                          onPtt: (held) => ref
                              .read(voiceSessionProvider.notifier)
                              .setPttHeld(held),
                          onStart: () =>
                              ref.read(voiceSessionProvider.notifier).start(),
                          // End the call and return home. `stop()` itself
                          // clears the active chat (+ crop/guide state), which
                          // is what the top-level gate watches to remount the
                          // chat list (`main.dart`).
                          onEnd: () =>
                              ref.read(voiceSessionProvider.notifier).stop(),
                          // The camera is ALWAYS one tap away: with a pending
                          // AI request it opens for that part; otherwise the
                          // farmer opens it on their own initiative.
                          onCamera: live
                              ? () => ref
                                    .read(appModeProvider.notifier)
                                    .toCamera(
                                      callId: pending?.callId ?? '',
                                      targetPart:
                                          pending?.targetPart ?? 'whole_plant',
                                      reason: pending?.reason ?? '',
                                    )
                              : null,
                        ),
                      ],
                    ),
                    CameraMode(
                      :final callId,
                      :final targetPart,
                      :final reason,
                    ) => CameraScreen(
                      callId: callId,
                      targetPart: targetPart,
                      reason: reason,
                    ),
                    ConfirmMode(
                      :final path,
                      :final targetPart,
                      :final origin,
                    ) => ConfirmOverlay(
                      path: path,
                      targetPart: targetPart,
                      origin: origin,
                    ),
                  },
                ),
                // The avatar itself — one persistent instance; the animated
                // container only resizes/repositions it, never remounts it.
                AnimatedPositioned(
                  duration: const Duration(milliseconds: 320),
                  curve: Curves.easeInOut,
                  left: left,
                  top: top,
                  width: avatarW,
                  height: avatarH,
                  // Hidden during the photo phase (camera/confirm): the farmer
                  // asked for no avatar on the camera screen. Opacity 0 (rather
                  // than unmounting) keeps the WebView alive so the GLB never
                  // reloads; IgnorePointer stops the invisible box eating taps
                  // over the camera preview.
                  child: IgnorePointer(
                    ignoring: pip,
                    child: AnimatedOpacity(
                      duration: const Duration(milliseconds: 180),
                      opacity: pip ? 0.0 : 1.0,
                      child: AnimatedContainer(
                        duration: const Duration(milliseconds: 320),
                        curve: Curves.easeInOut,
                        clipBehavior: Clip.antiAlias,
                        decoration: BoxDecoration(
                          borderRadius: BorderRadius.circular(radius),
                        ),
                        child: AvatarView(speaking: live),
                      ),
                    ),
                  ),
                ),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _EmptyHint extends StatelessWidget {
  const _EmptyHint({required this.state});

  final SessionState state;

  @override
  Widget build(BuildContext context) {
    final (title, body) = switch (state) {
      SessionState.connecting => (
        'Starting conversation…',
        'Just a moment.',
      ),
      SessionState.live => (
        'Ready to listen',
        'Hold the button below and speak. '
            'Release to let Alomat reply.',
      ),
      _ => (
        'Conversation ended',
        'Tap "Reconnect" to start a new session.',
      ),
    };
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              title,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 12),
            Text(
              body,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Theme.of(context).colorScheme.outline,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.state, this.error});

  final SessionState state;
  final String? error;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    late final String label;
    late final Color color;
    switch (state) {
      case SessionState.disconnected:
        label = 'Disconnected';
        color = theme.colorScheme.outline;
      case SessionState.connecting:
        label = 'Connecting…';
        color = Colors.amber;
      case SessionState.live:
        label = 'Live';
        color = Colors.greenAccent;
      case SessionState.error:
        label = error ?? 'Error';
        color = theme.colorScheme.error;
    }
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(Icons.circle, size: 10, color: color),
          const SizedBox(width: 6),
          Text(
            label,
            style: theme.textTheme.labelMedium?.copyWith(color: color),
          ),
        ],
      ),
    );
  }
}

class _BubbleView extends ConsumerWidget {
  const _BubbleView({required this.bubble});

  final TranscriptBubble bubble;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    switch (bubble) {
      case FarmerBubble(:final text, :final isFinal):
        return _Chat(
          text: text,
          isMe: true,
          faded: !isFinal,
          color: theme.colorScheme.primaryContainer,
          textColor: theme.colorScheme.onPrimaryContainer,
        );
      case AgentBubble(:final text, :final isFinal):
        return _Chat(
          text: text,
          isMe: false,
          faded: !isFinal,
          color: theme.colorScheme.surfaceContainerHighest,
          textColor: theme.colorScheme.onSurface,
        );
      case SystemBubble(:final text):
        return Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Center(
            child: Text(
              text,
              textAlign: TextAlign.center,
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.outline,
              ),
            ),
          ),
        );
      case DiagnosisCardBubble(:final result, :final preparations, :final photos):
        // Spec §7 (contract addendum P3.6/P3.7): the button/status renders
        // only for a chat-bound session (`activeChatProvider` holds a
        // non-empty id — the exact id sent in `session.start.chat_id`).
        final active = ref.watch(activeChatProvider);
        final chatBound = active != null && active.summary.id.isNotEmpty;
        final status = !chatBound
            ? ''
            : (ref.watch(agronomReviewProvider)?.status ?? 'none');
        return DiagnosisCard(
          result: result,
          preparations: preparations,
          photos: photos,
          agronomStatus: status,
          onAgronomRequest: status != 'none'
              ? null
              : () async {
                  final ok = await ref
                      .read(agronomReviewProvider.notifier)
                      .request(active!.summary.id);
                  if (!ok && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text(S.agronomRequestFailed),
                      ),
                    );
                  }
                },
        );
      case AgronomCardBubble(:final review):
        return AgronomCard(review: review);
      case ImageBubble(:final path):
        return _ImageThumb(path: path);
    }
  }
}

/// An uploaded-photo thumbnail, shown on the farmer (right) side.
class _ImageThumb extends StatelessWidget {
  const _ImageThumb({required this.path});

  final String path;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        constraints: const BoxConstraints(maxWidth: 200, maxHeight: 200),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(14),
          child: Image.file(
            File(path),
            fit: BoxFit.cover,
            errorBuilder: (_, _, _) => Container(
              width: 120,
              height: 120,
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              child: const Icon(Icons.image_not_supported_outlined),
            ),
          ),
        ),
      ),
    );
  }
}

class _Chat extends StatelessWidget {
  const _Chat({
    required this.text,
    required this.isMe,
    required this.faded,
    required this.color,
    required this.textColor,
  });

  final String text;
  final bool isMe;
  final bool faded;
  final Color color;
  final Color textColor;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: isMe ? Alignment.centerRight : Alignment.centerLeft,
      child: Opacity(
        opacity: faded ? 0.65 : 1,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.78,
          ),
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(14),
          ),
          child: Text(text, style: TextStyle(color: textColor)),
        ),
      ),
    );
  }
}

/// Bottom control row. The session is a phone call that stays alive on its
/// own; these controls never restart it by accident:
///
///  - connecting:   [camera·off] [        Ulanmoqda…        ]
///  - live:         [camera] [ PTT: hold to stream the mic ] [end-call]
///  - disconnected: [camera·off] [      Qayta boshlash      ]
///
/// The PTT pill uses a raw [Listener] (not GestureDetector long-press) so the
/// mic opens the instant the finger lands — the gesture arena's ~500 ms
/// long-press delay is unusable for walkie-talkie speech.
class _Controls extends StatelessWidget {
  const _Controls({
    required this.live,
    required this.busy,
    required this.pttHeld,
    required this.onPtt,
    required this.onStart,
    required this.onEnd,
    this.onCamera,
    this.onToggleText,
    this.textMode = false,
    this.onToggleMute,
    this.muted = false,
  });

  final bool live;
  final bool busy;
  final bool pttHeld;
  final ValueChanged<bool> onPtt;
  final VoidCallback onStart;
  final VoidCallback onEnd;

  /// Mute/unmute the agent's spoken voice. Null while offline (disabled).
  final VoidCallback? onToggleMute;

  /// Whether the agent voice is currently muted (highlights the toggle).
  final bool muted;

  /// Opens the guided camera. Null while the session is offline (the photo
  /// needs a live socket to upload), which renders the button disabled.
  final VoidCallback? onCamera;

  /// Shows/hides the typed-message row. Null while offline (disabled).
  final VoidCallback? onToggleText;

  /// Whether the typed-message row is currently open (highlights the toggle).
  final bool textMode;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
      child: Row(
        children: [
          // Always-present camera: the farmer photographs the plant whenever
          // they want, not only when the AI asks (the CTA banner still
          // highlights AI requests with the right part hint).
          IconButton.filledTonal(
            onPressed: onCamera,
            iconSize: 28,
            padding: const EdgeInsets.all(16),
            tooltip: 'Take a photo',
            icon: const Icon(Icons.photo_camera),
          ),
          const SizedBox(width: 8),
          // Text-chat fallback for when speaking is impractical.
          IconButton.filledTonal(
            onPressed: onToggleText,
            iconSize: 24,
            padding: const EdgeInsets.all(14),
            tooltip: 'Type a message',
            isSelected: textMode,
            icon: Icon(textMode ? Icons.keyboard_hide : Icons.keyboard),
          ),
          const SizedBox(width: 8),
          // Mute/unmute Rais's voice — text keeps flowing, only audio is off.
          IconButton.filledTonal(
            onPressed: onToggleMute,
            iconSize: 24,
            padding: const EdgeInsets.all(14),
            tooltip: muted ? 'Unmute agent' : 'Mute agent',
            isSelected: muted,
            icon: Icon(muted ? Icons.volume_off : Icons.volume_up),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: live
                ? _PttButton(held: pttHeld, onPtt: onPtt)
                : FilledButton.icon(
                    onPressed: busy ? null : onStart,
                    style: FilledButton.styleFrom(
                      padding: const EdgeInsets.symmetric(vertical: 18),
                    ),
                    icon: busy
                        ? const SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.refresh),
                    label: Text(busy ? 'Connecting…' : 'Reconnect'),
                  ),
          ),
          if (live) ...[
            const SizedBox(width: 12),
            IconButton.filled(
              onPressed: onEnd,
              iconSize: 24,
              padding: const EdgeInsets.all(14),
              tooltip: 'End call',
              style: IconButton.styleFrom(
                backgroundColor: theme.colorScheme.errorContainer,
                foregroundColor: theme.colorScheme.onErrorContainer,
              ),
              icon: const Icon(Icons.call_end),
            ),
          ],
        ],
      ),
    );
  }
}

/// The hold-to-talk pill. Pointer down → mic streams (+ instant barge-in if
/// the agent is mid-speech); pointer up/cancel → mic closes. Haptics mark
/// both edges so the farmer feels the mic state without looking.
class _PttButton extends StatelessWidget {
  const _PttButton({required this.held, required this.onPtt});

  final bool held;
  final ValueChanged<bool> onPtt;

  void _down() {
    HapticFeedback.mediumImpact();
    onPtt(true);
  }

  void _up() {
    HapticFeedback.lightImpact();
    onPtt(false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Listener(
      onPointerDown: (_) => _down(),
      onPointerUp: (_) => _up(),
      onPointerCancel: (_) => _up(),
      child: AnimatedScale(
        scale: held ? 1.04 : 1.0,
        duration: const Duration(milliseconds: 120),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          padding: const EdgeInsets.symmetric(vertical: 18),
          decoration: BoxDecoration(
            color: held ? theme.colorScheme.error : theme.colorScheme.primary,
            borderRadius: BorderRadius.circular(28),
          ),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.center,
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                held ? Icons.mic : Icons.mic_none,
                size: 30,
                color: held
                    ? theme.colorScheme.onError
                    : theme.colorScheme.onPrimary,
              ),
              // Icon-only when idle; while held a short "Gapiring…" confirms
              // the mic is live (color alone can be missed in sunlight).
              if (held) ...[
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    'Speak…',
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      color: theme.colorScheme.onError,
                      fontWeight: FontWeight.w700,
                      fontSize: 16,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The manual-capture call-to-action shown while a `request_photo` is pending.
///
/// Tapping the banner opens the guided camera (exactly what the old auto-switch
/// did); the trailing ✕ dismisses the request (local clear only).
///
/// Static by design: a *single* one-shot scale-in ([TweenAnimationBuilder],
/// ≤300 ms, runs once on appearance) draws the eye, but there is NO repeating
/// animation controller anywhere on the interview screen — a continuous pulse
/// kept the UI isolate busy and scrambled audio on a Helio G85.
class _PhotoCtaBanner extends StatelessWidget {
  const _PhotoCtaBanner({
    required this.targetPart,
    required this.onTap,
    required this.onDismiss,
  });

  final String targetPart;
  final VoidCallback onTap;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
      // One-shot entrance: animates once from 0.96→1.0 on first build, then
      // holds — no controller, no repeat.
      child: TweenAnimationBuilder<double>(
        tween: Tween<double>(begin: 0.96, end: 1.0),
        duration: const Duration(milliseconds: 260),
        curve: Curves.easeOut,
        builder: (context, scale, child) =>
            Transform.scale(scale: scale, child: child),
        child: Material(
          color: theme.colorScheme.primaryContainer,
          borderRadius: BorderRadius.circular(18),
          clipBehavior: Clip.antiAlias,
          child: InkWell(
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 8, 12),
              child: Row(
                children: [
                  Icon(
                    Icons.photo_camera,
                    size: 30,
                    color: theme.colorScheme.onPrimaryContainer,
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          'Take a photo',
                          style: theme.textTheme.titleMedium?.copyWith(
                            color: theme.colorScheme.onPrimaryContainer,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          _partHint(targetPart),
                          style: theme.textTheme.bodySmall?.copyWith(
                            color: theme.colorScheme.onPrimaryContainer
                                .withValues(alpha: 0.85),
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    onPressed: onDismiss,
                    tooltip: 'Dismiss',
                    color: theme.colorScheme.onPrimaryContainer.withValues(
                      alpha: 0.7,
                    ),
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// One-line, farmer-friendly instruction for the requested plant part.
  static String _partHint(String targetPart) {
    switch (targetPart) {
      case 'leaf':
        return 'Photograph the leaf';
      case 'fruit':
        return 'Photograph the fruit';
      case 'stem':
      case 'branch':
      case 'bark':
        return 'Photograph the stem';
      case 'root':
        return 'Photograph the root';
      case 'soil':
        return 'Photograph the soil';
      case 'flower':
        return 'Photograph the flower';
      case 'whole_plant':
        return 'Photograph the whole plant';
      default:
        return 'Photograph the plant';
    }
  }
}
