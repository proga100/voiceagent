/// The diagnosis result rendered as a farmer-friendly card in the transcript.
///
/// Header: likely disease + a colour-coded confidence chip (high=green,
/// medium=amber, low=grey). Below it the spoken summary is emphasised, then two
/// expandable sections — immediate treatment and prevention — and the
/// differentials as small chips whose tooltip explains the reasoning. All
/// labels are in Uzbek.
///
/// When `result.isPlant` is false the diagnosis was made WITHOUT a photo (none
/// was sent, or it didn't show a plant): the card still renders the full
/// text-based diagnosis but leads with an amber note saying the photo was not
/// used and a proper one would sharpen the result. Only when the model also
/// produced no disease at all does the card fall back to a neutral prompt.
library;

import 'package:flutter/material.dart';

import '../../core/protocol/events.dart';
import '../chat/strings.dart';

/// Renders a [DiagnosisResult] as a card bubble.
class DiagnosisCard extends StatelessWidget {
  const DiagnosisCard({
    super.key,
    required this.result,
    this.preparations = const [],
    this.photos = const [],
    this.agronomStatus = '',
    this.onAgronomRequest,
  });

  final DiagnosisResult result;

  /// Growz Agroapteka preparations for [result.likelyDisease] (contract
  /// addendum P2.1/P2.8). Rendered only on the full-card path, never inside
  /// [_NotAPlantCard]; the section itself hides when this is empty.
  final List<Preparation> preparations;

  /// Case photos from `case.diagnosis.photos`. Only photos with an http(s)
  /// [PhotoRef.storedPath] render; never shown inside [_NotAPlantCard]
  /// (the :76 early return precedes the row).
  final List<PhotoRef> photos;

  /// Spec §7 agronom verification state (contract Phase 3, P3.7). Frozen
  /// vocabulary: `''` (chatless — render nothing) | `'none'` | `'pending'` |
  /// `'done'`.
  final String agronomStatus;

  /// Tapped from the `'none'` state's "Agronomga yuborish" button. `null`
  /// disables the row entirely (chatless session).
  final VoidCallback? onAgronomRequest;

  Color _confidenceColor() {
    switch (result.confidence) {
      case 'high':
        return Colors.green;
      case 'medium':
        return Colors.amber;
      case 'low':
        return Colors.grey;
      default:
        return Colors.grey;
    }
  }

  String _confidenceLabel() {
    switch (result.confidence) {
      case 'high':
        return 'Yuqori ishonch';
      case 'medium':
        return 'Oʻrta ishonch';
      case 'low':
        return 'Past ishonch';
      default:
        return result.confidence;
    }
  }

