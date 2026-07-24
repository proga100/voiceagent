import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/protocol/events.dart';

void main() {
  group('PhotoRef.fromJson', () {
    test('full parse — every field populated', () {
      final p = PhotoRef.fromJson({
        'photo_id': 'p2',
        'stored_path': 'https://cdn.example.com/p2.jpg',
        'selected': true,
        'image_confidence': 'low',
        'duplicate_of': 'p1',
        'per_image_analysis': {'organ': 'leaf', 'confidence': 0.9},
      });
      expect(p.photoId, 'p2');
      expect(p.storedPath, 'https://cdn.example.com/p2.jpg');
      expect(p.selected, true);
      expect(p.imageConfidence, 'low');
      expect(p.duplicateOf, 'p1');
      expect(p.perImageAnalysis['organ'], 'leaf');
      expect(p.perImageAnalysis['confidence'], 0.9);
      expect(p.isRenderable, true);
    });

    test('defaults — empty map', () {
      final p = PhotoRef.fromJson({});
      expect(p.photoId, '');
      expect(p.storedPath, '');
      expect(p.selected, false);
      expect(p.imageConfidence, '');
      expect(p.duplicateOf, '');
      expect(p.perImageAnalysis, isEmpty);
      expect(p.isRenderable, false);
    });

    test('stored_path: null → empty string, not renderable', () {
      final p = PhotoRef.fromJson({'stored_path': null});
      expect(p.storedPath, '');
      expect(p.isRenderable, false);
    });

    test('non-http guard', () {
      expect(
        PhotoRef.fromJson({'stored_path': '/var/data/photos/x.jpg'})
            .isRenderable,
        false,
      );
      expect(
        PhotoRef.fromJson({'stored_path': 'https://cdn.example.com/x.jpg'})
            .isRenderable,
        true,
      );
      expect(
        PhotoRef.fromJson({'stored_path': 'http://cdn.example.com/x.jpg'})
            .isRenderable,
        true,
      );
    });
  });

  group('CaseDiagnosis photo threading', () {
    test('no photos key → const []', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'c1',
        'result': <String, dynamic>{},
        'summary': <String, dynamic>{},
      });
      expect(e, isA<CaseDiagnosis>());
      expect((e as CaseDiagnosis).photos, isEmpty);
    });

    test('two photos parsed with fields', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'c1',
        'result': <String, dynamic>{},
        'summary': <String, dynamic>{},
        'photos': [
          {
            'photo_id': 'p1',
            'stored_path': 'https://cdn.example.com/p1.jpg',
            'selected': true,
          },
          {
            'photo_id': 'p2',
            'stored_path': 'https://cdn.example.com/p2.jpg',
            'selected': false,
          },
        ],
      });
      final photos = (e as CaseDiagnosis).photos;
      expect(photos.length, 2);
      expect(photos[0].photoId, 'p1');
      expect(photos[0].selected, true);
      expect(photos[0].isRenderable, true);
      expect(photos[1].photoId, 'p2');
      expect(photos[1].selected, false);
    });
  });
}
