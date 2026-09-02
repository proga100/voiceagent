/// Plant Doctor — Alomat voice assistant for Uzbek farmers.
///
/// Phase 3 is a thin realtime client: one WebSocket carries mic audio up and
/// agent PCM down, with a live transcript. All AI runs on the backend.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'features/chat/chat_list_screen.dart';
import 'features/chat/chat_providers.dart';
import 'features/interview/interview_screen.dart';

void main() {
  runApp(const ProviderScope(child: PlantDoctorApp()));
}

/// Dark MaterialApp. Home is the chat list; opening/creating a chat mounts
/// the interview.
class PlantDoctorApp extends StatelessWidget {
  const PlantDoctorApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Alomat',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF3DDC84),
          brightness: Brightness.dark,
        ),
      ),
      home: const _HomeGate(),
    );
  }
}

/// Top-level flow: the chat list until a chat is active, then the interview.
/// Ending the call (`VoiceSessionController.stop`) clears [activeChatProvider],
/// so the interview remounts fresh on the next chat, and its auto-start fires
/// again.
class _HomeGate extends ConsumerWidget {
  const _HomeGate();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final active = ref.watch(activeChatProvider);
    return active == null ? const ChatListScreen() : const InterviewScreen();
  }
}