  @override
  Widget build(BuildContext context) {
    // No diagnosis at all (edge case) -> neutral prompt card.
    if (!result.isPlant && result.likelyDisease.trim().isEmpty) {
      return _NotAPlantCard(result: result);
    }

    final theme = Theme.of(context);
    final confidenceColor = _confidenceColor();

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!result.isPlant)
            Container(
              width: double.infinity,
              color: Colors.amber.withValues(alpha: 0.18),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: Row(
                children: [
                  const Icon(Icons.no_photography_outlined,
                      size: 18, color: Colors.amber),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Tashxis suhbat asosida qoʻyildi — rasm yaroqsiz boʻlgani '
                      'uchun hisobga olinmadi. Toʻgʻri rasm yuborsangiz, '
                      'aniqroq tashxis beramiz.',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ),

          // Header.
          Padding(
            padding: const EdgeInsets.fromLTRB(14, 14, 14, 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  Icons.local_hospital,
                  size: 22,
                  color: theme.colorScheme.primary,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    result.likelyDisease.isEmpty
                        ? 'Tashxis'
                        : result.likelyDisease,
                    style: theme.textTheme.titleMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                if (result.confidence.isNotEmpty) ...[
                  const SizedBox(width: 8),
                  _ConfidenceChip(
                    label: _confidenceLabel(),
                    color: confidenceColor,
                  ),
                ],
              ],
            ),
          ),

          // Photo preview: what the AI looked at vs. what was merely kept.
          if (photos.any((p) => p.isRenderable))
            _PhotoPreviewRow(
              photos: photos
                  .where((p) => p.isRenderable)
                  .toList(growable: false),
            ),

          // Spoken summary — emphasised.
          if (result.spokenSummary.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
              child: Text(
                result.spokenSummary,
                style: theme.textTheme.bodyLarge?.copyWith(
                  height: 1.35,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),

          // Differentials.
          if (result.differentials.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Boshqa ehtimollar',
                    style: theme.textTheme.labelMedium?.copyWith(
                      color: theme.colorScheme.outline,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final d in result.differentials)
                        Tooltip(
                          message: d.why.isEmpty ? d.name : d.why,
                          child: Chip(
                            label: Text(d.name),
                            visualDensity: VisualDensity.compact,
                            materialTapTargetSize:
                                MaterialTapTargetSize.shrinkWrap,
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),

          // Treatment / prevention sections.
          if (result.immediateTreatment.isNotEmpty)
            _BulletSection(
              title: 'Darhol qilinadigan ishlar',
              icon: Icons.healing,
              iconColor: Colors.green,
              items: result.immediateTreatment,
              initiallyExpanded: true,
            ),
          if (result.prevention.isNotEmpty)
            _BulletSection(
              title: 'Oldini olish',
              icon: Icons.shield_outlined,
              iconColor: theme.colorScheme.primary,
              items: result.prevention,
              initiallyExpanded: false,
            ),
          if (preparations.isNotEmpty)
            PreparationsSection(preparations: preparations),

          // Spec §7 agronom verification (contract Phase 3, P3.7): after the
          // preparations section (or right after prevention when there are
          // none). `agronomStatus == ''` (chatless session) renders nothing.
          if (agronomStatus == 'none' && onAgronomRequest != null)
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 4, 14, 10),
              child: SizedBox(
                width: double.infinity,
                child: FilledButton.tonalIcon(
                  icon: const Icon(Icons.support_agent),
                  label: const Text(S.agronomSend),
                  onPressed: onAgronomRequest,
                ),
              ),
            ),
          if (agronomStatus == 'pending')
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 4, 14, 10),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 8),
                  Text(
                    S.agronomPending,
                    style: theme.textTheme.bodySmall?.copyWith(
                      color: theme.colorScheme.outline,
                    ),
                  ),
                ],
              ),
            ),

          const SizedBox(height: 4),
        ],
      ),
    );
  }
}

/// Neutral, non-alarming card shown when the photo wasn't a plant. Carries the
/// farmer-facing [DiagnosisResult.spokenSummary] (already in their language)
/// with a muted icon and none of the disease chrome.
class _NotAPlantCard extends StatelessWidget {
  const _NotAPlantCard({required this.result});

  final DiagnosisResult result;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final muted = theme.colorScheme.outline;
    final message = result.spokenSummary.isEmpty
        ? 'Rasmda oʻsimlik koʻrinmadi. Iltimos, kasallangan qismini '
              'yaqindan suratga oling.'
        : result.spokenSummary;

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8),
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(Icons.image_not_supported_outlined, size: 22, color: muted),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                message,
                style: theme.textTheme.bodyLarge?.copyWith(
                  height: 1.35,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ConfidenceChip extends StatelessWidget {
  const _ConfidenceChip({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.18),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.5)),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
          color: color,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }
}

class _BulletSection extends StatelessWidget {
  const _BulletSection({
    required this.title,
    required this.icon,
    required this.iconColor,
    required this.items,
    required this.initiallyExpanded,
  });

