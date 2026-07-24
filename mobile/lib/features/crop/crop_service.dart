/// Fetches the Growz crop catalogue from the backend's `GET /crops` proxy.
///
/// The Growz API key lives server-side; the app only ever sees `{data:[...]}`.
/// A short timeout keeps the picker snappy; any failure surfaces as an
/// exception the UI turns into a retryable empty state.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/config.dart';
import 'crop.dart';

class CropService {
  const CropService();

  Future<List<Crop>> fetchCrops() async {
    final uri = Uri.parse('$httpBaseUrl/crops');
    final resp = await http
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 12));
    if (resp.statusCode != 200) {
      throw Exception('crops HTTP ${resp.statusCode}');
    }
    final body = json.decode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final data = (body['data'] as List<dynamic>? ?? const []);
    return data
        .map((e) => Crop.fromJson(e as Map<String, dynamic>))
        .where((c) => c.id.isNotEmpty && c.name.isNotEmpty)
        .toList();
  }
}
