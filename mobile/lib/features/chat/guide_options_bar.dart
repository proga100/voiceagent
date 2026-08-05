/// Renders the pending guided-flow question (`chat.question`) between the
/// transcript and the controls row of the interview screen
/// (`docs/multichat_contract.md` §5.4).
///
/// The input surface matches `chat.question.kind`:
///  * `buttons`     — tappable chips, one per option. Also covers the v2
///    `diag_offer` step and the 3-option `query_type` step — no extra code
///    needed, they are plain buttons.
///  * `crop_picker` — v2: memory-crop quick-pick chips (any option whose
///    `id != "open_crop_picker"`) rendered as answer chips BEFORE the button
///    that opens the reusable [CropPickList] in a modal bottom sheet; the
///    picked crop is the answer either way.
///  * `photo`       — options are payload-driven: the `take_photo` option
///    opens the existing guided camera (camera icon); every other option
///    (`done_photos`, `skip`, …) renders as a generic answer chip with its
///    payload label. The server re-emits this question after each accepted
///    photo (multi-photo loop) and stops at the server-side cap.
///  * `symptom`     — v2 NEW: identical rendering to `buttons` (the "Rasmga
///    oʻtish" chip); this bar persists across many voice turns instead of
///    being answered once.
///  * `free`        — v2 NEW: caption only, no input surface (the general-
///    phase hint and the farmer talks/types instead).
///
/// A tap sends `chat.answer`; VOICE stays fully usable at every step — a
/// spoken answer is captured server-side (the `select_option`/`to_photo`/
/// `switch_to_diagnostic` Live tools) and comes back as the same `chat.step`
/// that a tap produces, so this bar disappears identically either way. The
/// bar hides itself entirely when there is no pending question
/// (`guideQuestionProvider == null`).
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/protocol/events.dart';
import '../crop/crop.dart';
import '../crop/crop_pick_screen.dart';
import '../crop/crop_providers.dart';
import '../session/app_mode.dart';
import '../session/voice_session_controller.dart';
import 'chat_providers.dart';
import 'strings.dart';

/// A `buttons` question with more options than this renders the first few
/// inline + a «Boshqa» sheet for the rest (progressive disclosure). Short sets
/// (query_type=3, diag_offer=2, plant_part stays inline only if it ever shrinks)
/// render fully inline; the 9-option plant-part step trips this.
const int _kInlineButtonLimit = 6;

class GuideOptionsBar extends ConsumerStatefulWidget {
  const GuideOptionsBar({super.key});

  @override
  ConsumerState<GuideOptionsBar> createState() => _GuideOptionsBarState();
}

class _GuideOptionsBarState extends ConsumerState<GuideOptionsBar> {
  /// The step id of the last tapped/submitted answer — disables the bar
  /// (visually) until the matching `chat.step` or a new `chat.question`
  /// arrives, so a slow round-trip can't be double-sent. A `chat.step` ack
  /// clears [guideQuestionProvider] entirely (this widget unmounts its input
  /// for a beat).
  ///
  /// The multi-photo loop breaks the old "the *next* question is always for a
  /// different step_id" assumption: after an accepted photo the server RE-EMITS
  /// the SAME `step_id:"photo"` question, and [GuideOptionsBar] is a persistent
  /// widget that is never guaranteed to unmount, so comparing step ids alone
  /// would leave the re-asked bar disabled forever. We therefore latch on the
  /// question *identity* too: [_lastLatchedQuestion] is the exact instance we
  /// tapped, and a re-emitted `chat.question` is a fresh [ChatQuestion] object
  /// (events.dart `fromJson`), so `identical()` re-enables the bar precisely
  /// when the server re-asks, while a slow ack for the tapped instance stays
  /// disabled.
  String? _sentForStepId;
  ChatQuestion? _lastLatchedQuestion;

