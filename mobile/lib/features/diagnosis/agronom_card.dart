/// The finished agronom (expert) review rendered as its own card bubble —
/// spec §7.2, contract addendum Phase 3 (P3.7).
///
/// Deliberately visually DISTINCT from the AI [DiagnosisCard]: a tertiary
/// border + header band mark it as coming from a different author, plus a
/// verdict pill and (when the review is the auto senior-agronom stub) the
/// «AI yordamchi (sinov)» mock pill so it is never mistakable for a human.
library;

import 'package:flutter/material.dart';

import '../chat/chat.dart' show AgronomReview;
import '../chat/strings.dart';
import 'diagnosis_card.dart' show PreparationsSection;

/// Renders an [AgronomReview] (status `'done'`) as a card bubble.
class AgronomCard extends StatelessWidget {
  const AgronomCard({super.key, required this.review});

  final AgronomReview review;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 8),
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: theme.colorScheme.tertiary, width: 1.4),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header band — the tertiary tint + verified_user icon are the
          // visual cue that this card is NOT the AI's own answer.
          Container(
            width: double.infinity,
            color: theme.colorScheme.tertiaryContainer,
            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
            child: Row(
              children: [
                Icon(
                  Icons.verified_user,
                  size: 20,
                  color: theme.colorScheme.onTertiaryContainer,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    S.agronomCardTitle,
                    style: theme.textTheme.titleSmall?.copyWith(
                      fontWeight: FontWeight.w700,
                      color: theme.colorScheme.onTertiaryContainer,
                    ),
                  ),
                ),
                if (review.isMock) ...[
                  const SizedBox(width: 8),
                  const _Pill(label: S.agronomMockLabel, color: Colors.amber),
                ],
              ],
            ),
          ),

          Padding(
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    const _Pill(label: S.agronomBadge, color: Colors.green),
                    if (review.verdict == 'confirmed')
                      const _Pill(
                        label: S.agronomConfirmed,
                        color: Colors.green,
                      )
                    else if (review.verdict == 'adjusted')
                      const _Pill(
                        label: S.agronomAdjusted,
                        color: Colors.amber,
                      ),
                  ],
                ),
                if (review.expertSummary.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 10),
                    child: Text(
                      review.expertSummary,
                      style: theme.textTheme.bodyLarge?.copyWith(
                        height: 1.35,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ),
                if (review.expertNotes.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 10, bottom: 6),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        for (final note in review.expertNotes)
                          Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Padding(
                                  padding: const EdgeInsets.only(
                                    top: 6,
                                    right: 8,
                                  ),
                                  child: Icon(
                                    Icons.circle,
                                    size: 6,
                                    color: theme.colorScheme.outline,
                                  ),
                                ),
                                Expanded(
                                  child: Text(
                                    note,
                                    style: theme.textTheme.bodyMedium,
                                  ),
                                ),
                              ],
                            ),
                          ),
                      ],
                    ),
                  ),
              ],
            ),
          ),

          if (review.adjustedPreparations.isNotEmpty)
            PreparationsSection(
              preparations: review.adjustedPreparations,
              title: S.agronomAdjustedPreps,
            )
          else
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 0, 14, 10),
              child: Text(
                S.agronomKeepPreps,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.outline,
                ),
              ),
            ),
          const SizedBox(height: 4),
        ],
      ),
    );
  }
}

/// The badge/verdict/mock-label pill — a local private copy of
/// `DiagnosisCard`'s `_ConfidenceChip` visual (that widget is file-private).
class _Pill extends StatelessWidget {
  const _Pill({required this.label, required this.color});

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
