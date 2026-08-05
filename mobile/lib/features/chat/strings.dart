/// Uzbek (Latin, `ʻ` U+02BB okina) string table for the multichat + guided
/// flow UI. Keys mirror the backend's `chat/models.py` `UZ` dict exactly —
/// see `docs/multichat_contract.md` §6. Some of these strings (`chat.step`
/// labels, stored message text) must match the backend byte-for-byte, so do
/// not substitute a plain `'` for `ʻ`.
library;

abstract final class S {
  const S._();

  static const String homeTitle = 'Suhbatlar';
  static const String newChat = 'Yangi chat';
  static const String emptyList =
      'Hali suhbatlar yoʻq. «Yangi chat» tugmasini bosing.';
  static const String listLoadFailed = 'Suhbatlar roʻyxati yuklanmadi';
  static const String chatLoadFailed =
      'Suhbat ochilmadi. Qayta urinib koʻring.';
  static const String retry = 'Qayta urinish';
  static const String offlineChat =
      'Suhbat saqlanmaydi — server bilan aloqa yoʻq.';
  static const String today = 'Bugun';
  static const String yesterday = 'Kecha';
  static const String newChatTitle = 'Yangi suhbat';

  // v2 (docs/multichat_contract.md §6): qQueryType REVISED, third option
  // `optGeneral` added.
  static const String qQueryType = 'Nima boʻyicha maslahat kerak?';
  static const String optDiseasePest = 'Kasalliklar va zararkunandalar';
  static const String optWeed = 'Begona oʻt';
  static const String optGeneral = 'Umumiy savol berish';

  static const String qCrop = 'Bu ekin profilingizda bormi?';
  static const String optCrops = 'Ekinlar';
  static const String optCropYes = 'Ha';
  static const String optCropNo = 'Yoʻq';
  static const String savedCropsTitle = 'Profildagi ekinlar';

  static const String qPlantPart = 'Muammo oʻsimlikning qaysi qismida?';
  static const String optMore = 'Boshqa';
  static const String partLeaf = 'Bargida';
  static const String partStem = 'Poyasida';
  static const String partFruit = 'Mevasida';
  static const String partFlower = 'Gulida';
  static const String partRoot = 'Ildizida';
  static const String partBranch = 'Shoxida';
  static const String partBark = 'Poʻstlogʻida';
  static const String partWhole = 'Butun oʻsimlik';
  static const String partSoil = 'Tuproqda';

  // v2 NEW: symptom-dialogue phase (§1.3 c).
  static const String qSymptom = 'Belgilar haqida gapirib bering';
  static const String optToPhoto = 'Rasmga oʻtish';

  static const String qPhoto = 'Kasallangan qismning rasmini yuboring';
  static const String optTakePhoto = 'Rasm tanlash';
  // optSkipPhoto removed 2026-08-05 — a photo is mandatory; the server never
  // sends a skip option, so the app has no button to label.
  static const String photoMarker = '[rasm]';
  static const String photoBubble = '📷 Rasm';

  // v2 NEW: general-question phase + trigger offer (§1.3 e/f).
  static const String qGeneral = 'Savolingizni bemalol ayting';
  static const String qDiagOffer = 'Aniqlash jarayonini boshlaymizmi?';
  static const String optSwitchDiag = 'Ha, aniqlaymiz';
  static const String optStayGeneral = 'Yoʻq, davom etamiz';
  static const String generalTitle = 'Umumiy savol';

  static const String qtDisease = 'kasallik';
  static const String qtWeed = 'begona oʻt';
  static const String diagPrefix = 'Tashxis:';

  // Phase 3 (docs/multichat_contract.md P3.8): agronom verification stub.
  static const String agronomSend = 'Agronomga yuborish';
  static const String agronomPending = 'Agronom tekshirmoqda…';
  static const String agronomCardTitle = 'Agronom (ekspert) javobi';
  static const String agronomBadge = 'Agronom tasdiqlagan javob';
  static const String agronomMockLabel = 'AI yordamchi (sinov)';
  static const String agronomConfirmed = 'Tashxis tasdiqlandi';
  static const String agronomAdjusted = 'Tavsiya aniqlashtirildi';
  static const String agronomKeepPreps =
      'AI tavsiya etgan preparatlar roʻyxati oʻz kuchida qoladi.';
  static const String agronomAdjustedPreps = 'Yangilangan preparatlar roʻyxati';
  static const String agronomRequestFailed =
      'Yuborib boʻlmadi. Qayta urinib koʻring.';
}
