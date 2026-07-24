import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/features/chat/chat.dart';

void main() {
  group('AgronomReview.fromJson — spec §7 (contract addendum P3.1/P3.7)', () {
    test('null -> AgronomReview.none()', () {
      final r = AgronomReview.fromJson(null);
      expect(r.status, 'none');
      expect(r.requestedAt, isNull);
      expect(r.reviewedAt, isNull);
      expect(r.isMock, false);
      expect(r.verdict, '');
      expect(r.expertSummary, '');
      expect(r.expertNotes, isEmpty);
      expect(r.adjustedPreparations, isEmpty);
    });

    test('full done object parses every field', () {
      final r = AgronomReview.fromJson({
        'status': 'done',
        'requested_at': '2026-07-14T09:12:31+00:00',
        'reviewed_at': '2026-07-14T09:13:02+00:00',
        'is_mock': true,
        'verdict': 'adjusted',
        'expert_summary':
            'Tashxis toʻgʻri, ammo doza pasaytirilishi kerak.',
        'expert_notes': [
          'Ertalab salqinda ishlov bering.',
          '7 kundan keyin takrorlang.',
        ],
        'adjusted_preparations': [
          {
            'name': 'TOPAZ 10% EM.K',
            'dose_min': 0.3,
            'dose_max': 0.5,
            'unit': 'l/ga',
            'type': 'disease',
            'description': '…',
          },
        ],
      });

      expect(r.status, 'done');
      expect(r.requestedAt, DateTime.parse('2026-07-14T09:12:31+00:00'));
      expect(r.reviewedAt, DateTime.parse('2026-07-14T09:13:02+00:00'));
      expect(r.isMock, true);
      expect(r.verdict, 'adjusted');
      expect(r.expertSummary, 'Tashxis toʻgʻri, ammo doza pasaytirilishi kerak.');
      expect(r.expertNotes, [
        'Ertalab salqinda ishlov bering.',
        '7 kundan keyin takrorlang.',
      ]);
      expect(r.adjustedPreparations, hasLength(1));
      final p = r.adjustedPreparations.single;
      expect(p.name, 'TOPAZ 10% EM.K');
      expect(p.doseMin, 0.3);
      expect(p.doseMax, 0.5);
      expect(p.unit, 'l/ga');
      expect(p.type, 'disease');
    });

    test('string-typed doses in adjusted_preparations coerce to double', () {
      final r = AgronomReview.fromJson({
        'status': 'done',
        'adjusted_preparations': [
          {
            'name': 'X',
            'dose_min': '0.3',
            'dose_max': '0.5',
            'unit': 'l/ga',
            'type': 'disease',
            'description': '',
          },
        ],
      });
      final p = r.adjustedPreparations.single;
      expect(p.doseMin, 0.3);
      expect(p.doseMax, 0.5);
    });

    test('unknown status string passes through unchanged', () {
      final r = AgronomReview.fromJson({'status': 'something_new'});
      expect(r.status, 'something_new');
    });

    test('ChatSummary.fromJson without agronom_review key -> none (old backend)', () {
      final summary = ChatSummary.fromJson({
        'id': 'c1',
        'user_id': 'u1',
        'title': 'Suhbat',
        'query_type': '',
        'crop_id': '',
        'crop_name': '',
        'plant_part': '',
        'created_at': '',
        'updated_at': '',
        'finished': false,
        'message_count': 0,
      });
      expect(summary.agronomReview.status, 'none');
    });

    test('ChatSummary.fromJson with agronom_review key parses it', () {
      final summary = ChatSummary.fromJson({
        'id': 'c1',
        'user_id': 'u1',
        'title': 'Suhbat',
        'query_type': '',
        'crop_id': '',
        'crop_name': '',
        'plant_part': '',
        'created_at': '',
        'updated_at': '',
        'finished': false,
        'message_count': 0,
        'agronom_review': {'status': 'pending'},
      });
      expect(summary.agronomReview.status, 'pending');
    });
  });
}