  @override
  Widget build(BuildContext context) {
    final question = ref.watch(guideQuestionProvider);
    if (question == null) return const SizedBox.shrink();

    final theme = Theme.of(context);
    // Multi-photo loop: the server legally re-asks the SAME step after a
    // chat.step ack cleared the provider. Re-arm when the question object is a
    // fresh instance for the step we latched — a re-emitted chat.question is a
    // new ChatQuestion, so identical() is false and the bar re-enables.
    final disabled =
        identical(question, _lastLatchedQuestion) &&
        _sentForStepId == question.stepId;

    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            question.prompt,
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 6),
          _buildInput(question, disabled),
        ],
      ),
    );
  }

  Widget _buildInput(ChatQuestion question, bool disabled) {
    switch (question.kind) {
      case 'crop_picker':
        // §1.1 «Bu ekin profilingizda bormi?»: the «Ha»/«Yoʻq» logic is
        // SERVER-driven now — buttons render whatever options arrive.
        //   saved_crops       → plain answer; the server re-sends this same
        //                       question with the profile chips as options
        //   open_crop_picker  → opens the full-catalogue sheet (client UI)
        //   anything else     → a profile chip: answer with the crop object
        // Neither path touches `selectedCropProvider` here — the server owns
        // the crop on the ChatDoc (the catalogue sheet still mirrors it).
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final o in question.options)
              FilledButton.tonal(
                onPressed: disabled
                    ? null
                    : () {
                        if (o.id == 'open_crop_picker') {
                          _openCropPicker(question);
                        } else if (o.id == 'saved_crops') {
                          _answer(question, optionId: o.id);
                        } else {
                          _answer(
                            question,
                            optionId: o.id,
                            cropId: o.id,
                            cropName: o.label,
                          );
                        }
                      },
                child: Text(o.label),
              ),
          ],
        );
      case 'photo':
        // Server-driven: options ride the chat.question payload (take_photo /
        // done_photos / skip). "take_photo" opens the guided camera; every other
        // id is answered generically with the payload label — no hard-coded ids
        // or S.* labels, so the backend can evolve the option set freely.
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final o in question.options)
              if (o.id == 'take_photo')
                FilledButton.tonalIcon(
                  onPressed: disabled ? null : () => _takePhoto(question),
                  icon: const Icon(Icons.photo_camera_outlined),
                  label: Text(o.label),
                )
              else
                FilledButton.tonal(
                  onPressed: disabled ? null : () => _answer(question, optionId: o.id),
                  child: Text(o.label),
                ),
          ],
        );
      case 'symptom':
        // v2 NEW (§1.3 c): identical rendering to `buttons` — this bar just
        // persists on screen across many voice turns instead of being
        // answered once. Tapping «Rasmga oʻtish» sends
        // `chat.answer{step_id:"symptom", option_id:"to_photo", value:""}`.
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final o in question.options)
              FilledButton.tonal(
                onPressed: disabled
                    ? null
                    : () => _answer(question, optionId: o.id),
                child: Text(o.label),
              ),
          ],
        );
      case 'free':
        // v2 NEW (§1.3 e): the general-phase hint is caption-only — no input
        // widgets; the farmer talks (PTT) or types (existing text fallback).
        return const SizedBox.shrink();
      case 'buttons':
      default:
        // Progressive disclosure: a long option set (e.g. the 9 plant parts)
        // eats too much vertical space as chips. Show the first few common
        // options inline and tuck the long tail behind a «Boshqa» button that
        // opens a sheet. Short sets (query_type, diag_offer) stay fully inline.
        const inlineCount = 3;
        if (question.options.length > _kInlineButtonLimit) {
          final inline = question.options.take(inlineCount).toList();
          final overflow = question.options.skip(inlineCount).toList();
          return Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final o in inline)
                FilledButton.tonal(
                  onPressed:
                      disabled ? null : () => _answer(question, optionId: o.id),
                  child: Text(o.label),
                ),
              FilledButton.tonalIcon(
                onPressed: disabled
                    ? null
                    : () => _openOptionsSheet(
                        question,
                        overflow,
                        question.prompt,
                      ),
                icon: const Icon(Icons.more_horiz),
                label: const Text(S.optMore),
              ),
            ],
          );
        }
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final o in question.options)
              FilledButton.tonal(
                onPressed: disabled
                    ? null
                    : () => _answer(question, optionId: o.id),
                child: Text(o.label),
              ),
          ],
        );
    }
  }

  void _answer(
    ChatQuestion question, {
    required String optionId,
    String value = '',
    String? cropId,
    String? cropName,
  }) {
    setState(() {
      _sentForStepId = question.stepId;
      _lastLatchedQuestion = question;
    });
    ref.read(voiceSessionProvider.notifier).sendChatAnswer(
          question.stepId,
          optionId: optionId,
          value: value,
          cropId: cropId,
          cropName: cropName,
        );
  }

  /// Opens the guided camera for the `photo` step. The requested part is the
  /// farmer's earlier `plant_part` selection, or `whole_plant` when that step
  /// was skipped (weed queries). The existing camera → confirm → `uploadPhoto`
  /// pipeline runs unchanged from here; the guide only observes it server-side.
  void _takePhoto(ChatQuestion question) {
    setState(() {
      _sentForStepId = question.stepId;
      _lastLatchedQuestion = question;
    });
    final part = ref.read(guideSelectionsProvider)['plant_part'];
    ref
        .read(appModeProvider.notifier)
        .toCamera(
          callId: 'guide-photo',
          targetPart: (part == null || part.isEmpty) ? 'whole_plant' : part,
          reason: S.qPhoto,
        );
  }

  /// A bottom sheet listing [options] under [title]. Tapping one answers the
  /// step exactly like an inline chip. Used both for the `crop_picker` saved
  /// list and for the «Boshqa» overflow of a long button set (e.g. plant
  /// parts). [sendValue] mirrors the inline path: crop picks carry the label
  /// as `value`, plain buttons send `option_id` only.
  Future<void> _openOptionsSheet(
    ChatQuestion question,
    List<ChatOption> options,
    String title, {
    IconData leadingIcon = Icons.chevron_right,
    bool sendValue = false,
  }) async {
    final picked = await showModalBottomSheet<ChatOption>(
      context: context,
      builder: (sheetContext) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 20, 8),
              child: Text(
                title,
                style: Theme.of(sheetContext).textTheme.titleMedium,
              ),
            ),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final o in options)
                    ListTile(
                      leading: Icon(leadingIcon),
                      title: Text(o.label),
                      onTap: () => Navigator.of(sheetContext).pop(o),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 8),
          ],
        ),
      ),
    );
    if (picked == null || !mounted) return;
    // sendValue == a crop pick: the crop rides as its own object; option_id
    // stays the tapped chip. Plain button sheets keep option_id only.
    _answer(
      question,
      optionId: picked.id,
      cropId: sendValue ? picked.id : null,
      cropName: sendValue ? picked.label : null,
    );
  }

  Future<void> _openCropPicker(ChatQuestion question) async {
    final crop = await showModalBottomSheet<Crop>(
      context: context,
      isScrollControlled: true,
      builder: (sheetContext) => SafeArea(
        child: SizedBox(
          height: MediaQuery.of(sheetContext).size.height * 0.85,
          child: Padding(
            padding: const EdgeInsets.only(top: 12),
            child: CropPickList(
              onPick: (c) => Navigator.of(sheetContext).pop(c),
            ),
          ),
        ),
      ),
    );
    if (crop == null || !mounted) return;
    ref.read(selectedCropProvider.notifier).select(crop);
    _answer(
      question,
      optionId: 'open_crop_picker',
      cropId: crop.id,
      cropName: crop.name,
    );
  }
}
