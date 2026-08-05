import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/core/protocol/events.dart';

void main() {
  group('ServerEvent.fromJson — every server event', () {
    test('stt.partial carries value', () {
      final e = ServerEvent.fromJson({'type': 'stt.partial', 'value': 'salom'});
      expect(e, isA<SttPartial>());
      expect((e as SttPartial).text, 'salom');
    });

    test('llm.token', () {
      final e = ServerEvent.fromJson({'type': 'llm.token', 'token': 'ha '});
      expect(e, isA<LlmToken>());
      expect((e as LlmToken).token, 'ha ');
    });

    test('tts.started / tts.finished', () {
      expect(ServerEvent.fromJson({'type': 'tts.started'}), isA<TtsStarted>());
      expect(
        ServerEvent.fromJson({'type': 'tts.finished'}),
        isA<TtsFinished>(),
      );
    });

    test('agent.interrupted', () {
      expect(
        ServerEvent.fromJson({'type': 'agent.interrupted'}),
        isA<AgentInterrupted>(),
      );
    });

    test('usage is no longer part of the protocol -> unknown event', () {
      // Server-side telemetry only (team decision 2026-08-05): the server
      // logs token counts instead of sending them to the farmer's app.
      final e = ServerEvent.fromJson({'type': 'usage', 'total': 30});
      expect(e, isA<UnknownEvent>());
    });

    test('usage_azure is no longer part of the protocol -> unknown event', () {
      // Server-side telemetry only (2026-08-05), like `usage`: the app stored
      // the character count but never displayed it.
      final e = ServerEvent.fromJson({'type': 'usage_azure', 'chars': 142});
      expect(e, isA<UnknownEvent>());
    });

    test('error', () {
      final e = ServerEvent.fromJson({
        'type': 'error',
        'code': 'bad_audio',
        'message': 'frame too small',
      });
      expect(e, isA<ErrorEvent>());
      expect((e as ErrorEvent).code, 'bad_audio');
      expect(e.message, 'frame too small');
    });

    test('tool.request_photo (defaults target_part when absent)', () {
      final e = ServerEvent.fromJson({
        'type': 'tool.request_photo',
        'call_id': 'c1',
        'reason': 'need a leaf',
      });
      expect(e, isA<ToolRequestPhoto>());
      final t = e as ToolRequestPhoto;
      expect(t.callId, 'c1');
      expect(t.reason, 'need a leaf');
      expect(t.targetPart, 'leaf');
    });

    test('tool.cancelled', () {
      final e = ServerEvent.fromJson({
        'type': 'tool.cancelled',
        'call_ids': ['a', 'b'],
      });
      expect(e, isA<ToolCancelled>());
      expect((e as ToolCancelled).callIds, ['a', 'b']);
    });

    test('photo.received', () {
      final e = ServerEvent.fromJson({
        'type': 'photo.received',
        'photo_id': 'p9',
        'count': 3,
      });
      expect(e, isA<PhotoReceived>());
      final p = e as PhotoReceived;
      expect(p.photoId, 'p9');
      expect(p.count, 3);
    });

    test('diagnosis.started', () {
      final e = ServerEvent.fromJson({
        'type': 'diagnosis.started',
        'case_id': 'case42',
      });
      expect(e, isA<DiagnosisStarted>());
      expect((e as DiagnosisStarted).caseId, 'case42');
    });

    test('case.diagnosis with nested result', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'case42',
        'result': {
          'likely_disease': 'Late blight',
          'confidence': 'high',
          'differentials': [
            {'name': 'Early blight', 'why': 'similar spots'},
          ],
          'immediate_treatment': ['remove leaves', 'apply fungicide'],
          'prevention': ['rotate crops'],
          'spoken_summary': 'Ehtimol fitoftoroz.',
          'language': 'uz-UZ',
        },
        'summary': {'crop': 'tomato'},
      });
      expect(e, isA<CaseDiagnosis>());
      final d = e as CaseDiagnosis;
      expect(d.caseId, 'case42');
      expect(d.result.likelyDisease, 'Late blight');
      expect(d.result.confidence, 'high');
      expect(d.result.differentials.single.name, 'Early blight');
      expect(d.result.differentials.single.why, 'similar spots');
      expect(d.result.immediateTreatment, ['remove leaves', 'apply fungicide']);
      expect(d.result.prevention, ['rotate crops']);
      expect(d.result.spokenSummary, 'Ehtimol fitoftoroz.');
      expect(d.result.language, 'uz-UZ');
      expect(d.result.isPlant, isTrue, reason: 'defaults true when absent');
      expect(d.summary['crop'], 'tomato');
    });

    test('case.diagnosis result without is_plant defaults isPlant=true', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'c1',
        'result': {'likely_disease': 'Late blight', 'confidence': 'high'},
        'summary': {},
      });
      final d = e as CaseDiagnosis;
      expect(d.result.isPlant, isTrue);
    });

    test('case.diagnosis WITH preparations (contract addendum P2.1)', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'case42',
        'result': {
          'likely_disease': 'Un shudring',
          'confidence': 'high',
          'spoken_summary': 'Ehtimol un shudring.',
          'language': 'uz-UZ',
        },
        'summary': {'crop': 'tomato'},
        'preparations': [
          {
            'name': 'NURELL AGRO 55% EM.K',
            'dose_min': 0.75,
            'dose_max': 1.0,
            'unit': 'l/ga',
            'type': 'pest',
            'description': 'Keng taʼsir doirali insektitsid…',
          },
          {
            // String-typed doses (loose backend typing) must coerce to double.
            'name': 'TOPAZ 10% EM.K',
            'dose_min': '0.4',
            'dose_max': '0.4',
            'unit': 'l/ga',
            // Unknown `type` value must be tolerated (passthrough, no crash).
            'type': 'fungicide',
            'description': '',
          },
        ],
      });
      final d = e as CaseDiagnosis;
      expect(d.preparations, hasLength(2));

      final p0 = d.preparations[0];
      expect(p0.name, 'NURELL AGRO 55% EM.K');
      expect(p0.doseMin, 0.75);
      expect(p0.doseMax, 1.0);
      expect(p0.unit, 'l/ga');
      expect(p0.type, 'pest');
      expect(p0.description, 'Keng taʼsir doirali insektitsid…');

      final p1 = d.preparations[1];
      expect(p1.name, 'TOPAZ 10% EM.K');
      expect(p1.doseMin, 0.4);
      expect(p1.doseMax, 0.4);
      expect(p1.type, 'fungicide');
      expect(p1.description, '');
    });

    test('case.diagnosis WITHOUT preparations key -> empty list', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'case42',
        'result': {
          'likely_disease': 'Late blight',
          'confidence': 'high',
          'spoken_summary': 'Ehtimol fitoftoroz.',
          'language': 'uz-UZ',
        },
        'summary': {'crop': 'tomato'},
      });
      final d = e as CaseDiagnosis;
      expect(d.preparations, isEmpty);
    });

    test('case.diagnosis result with is_plant=false parses to false', () {
      final e = ServerEvent.fromJson({
        'type': 'case.diagnosis',
        'case_id': 'c1',
        'result': {
          'is_plant': false,
          'likely_disease': '',
          'confidence': '',
          'spoken_summary': 'Rasmda oʻsimlik koʻrinmadi.',
        },
        'summary': {},
      });
      final d = e as CaseDiagnosis;
      expect(d.result.isPlant, isFalse);
      expect(d.result.spokenSummary, 'Rasmda oʻsimlik koʻrinmadi.');
    });

    test('chat.state parses phase and selections', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.state',
        'chat_id': 'c1',
        'phase': 'guide',
        'selections': {
          'query_type': 'disease_pest',
          'crop_id': '',
          'crop_name': '',
          'plant_part': '',
          'photo_id': '',
        },
      });
      expect(e, isA<ChatStateEvent>());
      final s = e as ChatStateEvent;
      expect(s.chatId, 'c1');
      expect(s.phase, 'guide');
      expect(s.selections['query_type'], 'disease_pest');
      expect(s.selections['crop_id'], '');
    });

    test('chat.question parses step, prompt, kind and options', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'query_type',
        'prompt': 'Qanday muammo boʻyicha yordam kerak?',
        'kind': 'buttons',
        'options': [
          {'id': 'disease_pest', 'label': 'Kasalliklar va zararkunandalar'},
          {'id': 'weed', 'label': 'Begona oʻt'},
        ],
      });
      expect(e, isA<ChatQuestion>());
      final q = e as ChatQuestion;
      expect(q.chatId, 'c1');
      expect(q.stepId, 'query_type');
      expect(q.prompt, 'Qanday muammo boʻyicha yordam kerak?');
      expect(q.kind, 'buttons');
      expect(q.options, hasLength(2));
      expect(q.options.first.id, 'disease_pest');
      expect(q.options.first.label, 'Kasalliklar va zararkunandalar');
      expect(q.options.last.id, 'weed');
      expect(q.options.last.label, 'Begona oʻt');
    });

    // --- v2 (docs/multichat_contract.md §1.2/§1.3/§1.4): new phases, steps
    // and kinds are opaque strings to this layer — expect pure pass-through.

    test('chat.state parses the v2 symptom phase', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.state',
        'chat_id': 'c1',
        'phase': 'symptom',
        'selections': {
          'query_type': 'disease_pest',
          'crop_id': '6a91-growz-uuid',
          'crop_name': 'Pomidor',
          'plant_part': 'leaf',
          'photo_id': '',
        },
      });
      final s = e as ChatStateEvent;
      expect(s.phase, 'symptom');
      expect(s.selections['plant_part'], 'leaf');
    });

    test('chat.state parses the v2 general phase', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.state',
        'chat_id': 'c1',
        'phase': 'general',
        'selections': {
          'query_type': 'general',
          'crop_id': '',
          'crop_name': '',
          'plant_part': '',
          'photo_id': '',
        },
      });
      final s = e as ChatStateEvent;
      expect(s.phase, 'general');
      expect(s.selections['query_type'], 'general');
    });

    test('chat.question — query_type gains the third "general" option', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'query_type',
        'prompt': 'Nima boʻyicha maslahat kerak?',
        'kind': 'buttons',
        'options': [
          {'id': 'disease_pest', 'label': 'Kasalliklar va zararkunandalar'},
          {'id': 'weed', 'label': 'Begona oʻt'},
          {'id': 'general', 'label': 'Umumiy savol berish'},
        ],
      });
      final q = e as ChatQuestion;
      expect(q.options, hasLength(3));
      expect(q.options.last.id, 'general');
      expect(q.options.last.label, 'Umumiy savol berish');
    });

    test('chat.question — crop step carries memory-crop chips before '
        'open_crop_picker', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'crop',
        'prompt': 'Qaysi ekin haqida gaplashamiz?',
        'kind': 'crop_picker',
        'options': [
          {'id': '6a91-growz-uuid', 'label': 'Pomidor'},
          {'id': '83bd-growz-uuid', 'label': 'Bodring'},
          {'id': 'open_crop_picker', 'label': 'Ekinlar'},
        ],
      });
      final q = e as ChatQuestion;
      expect(q.kind, 'crop_picker');
      expect(q.options, hasLength(3));
      expect(q.options[0].id, '6a91-growz-uuid');
      expect(q.options[0].label, 'Pomidor');
      expect(q.options[1].id, '83bd-growz-uuid');
      expect(q.options.last.id, 'open_crop_picker');
      expect(q.options.last.label, 'Ekinlar');
    });

    test('chat.question — symptom step (new kind "symptom")', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'symptom',
        'prompt': 'Belgilar haqida gapirib bering',
        'kind': 'symptom',
        'options': [
          {'id': 'to_photo', 'label': 'Rasmga oʻtish'},
        ],
      });
      final q = e as ChatQuestion;
      expect(q.stepId, 'symptom');
      expect(q.kind, 'symptom');
      expect(q.options.single.id, 'to_photo');
      expect(q.options.single.label, 'Rasmga oʻtish');
    });

    test('chat.question — general step (new kind "free", no options)', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'general',
        'prompt': 'Savolingizni bemalol ayting',
        'kind': 'free',
        'options': <dynamic>[],
      });
      final q = e as ChatQuestion;
      expect(q.stepId, 'general');
      expect(q.kind, 'free');
      expect(q.options, isEmpty);
    });

    test('chat.question — diag_offer step (buttons)', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'diag_offer',
        'prompt': 'Aniqlash jarayonini boshlaymizmi?',
        'kind': 'buttons',
        'options': [
          {'id': 'switch_diag', 'label': 'Ha, aniqlaymiz'},
          {'id': 'stay_general', 'label': 'Yoʻq, davom etamiz'},
        ],
      });
      final q = e as ChatQuestion;
      expect(q.stepId, 'diag_offer');
      expect(q.options.first.id, 'switch_diag');
      expect(q.options.first.label, 'Ha, aniqlaymiz');
      expect(q.options.last.id, 'stay_general');
      expect(q.options.last.label, 'Yoʻq, davom etamiz');
    });

    test('chat.step — v2 new step ids round-trip (symptom/diag_offer/'
        'query_type=general)', () {
      final symptomDone = ServerEvent.fromJson({
        'type': 'chat.step',
        'chat_id': 'c1',
        'step_id': 'symptom',
        'option_id': 'to_photo',
        'value': 'Barglarda sargʻayish, 3 kundan beri.',
        'label': 'Rasmga oʻtish',
      }) as ChatStepAck;
      expect(symptomDone.stepId, 'symptom');
      expect(symptomDone.optionId, 'to_photo');
      expect(symptomDone.label, 'Rasmga oʻtish');

      final switchDiag = ServerEvent.fromJson({
        'type': 'chat.step',
        'chat_id': 'c1',
        'step_id': 'diag_offer',
        'option_id': 'switch_diag',
        'value': '',
        'label': 'Ha, aniqlaymiz',
      }) as ChatStepAck;
      expect(switchDiag.stepId, 'diag_offer');
      expect(switchDiag.optionId, 'switch_diag');

      final stayGeneral = ServerEvent.fromJson({
        'type': 'chat.step',
        'chat_id': 'c1',
        'step_id': 'diag_offer',
        'option_id': 'stay_general',
        'value': '',
        'label': 'Yoʻq, davom etamiz',
      }) as ChatStepAck;
      expect(stayGeneral.stepId, 'diag_offer');
      expect(stayGeneral.optionId, 'stay_general');

      final generalAccepted = ServerEvent.fromJson({
        'type': 'chat.step',
        'chat_id': 'c1',
        'step_id': 'query_type',
        'option_id': 'general',
        'value': '',
        'label': 'Umumiy savol berish',
      }) as ChatStepAck;
      expect(generalAccepted.stepId, 'query_type');
      expect(generalAccepted.optionId, 'general');
      expect(generalAccepted.label, 'Umumiy savol berish');
    });

    test('chat.question defaults options to empty when absent', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.question',
        'chat_id': 'c1',
        'step_id': 'photo',
        'prompt': 'Kasallangan qismning rasmini yuboring',
        'kind': 'photo',
      });
      expect((e as ChatQuestion).options, isEmpty);
    });

    test('chat.step parses the accepted answer ack', () {
      final e = ServerEvent.fromJson({
        'type': 'chat.step',
        'chat_id': 'c1',
        'step_id': 'crop',
        'option_id': '6a91-growz-uuid',
        'value': 'Pomidor',
        'label': 'Pomidor',
      });
      expect(e, isA<ChatStepAck>());
      final s = e as ChatStepAck;
      expect(s.chatId, 'c1');
      expect(s.stepId, 'crop');
      expect(s.optionId, '6a91-growz-uuid');
      expect(s.value, 'Pomidor');
      expect(s.label, 'Pomidor');
    });

    test('unknown type → UnknownEvent (ignored gracefully)', () {
      final e = ServerEvent.fromJson({'type': 'intent.partial', 'data': {}});
      expect(e, isA<UnknownEvent>());
      expect((e as UnknownEvent).type, 'intent.partial');
    });

    test('missing type → UnknownEvent', () {
      final e = ServerEvent.fromJson({'foo': 'bar'});
      expect(e, isA<UnknownEvent>());
      expect((e as UnknownEvent).type, '');
    });
  });

  group('ClientEvent builders round-trip', () {
    test('chat.start carries language only — rate/voice are server settings', () {
      const e = ChatStartRequest();
      expect(e.toJson(), {
        'type': 'chat.start',
        'language': 'uz-UZ',
      });
      // Renamed from session.start and slimmed down (2026-08-05): the server
      // takes the mic rate and TTS voice from its own .env.
      expect(e.toJson().containsKey('sample_rate'), false);
      expect(e.toJson().containsKey('voice'), false);
    });

    test('chat.start carries user_id when provided', () {
      const e = ChatStartRequest(userId: 'abc-123-def');
      expect(e.toJson()['user_id'], 'abc-123-def');
    });

    test('chat.start carries crop + GPS enrichment when provided', () {
      const e = ChatStartRequest(
        cropId: 'u-42', cropName: 'Pomidor', lat: 41.31, lon: 69.28,
      );
      final j = e.toJson();
      expect(j['crop_id'], 'u-42');
      expect(j['crop_name'], 'Pomidor');
      expect(j['lat'], 41.31);
      expect(j['lon'], 69.28);
    });

    test('chat.start omits crop + GPS when absent', () {
      final j = const ChatStartRequest().toJson();
      expect(j.containsKey('crop_id'), false);
      expect(j.containsKey('crop_name'), false);
      expect(j.containsKey('lat'), false);
      expect(j.containsKey('lon'), false);
    });

    test('chat.start omits GPS unless BOTH lat and lon are present', () {
      expect(
        const ChatStartRequest(lat: 41.0).toJson().containsKey('lat'),
        false,
      );
      expect(
        const ChatStartRequest(lon: 69.0).toJson().containsKey('lon'),
        false,
      );
    });

    test('chat.start omits user_id when null or empty', () {
      expect(const ChatStartRequest().toJson().containsKey('user_id'), false);
      expect(
        const ChatStartRequest(userId: '').toJson().containsKey('user_id'),
        false,
      );
    });

    test('chat.start carries chat_id when provided', () {
      const e = ChatStartRequest(chatId: '9f2c4e61a7b84d0f8a3c5e7d9b1f2a4c');
      expect(e.toJson()['chat_id'], '9f2c4e61a7b84d0f8a3c5e7d9b1f2a4c');
    });

    test('chat.start omits chat_id when null or empty', () {
      expect(const ChatStartRequest().toJson().containsKey('chat_id'), false);
      expect(
        const ChatStartRequest(chatId: '').toJson().containsKey('chat_id'),
        false,
      );
    });

    test('user.interrupt', () {
      expect(const UserInterrupt().toJson(), {'type': 'user.interrupt'});
    });

    // "session.end" was removed (2026-08-05) — closing the socket is the
    // hangup signal, so there is no client event left to round-trip.

    test('stt.corrected parses text and orig', () {
      final e = ServerEvent.fromJson({
        'type': 'stt.corrected',
        'text': 'Mening ismim Rustam',
        'orig': 'tam',
      });
      expect(e, isA<SttCorrected>());
      e as SttCorrected;
      expect(e.text, 'Mening ismim Rustam');
      expect(e.orig, 'tam');
    });

    test('session.expired parses with message', () {
      final e = ServerEvent.fromJson({
        'type': 'session.expired',
        'message': 'Suhbat vaqti tugadi',
      });
      expect(e, isA<SessionExpired>());
      expect((e as SessionExpired).message, 'Suhbat vaqti tugadi');
    });

    test('text.input carries the typed message as value', () {
      expect(
        const TextInputRequest(text: 'Salom, pomidorim kasal').toJson(),
        {'type': 'text.input', 'value': 'Salom, pomidorim kasal'},
      );
    });

    test('photo.upload carries only the URL — bytes went over REST', () {
      // 2026-08-05 protocol: base64/mime/width/height/origin are gone.
      const e = PhotoUploadRequest(
        photoId: 'p1',
        value: 'https://cdn.example/p1.jpg',
        chatId: 'chat-1',
      );
      expect(e.toJson(), {
        'type': 'photo.upload',
        'chat_id': 'chat-1',
        'photo_id': 'p1',
        'value': 'https://cdn.example/p1.jpg',
      });
    });

    test('photo.upload omits chat_id when unbound', () {
      const e = PhotoUploadRequest(photoId: 'p1', value: 'https://x/p1.jpg');
      expect(e.toJson().containsKey('chat_id'), isFalse);
      expect(e.toJson().containsKey('data'), isFalse);
      expect(e.toJson().containsKey('target_part'), isFalse);
    });

    // camera.cancelled left the protocol (2026-08-05): request_photo is acked
    // immediately server-side, so closing the camera is a local UI action.

    test('chat.answer on the crop step carries the crop as an object', () {
      const e = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'crop',
        optionId: 'open_crop_picker',
        cropId: '6a91-growz-uuid',
        cropName: 'Pomidor',
      );
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'crop',
        'option_id': 'open_crop_picker',
        'value': '',
        'crop': {'id': '6a91-growz-uuid', 'name': 'Pomidor'},
      });
    });

    test('chat.answer (buttons/crop_picker answer)', () {
      const e = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'crop',
        optionId: '6a91-growz-uuid',
        value: 'Pomidor',
      );
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'crop',
        'option_id': '6a91-growz-uuid',
        'value': 'Pomidor',
      });
    });

    test('chat.answer defaults option_id and value to empty', () {
      const e = ChatAnswerRequest(chatId: 'c1', stepId: 'query_type');
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'query_type',
        'option_id': '',
        'value': '',
      });
    });

    // --- v2 (docs/multichat_contract.md §1.5): new accepted step ids/values.
    // The builder's shape is unchanged; only the values it carries are new.

    test('chat.answer — query_type "general" (third option)', () {
      const e = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'query_type',
        optionId: 'general',
      );
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'query_type',
        'option_id': 'general',
        'value': '',
      });
    });

    test('chat.answer — symptom step "to_photo" (Rasmga oʻtish tap)', () {
      const e = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'symptom',
        optionId: 'to_photo',
      );
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'symptom',
        'option_id': 'to_photo',
        'value': '',
      });
    });

    test('chat.answer — diag_offer accept/decline', () {
      const accept = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'diag_offer',
        optionId: 'switch_diag',
      );
      expect(accept.toJson()['option_id'], 'switch_diag');

      const decline = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'diag_offer',
        optionId: 'stay_general',
      );
      expect(decline.toJson()['option_id'], 'stay_general');
    });

    test('chat.answer — crop step accepts a memory-chip Growz UUID', () {
      const e = ChatAnswerRequest(
        chatId: 'c1',
        stepId: 'crop',
        optionId: '6a91-growz-uuid',
        value: 'Pomidor',
      );
      expect(e.toJson(), {
        'type': 'chat.answer',
        'chat_id': 'c1',
        'step_id': 'crop',
        'option_id': '6a91-growz-uuid',
        'value': 'Pomidor',
      });
    });
  });
}
