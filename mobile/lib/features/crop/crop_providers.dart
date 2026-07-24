/// Crop-picker state: the catalogue (async) and the farmer's current choice.
///
/// [selectedCropProvider] is the app's top-level gate — while it is null the
/// crop picker is shown; once set, the interview mounts and its session.start
/// carries the crop. [cropsProvider] loads the Growz catalogue once and caches
/// it for the picker.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'crop.dart';
import 'crop_service.dart';

/// The Growz crop catalogue (loaded once via `GET /crops`).
final cropsProvider = FutureProvider<List<Crop>>((ref) async {
  return const CropService().fetchCrops();
});

/// The crop the farmer picked for this call, or null before they choose.
final selectedCropProvider =
    NotifierProvider<SelectedCropController, Crop?>(SelectedCropController.new);

class SelectedCropController extends Notifier<Crop?> {
  @override
  Crop? build() => null;

  void select(Crop crop) => state = crop;

  /// Return to the picker (end of call / "change crop").
  void clear() => state = null;
}
