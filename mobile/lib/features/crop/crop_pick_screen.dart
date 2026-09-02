/// Pre-call crop picker — the app's first screen.
///
/// The farmer chooses which crop they want to talk about; picking one sets
/// [selectedCropProvider], which the top-level gate observes to mount the
/// interview. The Growz catalogue (~134 crops) is searchable and grouped by
/// category. Network hiccups show a retry state rather than a dead screen.
///
/// [CropPickList] is the reusable search-field + grouped-list body, extracted
/// so the guided-flow "Ekinlar" step (`lib/features/chat/guide_options_bar.dart`)
/// can embed the identical picker inside a modal bottom sheet instead of this
/// standalone screen.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'crop.dart';
import 'crop_providers.dart';

class CropPickScreen extends ConsumerWidget {
  const CropPickScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);

    return Scaffold(
      body: SafeArea(
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Which crop shall we talk about?',
                    style: theme.textTheme.headlineSmall?.copyWith(
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'Select a crop — Alomat will focus the conversation on it.',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: theme.colorScheme.onSurfaceVariant,
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: CropPickList(
                onPick: (c) =>
                    ref.read(selectedCropProvider.notifier).select(c),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// The search field + grouped crop list. Requires a bounded height from its
/// ancestor (an `Expanded`/`Flexible`/fixed-height box) — the standalone
/// screen above provides one via `Expanded`; the guided-flow crop sheet wraps
/// it in a fixed-height `SizedBox`.
class CropPickList extends ConsumerStatefulWidget {
  const CropPickList({super.key, required this.onPick});

  /// Called with the tapped crop. The caller decides what happens next (set
  /// [selectedCropProvider], pop a sheet, send a `chat.answer`, …).
  final void Function(Crop crop) onPick;

  @override
  ConsumerState<CropPickList> createState() => _CropPickListState();
}

class _CropPickListState extends ConsumerState<CropPickList> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final cropsAsync = ref.watch(cropsProvider);

    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: TextField(
            onChanged: (v) => setState(() => _query = v.trim()),
            textInputAction: TextInputAction.search,
            decoration: InputDecoration(
              hintText: 'Search crops…',
              prefixIcon: const Icon(Icons.search),
              filled: true,
              isDense: true,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(14),
                borderSide: BorderSide.none,
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        Expanded(
          child: cropsAsync.when(
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (e, _) => _ErrorState(
              onRetry: () => ref.invalidate(cropsProvider),
            ),
            data: (crops) => _CropRows(
              crops: _filter(crops, _query),
              onPick: widget.onPick,
            ),
          ),
        ),
      ],
    );
  }

  List<Crop> _filter(List<Crop> crops, String query) {
    if (query.isEmpty) return crops;
    final q = query.toLowerCase();
    return crops
        .where((c) =>
            c.name.toLowerCase().contains(q) ||
            c.category.toLowerCase().contains(q) ||
            c.biologyName.toLowerCase().contains(q))
        .toList();
  }
}

/// The catalogue as category headers + tappable crop rows. The backend already
/// sorts by (category, name), so a single pass emits headers on category change.
class _CropRows extends StatelessWidget {
  const _CropRows({required this.crops, required this.onPick});

  final List<Crop> crops;
  final void Function(Crop) onPick;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    if (crops.isEmpty) {
      return Center(
        child: Text(
          'Nothing found',
          style: theme.textTheme.bodyLarge?.copyWith(
            color: theme.colorScheme.onSurfaceVariant,
          ),
        ),
      );
    }

    // Flatten to a list of rows: category header (String) or crop (Crop).
    final rows = <Object>[];
    String? lastCategory;
    for (final c in crops) {
      if (c.category.isNotEmpty && c.category != lastCategory) {
        rows.add(c.category);
        lastCategory = c.category;
      }
      rows.add(c);
    }

    return ListView.builder(
      padding: const EdgeInsets.only(bottom: 24),
      itemCount: rows.length,
      itemBuilder: (context, i) {
        final row = rows[i];
        if (row is String) {
          return Padding(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 6),
            child: Text(
              row.toUpperCase(),
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.primary,
                fontWeight: FontWeight.w700,
                letterSpacing: 0.6,
              ),
            ),
          );
        }
        final crop = row as Crop;
        return ListTile(
          leading: const Text('🌱', style: TextStyle(fontSize: 22)),
          title: Text(crop.name),
          subtitle: crop.biologyName.isEmpty
              ? null
              : Text(
                  crop.biologyName,
                  style: TextStyle(
                    fontStyle: FontStyle.italic,
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
          trailing: const Icon(Icons.chevron_right),
          onTap: () => onPick(crop),
        );
      },
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
          Icon(Icons.cloud_off, size: 48, color: theme.colorScheme.onSurfaceVariant),
          const SizedBox(height: 12),
          Text("Couldn't load crops", style: theme.textTheme.titleMedium),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: onRetry, child: const Text('Retry')),
        ],
      ),
    );
  }
}
