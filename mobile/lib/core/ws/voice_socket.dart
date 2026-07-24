/// The single realtime transport to the backend.
///
/// One [WebSocketChannel] multiplexes:
///  * text frames  -> decoded into [ServerEvent]s ([events])
///  * binary frames -> raw agent PCM ([audio])
///  * client sends  -> [send] (JSON) and [sendAudio] (mic PCM)
///
/// On an *unexpected* drop it auto-reconnects with a short backoff (3 tries)
/// and surfaces the [SocketConnectionState] so the UI can reflect it. A caller
/// initiated [disconnect] never reconnects.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:web_socket_channel/status.dart' as ws_status;
import 'package:web_socket_channel/web_socket_channel.dart';

import '../protocol/events.dart';

/// Lifecycle of the voice socket, surfaced to the UI.
enum SocketConnectionState {
  /// No socket, or cleanly closed by the client.
  disconnected,

  /// First connection attempt in flight.
  connecting,

  /// Connected and streaming.
  connected,

  /// A drop occurred and a retry is scheduled/in flight.
  reconnecting,

  /// All retries exhausted.
  failed,
}

/// Owns the WebSocket and demultiplexes its frames.
class VoiceSocket {
  VoiceSocket({required this.baseUrl, this.token});

  /// The `ws://host:port/ws/voice` URL, without the token query.
  final String baseUrl;

  /// Optional auth token appended as `?token=`.
  final String? token;

  static const int _maxRetries = 3;
  static const List<Duration> _backoff = [
    Duration(milliseconds: 500),
    Duration(seconds: 1),
    Duration(seconds: 2),
  ];

  final StreamController<ServerEvent> _events =
      StreamController<ServerEvent>.broadcast();
  final StreamController<Uint8List> _audio =
      StreamController<Uint8List>.broadcast();
  final StreamController<SocketConnectionState> _state =
      StreamController<SocketConnectionState>.broadcast();

  /// Decoded JSON control events from the backend.
  Stream<ServerEvent> get events => _events.stream;

  /// Raw agent voice PCM (24 kHz mono 16-bit), frames may split mid-sample.
  Stream<Uint8List> get audio => _audio.stream;

  /// Connection lifecycle updates.
  Stream<SocketConnectionState> get connectionState => _state.stream;

  /// The latest connection state (also available via [connectionState]).
  SocketConnectionState get current => _current;
  SocketConnectionState _current = SocketConnectionState.disconnected;

  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _sub;
  Timer? _retryTimer;
  bool _closedByUser = false;
  int _retry = 0;

  Uri get _uri {
    final u = Uri.parse(baseUrl);
    if (token == null || token!.isEmpty) return u;
    return u.replace(queryParameters: {...u.queryParameters, 'token': token!});
  }

  /// Connects and begins listening. Safe to call once per [VoiceSocket].
  Future<void> connect() async {
    _closedByUser = false;
    _retry = 0;
    await _open();
  }

  Future<void> _open() async {
    _emit(
      _retry == 0
          ? SocketConnectionState.connecting
          : SocketConnectionState.reconnecting,
    );
    try {
      final channel = WebSocketChannel.connect(_uri);
      await channel.ready;
      _channel = channel;
      _retry = 0;
      _emit(SocketConnectionState.connected);
      _sub = channel.stream.listen(
        _onData,
        onError: (Object e, StackTrace _) => _onDrop(),
        onDone: _onDrop,
        cancelOnError: false,
      );
    } catch (_) {
      _onDrop();
    }
  }

  void _onData(dynamic data) {
    if (data is String) {
      _handleText(data);
    } else if (data is Uint8List) {
      _audio.add(data);
    } else if (data is List<int>) {
      _audio.add(Uint8List.fromList(data));
    }
  }

  void _handleText(String text) {
    try {
      final decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        _events.add(ServerEvent.fromJson(decoded));
      }
    } catch (_) {
      // Ignore malformed / non-object text frames.
    }
  }

  void _onDrop() {
    _tearDownChannel();
    if (_closedByUser) {
      _emit(SocketConnectionState.disconnected);
      return;
    }
    if (_retry >= _maxRetries) {
      _emit(SocketConnectionState.failed);
      return;
    }
    final delay = _backoff[_retry.clamp(0, _backoff.length - 1)];
    _retry++;
    _emit(SocketConnectionState.reconnecting);
    _retryTimer?.cancel();
    _retryTimer = Timer(delay, () {
      if (!_closedByUser) _open();
    });
  }

  void _tearDownChannel() {
    _sub?.cancel();
    _sub = null;
    _channel = null;
  }

  /// Sends a JSON control event.
  void send(ClientEvent event) {
    _channel?.sink.add(jsonEncode(event.toJson()));
  }

  /// Sends one raw mic PCM frame (binary).
  void sendAudio(Uint8List frame) {
    _channel?.sink.add(frame);
  }

  /// Sends a raw JSON map. Used only by flag-gated audio telemetry
  /// (`{"type":"debug.log","msg":...}`); production callers use [send].
  void sendRaw(Map<String, dynamic> map) {
    _channel?.sink.add(jsonEncode(map));
  }

  void _emit(SocketConnectionState s) {
    _current = s;
    if (!_state.isClosed) _state.add(s);
  }

  /// Closes the socket without reconnecting.
  Future<void> disconnect() async {
    _closedByUser = true;
    _retryTimer?.cancel();
    await _sub?.cancel();
    _sub = null;
    await _channel?.sink.close(ws_status.normalClosure);
    _channel = null;
    _emit(SocketConnectionState.disconnected);
  }

  /// Closes the socket and releases all stream controllers.
  Future<void> dispose() async {
    await disconnect();
    await _events.close();
    await _audio.close();
    await _state.close();
  }
}
