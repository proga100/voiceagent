/// Token-batching semantics of the transcript (audio-stutter fix): a fast
/// `llm.token` stream must rebuild [state] at ~10 Hz, not per token, and no
/// text may be lost or delayed at a turn boundary.
library;

import 'package:fake_async/fake_async.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:plant_doctor/features/interview/transcript_provider.dart';

void main() {
  ProviderContainer container() {
    final c = ProviderContainer();
    addTearDown(c.dispose);
    return c;
  }

  test('addFarmerText appends a final farmer bubble and closes open turns', () {
    final c = container();
    final n = c.read(transcriptProvider.notifier);

    n.onSttPartial('eski partial');
    n.addFarmerText('Salom, pomidorim kasal');

    final bubbles = c.read(transcriptProvider);
    final typed = bubbles.whereType<FarmerBubble>().last;
    expect(typed.text, 'Salom, pomidorim kasal');
    expect(typed.isFinal, true);
    // The previously open (partial) farmer bubble was finalized, not merged.
    final partial = bubbles.whereType<FarmerBubble>().first;
    expect(partial.isFinal, true);
  });

  test('correctFarmer patches the right bubble by content, suffix-tolerant', () {
    final c = container();
    final n = c.read(transcriptProvider.notifier);

    // Turn 1: the bubble holds only the TAIL fragment of the utterance.
    n.onSttPartial('tam');
    n.onLlmToken('Salom Rustam!');
    n.onAgentDone();
    // Turn 2 opens before the (late) correction arrives.
    n.onSttPartial('pomidorim kasal');

    n.correctFarmer('tam', 'Mening ismim Rustam');

    final farmers = c.read(transcriptProvider).whereType<FarmerBubble>().toList();
    expect(farmers.first.text, 'Mening ismim Rustam'); // old bubble upgraded
    expect(farmers.last.text, 'pomidorim kasal');      // newer turn untouched
  });

  test('correctFarmer with no matching bubble is a no-op', () {
    final c = container();
    final n = c.read(transcriptProvider.notifier);
    n.onSttPartial('salom');
    n.correctFarmer('butunlay boshqa matn', 'x');
    final farmers = c.read(transcriptProvider).whereType<FarmerBubble>().toList();
    expect(farmers.single.text, 'salom');
  });

  test('rapid tokens coalesce into one state update per flush window', () {
    fakeAsync((async) {
      final c = container();
      final n = c.read(transcriptProvider.notifier);
      var rebuilds = 0;
      c.listen(transcriptProvider, (_, _) => rebuilds++);

      for (var i = 0; i < 30; i++) {
        n.onLlmToken('t$i ');
        async.elapse(const Duration(milliseconds: 3)); // 30 tokens in ~90ms
      }
      final rebuildsBeforeFlush = rebuilds;
      async.elapse(const Duration(milliseconds: 120)); // let the timer fire

      // Far fewer rebuilds than tokens (one open-bubble + ~1-2 flushes).
      expect(rebuilds, lessThan(6));
      expect(rebuilds, greaterThan(rebuildsBeforeFlush - 1));
      final agent = c.read(transcriptProvider).whereType<AgentBubble>().single;
      expect(agent.text, [for (var i = 0; i < 30; i++) 't$i '].join());
    });
  });

  test('turn end flushes synchronously — no text lost, no 100ms tail wait',
      () {
    fakeAsync((async) {
      final c = container();
      final n = c.read(transcriptProvider.notifier);

      n.onLlmToken('salom ');
      n.onLlmToken('dunyo');
      // No timer elapse: buffer not yet flushed. End the turn immediately.
      n.onAgentDone();

      final agent = c.read(transcriptProvider).whereType<AgentBubble>().single;
      expect(agent.text, 'salom dunyo');
      // Nothing further pending: elapsing time must not change state.
      final before = c.read(transcriptProvider);
      async.elapse(const Duration(seconds: 1));
      expect(identical(before, c.read(transcriptProvider)), isTrue);
    });
  });

  test('farmer partial arriving mid-buffer finalizes the agent text first',
      () {
    fakeAsync((async) {
      final c = container();
      final n = c.read(transcriptProvider.notifier);

      n.onLlmToken('javob');
      n.onSttPartial('yangi savol'); // farmer speaks -> agent turn boundary

      final bubbles = c.read(transcriptProvider);
      expect(bubbles.whereType<AgentBubble>().single.text, 'javob');
      expect(
        bubbles.whereType<FarmerBubble>().single.text,
        'yangi savol',
      );
    });
  });
}

