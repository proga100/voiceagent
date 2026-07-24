import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/features/session/photo_request_provider.dart';

void main() {
  group('PendingPhotoRequestNotifier — manual-capture request lifecycle', () {
    late ProviderContainer container;
    PendingPhotoRequestNotifier notifier() =>
        container.read(pendingPhotoRequestProvider.notifier);
    PendingPhotoRequest? current() =>
        container.read(pendingPhotoRequestProvider);

    setUp(() => container = ProviderContainer());
    tearDown(() => container.dispose());

    test('starts empty', () {
      expect(current(), isNull);
    });

    test('set stores the pending request', () {
      notifier().set(callId: 'c1', targetPart: 'leaf', reason: 'need leaf');
      final req = current();
      expect(req, isNotNull);
      expect(req!.callId, 'c1');
      expect(req.targetPart, 'leaf');
      expect(req.reason, 'need leaf');
    });

    test('set replaces an existing pending request', () {
      notifier().set(callId: 'c1', targetPart: 'leaf', reason: 'first');
      notifier().set(callId: 'c2', targetPart: 'fruit', reason: 'second');
      final req = current();
      expect(req, isNotNull);
      expect(req!.callId, 'c2');
      expect(req.targetPart, 'fruit');
      expect(req.reason, 'second');
    });

    test('clear removes the pending request', () {
      notifier().set(callId: 'c1', targetPart: 'leaf', reason: 'need leaf');
      notifier().clear();
      expect(current(), isNull);
    });

    test('clear on an empty state is a no-op', () {
      notifier().clear();
      expect(current(), isNull);
    });

    test('can set again after clearing (upload done -> new request)', () {
      notifier().set(callId: 'c1', targetPart: 'leaf', reason: 'first');
      notifier().clear();
      notifier().set(callId: 'c2', targetPart: 'soil', reason: 'second');
      final req = current();
      expect(req, isNotNull);
      expect(req!.callId, 'c2');
      expect(req.targetPart, 'soil');
    });

    test('notifies listeners on set and clear', () {
      final seen = <PendingPhotoRequest?>[];
      container.listen<PendingPhotoRequest?>(
        pendingPhotoRequestProvider,
        (_, next) => seen.add(next),
        fireImmediately: false,
      );
      notifier().set(callId: 'c1', targetPart: 'leaf', reason: 'r');
      notifier().clear();
      expect(seen.length, 2);
      expect(seen[0]?.callId, 'c1');
      expect(seen[1], isNull);
    });
  });
}