  final String title;
  final IconData icon;
  final Color iconColor;
  final List<String> items;
  final bool initiallyExpanded;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Theme(
      // Drop the default ExpansionTile dividers for a cleaner card.
      data: theme.copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        initiallyExpanded: initiallyExpanded,
        tilePadding: const EdgeInsets.symmetric(horizontal: 14),
        childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 8),
        leading: Icon(icon, color: iconColor, size: 20),
        title: Text(
          title,
          style: theme.textTheme.titleSmall?.copyWith(
            fontWeight: FontWeight.w600,
          ),
        ),
        children: [
          for (final item in items)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 6, right: 8),
                    child: Icon(
                      Icons.circle,
                      size: 6,
                      color: theme.colorScheme.outline,
                    ),
                  ),
                  Expanded(
                    child: Text(item, style: theme.textTheme.bodyMedium),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// "Tavsiya etilgan preparatlar" — Growz Agroapteka lookup results for
/// [DiagnosisResult.likelyDisease] (contract addendum P2.8). Always visible
/// (not an `ExpansionTile`): the preparations are the payoff of the
/// diagnosis, not a collapsible aside. No button/link — the Marketplace
/// deep-link is deferred.
///
/// Public (contract Phase 3, P3.7): reused verbatim by `AgronomCard` for the
/// expert's `adjusted_preparations`, with a different [title].
class PreparationsSection extends StatelessWidget {
  const PreparationsSection({
    super.key,
    required this.preparations,
    this.title = 'Tavsiya etilgan preparatlar',
  });

  final List<Preparation> preparations;
  final String title;

  /// Badge label + color for a known [Preparation.type]; `null` (no badge)
  /// for anything else, including the empty string.
  static (String, Color)? _badge(String type) {
    switch (type) {
      case 'disease':
        return ('Kasallik', Colors.deepOrange);
      case 'pest':
        return ('Zararkunanda', Colors.brown);
      case 'weed':
        return ('Begona oʻt', Colors.teal);
      default:
        return null;
    }
  }

  /// Trims a whole-number double to its integer form (`1`), otherwise keeps
  /// up to 2 decimals with trailing zeros stripped (`0.75`, `0.5`).
  static String _fmtNum(double v) {
    if (v == v.roundToDouble()) return v.toInt().toString();
    var s = v.toStringAsFixed(2);
    while (s.endsWith('0')) {
      s = s.substring(0, s.length - 1);
    }
    if (s.endsWith('.')) s = s.substring(0, s.length - 1);
    return s;
  }

  /// `'0.75–1 l/ga'` — an en dash range when both bounds differ, else the
  /// single known value, with the unit appended when present.
  static String _fmtDose(Preparation p) {
    final min = p.doseMin;
    final max = p.doseMax;
    String number;
    if (min != null && max != null && min != max) {
      number = '${_fmtNum(min)}–${_fmtNum(max)}';
    } else {
      number = _fmtNum(min ?? max!);
    }
    return p.unit.isEmpty ? number : '$number ${p.unit}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 4, 14, 8),
          child: Row(
            children: [
              Icon(
                Icons.medication_outlined,
                size: 20,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(width: 8),
              Text(
                title,
                style: theme.textTheme.titleSmall?.copyWith(
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
        ),
        for (final p in preparations)
          Container(
            margin: const EdgeInsets.fromLTRB(14, 0, 14, 8),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: theme.colorScheme.surfaceContainerHighest.withValues(
                alpha: 0.5,
              ),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        p.name,
                        style: theme.textTheme.bodyMedium?.copyWith(
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                    if (_badge(p.type) case (final label, final color)) ...[
                      const SizedBox(width: 8),
                      _ConfidenceChip(label: label, color: color),
                    ],
                  ],
                ),
                if (p.doseMin != null || p.doseMax != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      'Doza: ${_fmtDose(p)}',
                      style: theme.textTheme.bodySmall?.copyWith(
                        color: theme.colorScheme.outline,
                      ),
                    ),
                  ),
                if (p.description.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      p.description,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
              ],
            ),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
          child: Text(
            'Growz Agroaptekasidan xarid qilishingiz mumkin.',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.outline,
            ),
          ),
        ),
      ],
    );
  }
}

/// The horizontal photo strip under the diagnosis header: the images the AI
/// actually analysed (prominent) vs. the ones merely kept.
class _PhotoPreviewRow extends StatelessWidget {
  const _PhotoPreviewRow({required this.photos});

