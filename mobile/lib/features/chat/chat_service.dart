/// Fetches/creates chats from the backend's `/chats` REST endpoints
/// (`docs/multichat_contract.md` §2). Mirrors `CropService`: plain `http`,
/// 12 s timeout, throws on non-200 — the UI turns failures into a retryable
/// state (list) or a fail-open plain session (new chat) per the contract.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/config.dart';
import 'chat.dart';

class ChatService {
  const ChatService();

  Future<List<ChatSummary>> listChats(String userId, {int limit = 50}) async {
    final uri = Uri.parse('$httpBaseUrl/chats').replace(
      queryParameters: {'user_id': userId, 'limit': '$limit'},
    );
    final resp = await http
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw Exception('chats HTTP ${resp.statusCode}');
    }
    final body =
        json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final data = (body['data'] as List<dynamic>? ?? const []);
    return data
        .map((e) => ChatSummary.fromJson(e as Map<String, dynamic>))
        .toList(growable: false);
  }

  Future<ChatSummary> createChat(String userId) async {
    final uri = Uri.parse('$httpBaseUrl/chats');
    final resp = await http
        .post(
          uri,
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          body: json.encode({'user_id': userId}),
        )
        .timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw Exception('POST /chats HTTP ${resp.statusCode}');
    }
    final body =
        json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ChatSummary.fromJson(body['data'] as Map<String, dynamic>);
  }

  Future<ChatDetail> getChat(String chatId, String userId) async {
    final uri = Uri.parse(
      '$httpBaseUrl/chats/$chatId',
    ).replace(queryParameters: {'user_id': userId});
    final resp = await http
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw Exception('chat HTTP ${resp.statusCode}');
    }
    final body =
        json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ChatDetail.fromJson(body['data'] as Map<String, dynamic>);
  }

  /// Spec §7.1 — `POST /chats/{chatId}/agronom-request` (contract addendum
  /// P3.3, P3.7). The farmer-facing "Agronomga yuborish" button. Idempotent
  /// server-side; the staff review-submit endpoint has no mobile client.
  Future<ChatSummary> requestAgronomReview(String chatId, String userId) async {
    final uri = Uri.parse('$httpBaseUrl/chats/$chatId/agronom-request');
    final resp = await http
        .post(
          uri,
          headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
          },
          body: json.encode({'user_id': userId}),
        )
        .timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw Exception('agronom-request HTTP ${resp.statusCode}');
    }
    final body =
        json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ChatSummary.fromJson(body['data'] as Map<String, dynamic>);
  }
}
