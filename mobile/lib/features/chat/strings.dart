/// English string table for the multichat + guided flow UI. Keys mirror the
/// backend's `chat/models.py` `UZ` dict exactly — see
/// `docs/multichat_contract.md` §6. Some of these strings (`chat.step`
/// labels, stored message text) must match the backend byte-for-byte, so do
/// not substitute a plain `'` for `ʻ`.
library;

abstract final class S {
  const S._();

  static const String homeTitle = 'Chats';
  static const String newChat = 'New chat';
  static const String emptyList =
      'No chats yet. Tap "New chat" to get started.';
  static const String listLoadFailed = "Couldn't load chats";
  static const String chatLoadFailed =
      "Couldn't open chat. Please try again.";
  static const String retry = 'Retry';
  static const String offlineChat =
      'Chat not saved — no server connection.';
  static const String today = 'Today';
  static const String yesterday = 'Yesterday';
  static const String newChatTitle = 'New chat';

  // v2 (docs/multichat_contract.md §6): qQueryType REVISED, third option
  // `optGeneral` added.
  static const String qQueryType = 'What do you need advice on?';
  static const String optDiseasePest = 'Diseases & pests';
  static const String optWeed = 'Weeds';
  static const String optGeneral = 'Ask a general question';

  static const String qCrop = 'Is this crop in your profile?';
  static const String optCrops = 'Crops';
  static const String optCropYes = 'Yes';
  static const String optCropNo = 'No';
  static const String savedCropsTitle = 'Crops in profile';

  static const String qPlantPart = 'Which part of the plant is affected?';
  static const String optMore = 'More';
  static const String partLeaf = 'Leaf';
  static const String partStem = 'Stem';
  static const String partFruit = 'Fruit';
  static const String partFlower = 'Flower';
  static const String partRoot = 'Root';
  static const String partBranch = 'Branch';
  static const String partBark = 'Bark';
  static const String partWhole = 'Whole plant';
  static const String partSoil = 'Soil';

  // v2 NEW: symptom-dialogue phase (§1.3 c).
  static const String qSymptom = 'Describe the symptoms';
  static const String optToPhoto = 'Take a photo';

  static const String qPhoto = 'Send a photo of the affected part';
  static const String optTakePhoto = 'Choose photo';
  // optSkipPhoto removed 2026-08-05 — a photo is mandatory; the server never
  // sends a skip option, so the app has no button to label.
  static const String photoMarker = '[rasm]';
  static const String photoBubble = '📷 Photo';

  // v2 NEW: general-question phase + trigger offer (§1.3 e/f).
  static const String qGeneral = 'Go ahead and ask your question';
  static const String qDiagOffer = 'Shall we start the diagnosis?';
  static const String optSwitchDiag = 'Yes, let\'s diagnose';
  static const String optStayGeneral = 'No, continue';
  static const String generalTitle = 'General question';

  static const String qtDisease = 'disease';
  static const String qtWeed = 'weed';
  static const String diagPrefix = 'Diagnosis:';

  // Phase 3 (docs/multichat_contract.md P3.8): agronom verification stub.
  static const String agronomSend = 'Send to agronomist';
  static const String agronomPending = 'Agronomist reviewing…';
  static const String agronomCardTitle = 'Agronomist (expert) response';
  static const String agronomBadge = 'Verified by agronomist';
  static const String agronomMockLabel = 'AI assistant (demo)';
  static const String agronomConfirmed = 'Diagnosis confirmed';
  static const String agronomAdjusted = 'Recommendation updated';
  static const String agronomKeepPreps =
      'The AI-recommended preparations list remains in effect.';
  static const String agronomAdjustedPreps = 'Updated preparations list';
  static const String agronomRequestFailed =
      "Couldn't send. Please try again.";
}