  final List<PhotoRef> photos;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final labelStyle = theme.textTheme.labelMedium?.copyWith(
      color: theme.colorScheme.outline,
    );
    final selected = photos.where((p) => p.selected).toList(growable: false);
    final kept = photos.where((p) => !p.selected).toList(growable: false);

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 0, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (selected.isNotEmpty) ...[
            Text('Tahlilga olingan rasmlar', style: labelStyle),
            const SizedBox(height: 6),
            SizedBox(
              height: 72,
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: [
                  for (final p in selected)
                    _PhotoThumb(photo: p, prominent: true),
                ],
              ),
            ),
          ],
          if (kept.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Saqlangan rasmlar', style: labelStyle),
            const SizedBox(height: 6),
            SizedBox(
              height: 72,
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: [
                  for (final p in kept)
                    _PhotoThumb(photo: p, prominent: false),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// A single 72×72 tappable thumbnail. [prominent] photos (AI-analysed) get a
/// primary border + check badge; kept photos are dimmed.
class _PhotoThumb extends StatelessWidget {
  const _PhotoThumb({required this.photo, required this.prominent});

  final PhotoRef photo;
  final bool prominent;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return GestureDetector(
      onTap: () => _PhotoViewer.open(context, photo),
      child: Container(
        width: 72,
        height: 72,
        margin: const EdgeInsets.only(right: 8),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(10),
          border: prominent
              ? Border.all(color: theme.colorScheme.primary, width: 2)
              : Border.all(color: theme.colorScheme.outlineVariant),
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: Stack(
            fit: StackFit.expand,
            children: [
              Image.network(
                photo.storedPath,
                fit: BoxFit.cover,
                loadingBuilder: (context, child, progress) => progress == null
                    ? child
                    : Container(
                        color: Theme.of(context)
                            .colorScheme
                            .surfaceContainerHighest,
                        child: const Center(
                          child: SizedBox(
                            width: 18,
                            height: 18,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        ),
                      ),
                errorBuilder: (_, _, _) => Container(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  child: const Icon(Icons.image_not_supported_outlined,
                      size: 20),
                ),
              ),
              if (!prominent)
                Container(color: Colors.black.withValues(alpha: 0.35)),
              if (prominent)
                Positioned(
                  right: 3,
                  bottom: 3,
                  child: Container(
                    decoration: BoxDecoration(
                      color: theme.colorScheme.primary,
                      shape: BoxShape.circle,
                    ),
                    padding: const EdgeInsets.all(2),
                    child: Icon(Icons.check,
                        size: 10, color: theme.colorScheme.onPrimary),
                  ),
                ),
              if (photo.imageConfidence == 'low')
                const Positioned(
                  left: 3,
                  top: 3,
                  child: Icon(Icons.warning_amber_rounded,
                      size: 14, color: Colors.amber),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Full-screen pinch-zoom viewer for a single case photo.
class _PhotoViewer extends StatelessWidget {
  const _PhotoViewer({required this.photo});

  final PhotoRef photo;

  static void open(BuildContext context, PhotoRef photo) =>
      Navigator.of(context).push(MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (_) => _PhotoViewer(photo: photo),
      ));

  @override
  Widget build(BuildContext context) {
    final organ = photo.perImageAnalysis['organ']?.toString() ?? '';
    final conf = photo.perImageAnalysis['confidence']?.toString() ?? '';
    final caption = [
      if (organ.isNotEmpty) organ,
      if (conf.isNotEmpty) 'ishonch: $conf',
    ].join(' · ');

    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
      ),
      body: Column(
        children: [
          Expanded(
            child: InteractiveViewer(
              maxScale: 5,
              child: Center(
                child: Image.network(
                  photo.storedPath,
                  fit: BoxFit.contain,
                  loadingBuilder: (context, child, progress) => progress == null
                      ? child
                      : const Center(
                          child: CircularProgressIndicator(color: Colors.white),
                        ),
                  errorBuilder: (_, _, _) => const Icon(
                    Icons.image_not_supported_outlined,
                    color: Colors.white54,
                    size: 48,
                  ),
                ),
              ),
            ),
          ),
          if (photo.selected &&
              photo.perImageAnalysis.isNotEmpty &&
              caption.isNotEmpty)
            SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Text(
                  caption,
                  textAlign: TextAlign.center,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: Colors.white),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
