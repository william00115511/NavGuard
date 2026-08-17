import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

const _fallbackLocation = LatLng(25.0330, 121.5654);
const _apiBaseUrl = String.fromEnvironment('API_BASE_URL');
const _demoGoogleMapsUrl =
    'https://www.google.com/maps/dir/%E5%8F%B0%E5%8C%97101%E8%B3%BC%E7%89%A9%E4%B8%AD%E5%BF%83+110%E8%87%BA%E5%8C%97%E5%B8%82%E4%BF%A1%E7%BE%A9%E5%8D%80%E8%A5%BF%E6%9D%91%E9%87%8C%E5%B8%82%E5%BA%9C%E8%B7%AF45+%E8%99%9F/%E5%8F%B0%E4%B8%AD+414%E8%87%BA%E4%B8%AD%E5%B8%82%E7%83%8F%E6%97%A5%E5%8D%80/%E6%97%A5%E6%9C%88%E6%BD%AD+555%E5%8D%97%E6%8A%95%E7%B8%A3%E9%AD%9A%E6%B1%A0%E9%84%89/%E9%AB%98%E9%9B%84+%E9%AB%98%E9%9B%84%E5%B8%82%E9%BC%93%E5%B1%B1%E5%8D%80%E9%BE%8D%E5%AD%90%E9%87%8C/@23.8275968,119.5984759,8z/data=!4m26!4m25!1m5!1m1!1s0x3442abb6da80a7ad:0xacc4d11dc963103c!2m2!1d121.5640212!2d25.0341222!1m5!1m1!1s0x34693ea3df35917f:0xc0c95f36683eb0ad!2m2!1d120.61419!2d24.11006!1m5!1m1!1s0x3468d5e076ee0005:0xec17a6fd5312a528!2m2!1d120.9159131!2d23.8573342!1m5!1m1!1s0x346e04fd209f4835:0x54511e7d86c87c09!2m2!1d120.3014375!2d22.6272772!3e2?entry=ttu&g_ep=EgoyMDI2MDgxMi4wIKXMDSoASAFQAw%3D%3D';

void main() => runApp(const SafewayApp());

class SafewayApp extends StatelessWidget {
  const SafewayApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'Safeway',
    theme: ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xffc39a4b),
        brightness: Brightness.dark,
      ),
    ),
    home: const SafeNavigationScreen(),
  );
}

class SafeNavigationScreen extends StatefulWidget {
  const SafeNavigationScreen({super.key});

  @override
  State<SafeNavigationScreen> createState() => _SafeNavigationScreenState();
}

class _SafeNavigationScreenState extends State<SafeNavigationScreen> {
  final _api = SafewayChatApi();
  final _input = TextEditingController();
  final _chatSheetController = DraggableScrollableController();
  final _chatRevision = ValueNotifier<int>(0);
  final _messages = <ChatMessage>[
    ChatMessage.assistant('晚上好，我是 Safeway。告訴我你想從哪裡走到哪裡；我會依公開資料提供較安全的步行建議。'),
  ];
  GoogleMapController? _map;
  LatLng _location = _fallbackLocation;
  String? _sessionId;
  RouteReadyResponse? _routeResponse;
  SafeRoute? _selectedRoute;
  bool _waiting = false;
  bool _isChatSheetOpen = false;

  @override
  void initState() {
    super.initState();
    _getLocationAndSession();
    WidgetsBinding.instance.addPostFrameCallback((_) => _showChatSheet());
  }

  @override
  void dispose() {
    _input.dispose();
    _chatSheetController.dispose();
    _chatRevision.dispose();
    _map?.dispose();
    super.dispose();
  }

  Future<void> _getLocationAndSession() async {
    try {
      if (await Geolocator.isLocationServiceEnabled()) {
        var permission = await Geolocator.checkPermission();
        if (permission == LocationPermission.denied) {
          permission = await Geolocator.requestPermission();
        }
        if (permission != LocationPermission.denied &&
            permission != LocationPermission.deniedForever) {
          final position = await Geolocator.getCurrentPosition();
          _location = LatLng(position.latitude, position.longitude);
          await _map?.animateCamera(CameraUpdate.newLatLngZoom(_location, 15));
        }
      }
    } catch (_) {
      // GPS is optional: the backend can still ask the user for an origin.
    }
    if (!mounted) return;
    setState(() {});
    await _createSession();
  }

  Future<void> _createSession() async {
    try {
      final session = await _api.createSession(_location);
      if (mounted) setState(() => _sessionId = session.id);
    } on ChatApiException catch (error) {
      if (mounted && _api.isConfigured) _showSnackbar(error.message);
    }
  }

  Future<void> _sendMessage() async {
    final text = _input.text.trim();
    if (text.isEmpty || _waiting) return;
    _input.clear();
    setState(() {
      _messages.add(ChatMessage.user(text));
      _waiting = true;
    });
    _chatRevision.value++;
    try {
      if (_sessionId == null && _api.isConfigured) await _createSession();
      final response = await _api.sendMessage(
        sessionId: _sessionId,
        message: text,
        location: _location,
      );
      if (!mounted) return;
      setState(() {
        _messages.add(ChatMessage.assistant(response.replyText));
        if (response is RouteReadyResponse) {
          _routeResponse = response;
          _selectedRoute = response.selectedRoute;
        }
      });
      _chatRevision.value++;
      if (response is RouteReadyResponse) {
        await _focusRoutes(response.routes);
        if (_isChatSheetOpen && mounted) Navigator.of(context).pop();
      }
    } on ChatApiException catch (error) {
      if (mounted) {
        setState(
          () => _messages.add(
            ChatMessage.assistant(error.message, isError: true),
          ),
        );
        _chatRevision.value++;
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _messages.add(
            ChatMessage.assistant('連線暫時失敗，請稍後再試。', isError: true),
          ),
        );
        _chatRevision.value++;
      }
    } finally {
      if (mounted) {
        setState(() => _waiting = false);
        _chatRevision.value++;
      }
    }
  }

  Future<void> _focusRoutes(List<SafeRoute> routes) async {
    final points = routes.expand((route) => route.path).toList();
    if (points.length < 2) return;
    await _map?.animateCamera(
      CameraUpdate.newLatLngBounds(_bounds(points), 72),
    );
  }

  Future<void> _openGoogleMaps() async {
    final url = _routeResponse?.googleMapsUrl ?? _demoGoogleMapsUrl;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('在 Google Maps 開啟'),
        content: const Text(
          'Google Maps 會依自己的演算法重新規劃，實際路線可能與 Safeway 的推薦路徑略有不同。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('繼續'),
          ),
        ],
      ),
    );
    if (accepted == true &&
        !await launchUrl(
          Uri.parse(url),
          mode: LaunchMode.externalApplication,
        ) &&
        mounted) {
      _showSnackbar('無法開啟 Google Maps。');
    }
  }

  void _showSnackbar(String message) => ScaffoldMessenger.of(
    context,
  ).showSnackBar(SnackBar(content: Text(message)));

  @override
  Widget build(BuildContext context) {
    final response = _routeResponse;
    return Scaffold(
      body: Stack(
        children: [
          GoogleMap(
            initialCameraPosition: const CameraPosition(
              target: _fallbackLocation,
              zoom: 14,
            ),
            onMapCreated: (controller) => _map = controller,
            myLocationEnabled: true,
            myLocationButtonEnabled: false,
            zoomControlsEnabled: false,
            polylines: {
              for (final route in response?.routes ?? <SafeRoute>[])
                Polyline(
                  polylineId: PolylineId(route.id),
                  points: route.path,
                  width: route == _selectedRoute ? 7 : 4,
                  color: route.id == 'fastest'
                      ? const Color(0xff677a89)
                      : const Color(0xffc39a4b),
                  patterns: route == _selectedRoute
                      ? const []
                      : [PatternItem.dot, PatternItem.gap(10)],
                ),
            },
            markers: {
              Marker(
                markerId: const MarkerId('current-location'),
                position: _location,
                infoWindow: const InfoWindow(title: '目前位置'),
              ),
              if (_selectedRoute?.path.isNotEmpty == true)
                Marker(
                  markerId: const MarkerId('destination'),
                  position: _selectedRoute!.path.last,
                  infoWindow: const InfoWindow(title: '目的地'),
                ),
            },
          ),
          Positioned(
            right: 16,
            top: 110,
            child: FloatingActionButton.small(
              onPressed: _getLocationAndSession,
              child: const Icon(Icons.my_location),
            ),
          ),
          if (response != null)
            Align(
              alignment: Alignment.bottomCenter,
              child: _RouteSummary(
                response: response,
                selected: _selectedRoute!,
                onSelect: (route) => setState(() => _selectedRoute = route),
                onOpenGoogleMaps: _openGoogleMaps,
                onContinueChat: _showChatSheet,
              ),
            ),
          Positioned(
            right: 16,
            top: 170,
            child: FloatingActionButton.small(
              heroTag: 'chat',
              tooltip: '開啟對話',
              onPressed: _showChatSheet,
              child: const Icon(Icons.chat_bubble_outline),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _showChatSheet() async {
    if (_isChatSheetOpen || !mounted) return;
    setState(() => _isChatSheetOpen = true);
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      isDismissible: true,
      enableDrag: true,
      useSafeArea: false,
      backgroundColor: Colors.transparent,
      builder: (sheetContext) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () {
          FocusScope.of(sheetContext).unfocus();
          Navigator.of(sheetContext).pop();
        },
        child: AnimatedPadding(
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
          padding: EdgeInsets.only(
            bottom: MediaQuery.viewInsetsOf(sheetContext).bottom,
          ),
          child: Align(
            alignment: Alignment.bottomCenter,
            child: GestureDetector(
              onTap: () => FocusScope.of(sheetContext).unfocus(),
              child: _ChatPanel(
                messages: _messages,
                controller: _input,
                chatRevision: _chatRevision,
                isWaiting: () => _waiting,
                sheetController: _chatSheetController,
                onInputTap: _expandChatSheet,
                onSend: _sendMessage,
              ),
            ),
          ),
        ),
      ),
    );
    if (mounted) setState(() => _isChatSheetOpen = false);
  }

  void _expandChatSheet() {
    if (!_chatSheetController.isAttached) return;
    _chatSheetController.animateTo(
      .9,
      duration: const Duration(milliseconds: 220),
      curve: Curves.easeOut,
    );
  }
}

class _ChatPanel extends StatelessWidget {
  const _ChatPanel({
    required this.messages,
    required this.controller,
    required this.chatRevision,
    required this.isWaiting,
    required this.sheetController,
    required this.onInputTap,
    required this.onSend,
  });
  final List<ChatMessage> messages;
  final TextEditingController controller;
  final ValueListenable<int> chatRevision;
  final bool Function() isWaiting;
  final DraggableScrollableController sheetController;
  final VoidCallback onInputTap;
  final VoidCallback onSend;

  @override
  Widget build(BuildContext context) {
    final keyboardVisible = MediaQuery.viewInsetsOf(context).bottom > 0;
    void resizeSheet(DragUpdateDetails details) {
      if (!sheetController.isAttached || details.primaryDelta == null) return;
      if (keyboardVisible) {
        if (details.primaryDelta! > 0) FocusScope.of(context).unfocus();
        return;
      }
      final height = MediaQuery.sizeOf(context).height;
      final next = (sheetController.size - details.primaryDelta! / height)
          .clamp(.4, .9)
          .toDouble();
      sheetController.jumpTo(next);
    }

    void settleSheet(DragEndDetails details) {
      if (!sheetController.isAttached || keyboardVisible) return;
      final velocity = details.primaryVelocity ?? 0;
      final target =
          velocity < -350 ||
              (velocity.abs() <= 350 && sheetController.size >= .65)
          ? .9
          : .4;
      sheetController.animateTo(
        target,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
      );
    }

    return DraggableScrollableSheet(
      controller: sheetController,
      expand: false,
      initialChildSize: .4,
      minChildSize: .4,
      maxChildSize: .9,
      snap: true,
      snapSizes: const [.4, .9],
      builder: (context, scrollController) => GestureDetector(
        behavior: HitTestBehavior.translucent,
        onVerticalDragUpdate: resizeSheet,
        onVerticalDragEnd: settleSheet,
        child: Container(
          padding: EdgeInsets.fromLTRB(
            16,
            8,
            16,
            MediaQuery.paddingOf(context).bottom + 10,
          ),
          decoration: const BoxDecoration(
            color: Color(0xff17150f),
            borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
          ),
          child: Column(
            children: [
              Row(
                children: [
                  Expanded(
                    child: Center(
                      child: Container(
                        width: 42,
                        height: 4,
                        margin: const EdgeInsets.symmetric(vertical: 12),
                        decoration: BoxDecoration(
                          color: Colors.white38,
                          borderRadius: BorderRadius.circular(4),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
              Expanded(
                child: ValueListenableBuilder<int>(
                  valueListenable: chatRevision,
                  builder: (context, _, _) {
                    final waiting = isWaiting();
                    return ListView.builder(
                      controller: scrollController,
                      itemCount: messages.length + (waiting ? 1 : 0),
                      itemBuilder: (context, index) {
                        if (waiting && index == messages.length) {
                          return const _TypingBubble();
                        }
                        final message = messages[index];
                        return _MessageBubble(message: message);
                      },
                    );
                  },
                ),
              ),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: controller,
                      onTap: onInputTap,
                      onSubmitted: (_) {
                        if (!isWaiting()) onSend();
                      },
                      textInputAction: TextInputAction.send,
                      decoration: const InputDecoration(
                        hintText: '例如：從台北車站走到公館夜市',
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.all(Radius.circular(999)),
                        ),
                        isDense: true,
                        contentPadding: EdgeInsets.symmetric(
                          horizontal: 14,
                          vertical: 14,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  ValueListenableBuilder<int>(
                    valueListenable: chatRevision,
                    builder: (context, _, _) => IconButton.filled(
                      onPressed: isWaiting() ? null : onSend,
                      icon: const Icon(Icons.arrow_upward),
                    ),
                  ),
                ],
              ),
              const Padding(
                padding: EdgeInsets.only(top: 7),
                child: Text(
                  '夜間步行輔助建議，無法保證安全；緊急狀況請撥 110 或 119。',
                  textAlign: TextAlign.center,
                  style: TextStyle(fontSize: 10, color: Color(0xffb7ae96)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message});
  final ChatMessage message;
  @override
  Widget build(BuildContext context) => Align(
    alignment: message.isUser ? Alignment.centerRight : Alignment.centerLeft,
    child: Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.all(10),
      constraints: const BoxConstraints(maxWidth: 310),
      decoration: BoxDecoration(
        color: message.isUser
            ? const Color(0xff9f7935)
            : message.isError
            ? const Color(0xff6d3535)
            : const Color(0xff2a2920),
        borderRadius: BorderRadius.only(
          topLeft: Radius.circular(message.isUser ? 25 : 0),
          topRight: Radius.circular(message.isUser ? 0 : 25),
          bottomLeft: const Radius.circular(25),
          bottomRight: const Radius.circular(25),
        ),
      ),
      child: Text(message.text),
    ),
  );
}

class _TypingBubble extends StatelessWidget {
  const _TypingBubble();
  @override
  Widget build(BuildContext context) => const Align(
    alignment: Alignment.centerLeft,
    child: Padding(
      padding: EdgeInsets.only(bottom: 8),
      child: Text('Safeway 正在查詢資料與規劃路線…'),
    ),
  );
}

class _RouteSummary extends StatelessWidget {
  const _RouteSummary({
    required this.response,
    required this.selected,
    required this.onSelect,
    required this.onOpenGoogleMaps,
    required this.onContinueChat,
  });
  final RouteReadyResponse response;
  final SafeRoute selected;
  final ValueChanged<SafeRoute> onSelect;
  final VoidCallback onOpenGoogleMaps;
  final VoidCallback onContinueChat;

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    padding: EdgeInsets.fromLTRB(
      16,
      10,
      16,
      MediaQuery.paddingOf(context).bottom + 14,
    ),
    decoration: const BoxDecoration(
      color: Color(0xee242117),
      borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
    ),
    child: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 36,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            itemCount: response.routes.length,
            separatorBuilder: (_, index) => const SizedBox(width: 8),
            itemBuilder: (_, index) {
              final route = response.routes[index];
              return ChoiceChip(
                label: Text(route.label),
                selected: route == selected,
                onSelected: (_) => onSelect(route),
              );
            },
          ),
        ),
        const SizedBox(height: 6),
        Text(
          '${_distance(selected.metrics.distanceMeters)} · 約 ${selected.metrics.durationMinutes} 分鐘',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 6),
        Text(
          response.replyText,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontSize: 13, color: Color(0xffded7c5)),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 5,
          children: [
            if (selected.metrics.litCoverageRatio != null)
              _MetricChip(
                icon: Icons.lightbulb_outline,
                text:
                    '照明 ${(selected.metrics.litCoverageRatio! * 100).round()}%',
              ),
            _MetricChip(
              icon: Icons.storefront_outlined,
              text: '${selected.metrics.helpPoints} 個求助據點',
            ),
          ],
        ),
        for (final reason in selected.reasons.take(1))
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Row(
              children: [
                const Icon(
                  Icons.check_circle_outline,
                  color: Color(0xffe1bc6b),
                  size: 17,
                ),
                const SizedBox(width: 6),
                Expanded(child: Text(reason)),
              ],
            ),
          ),
        for (final warning in selected.warnings.take(1))
          Padding(
            padding: const EdgeInsets.only(top: 5),
            child: Row(
              children: [
                const Icon(Icons.info_outline, color: Colors.amber, size: 17),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    warning,
                    style: const TextStyle(color: Colors.amber),
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: 8),
        SizedBox(
          width: double.infinity,
          child: FilledButton.icon(
            onPressed: onOpenGoogleMaps,
            icon: const Icon(Icons.directions_outlined),
            label: const Text('在 Google Maps 開啟'),
          ),
        ),
        Align(
          alignment: Alignment.center,
          child: TextButton.icon(
            onPressed: onContinueChat,
            icon: const Icon(Icons.chat_bubble_outline, size: 18),
            label: const Text('繼續對話或調整偏好'),
          ),
        ),
      ],
    ),
  );
}

class _MetricChip extends StatelessWidget {
  const _MetricChip({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) =>
      Chip(avatar: Icon(icon, size: 16), label: Text(text));
}

class SafewayChatApi {
  bool get isConfigured => _apiBaseUrl.isNotEmpty;

  Future<Session> createSession(LatLng location) async {
    if (!isConfigured) return const Session('demo-session');
    final response = await http
        .post(
          _uri('/api/session'),
          headers: _headers,
          body: jsonEncode({'user_location': _locationJson(location)}),
        )
        .timeout(const Duration(seconds: 20));
    return _sessionFromResponse(response);
  }

  Future<ChatResponse> sendMessage({
    required String? sessionId,
    required String message,
    required LatLng location,
  }) async {
    if (!isConfigured) return RouteReadyResponse.demo(location, message);
    if (sessionId == null) throw const ChatApiException('無法建立對話，請稍後再試。');
    final response = await http
        .post(
          _uri('/api/chat'),
          headers: _headers,
          body: jsonEncode({
            'session_id': sessionId,
            'message': message,
            'user_location': _locationJson(location),
          }),
        )
        .timeout(const Duration(seconds: 60));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _errorFromResponse(response);
    }
    return ChatResponse.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }

  Uri _uri(String path) =>
      Uri.parse('${_apiBaseUrl.replaceFirst(RegExp(r'/$'), '')}$path');
  Map<String, String> get _headers => const {
    'Content-Type': 'application/json',
  };
  Map<String, double> _locationJson(LatLng location) => {
    'lat': location.latitude,
    'lng': location.longitude,
  };
  Session _sessionFromResponse(http.Response response) {
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _errorFromResponse(response);
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    final id = body['session_id'] as String?;
    if (id == null) throw const ChatApiException('伺服器沒有回傳對話識別碼。');
    return Session(id);
  }

  ChatApiException _errorFromResponse(http.Response response) {
    try {
      final body = jsonDecode(response.body);
      return ChatApiException(
        body is Map
            ? (body['message'] ?? body['detail'] ?? '服務暫時無法使用').toString()
            : '服務暫時無法使用',
      );
    } catch (_) {
      return ChatApiException('服務暫時無法使用（${response.statusCode}）');
    }
  }
}

class Session {
  const Session(this.id);
  final String id;
}

class ChatApiException implements Exception {
  const ChatApiException(this.message);
  final String message;
}

class ChatMessage {
  const ChatMessage(this.text, {required this.isUser, this.isError = false});
  factory ChatMessage.user(String text) => ChatMessage(text, isUser: true);
  factory ChatMessage.assistant(String text, {bool isError = false}) =>
      ChatMessage(text, isUser: false, isError: isError);
  final String text;
  final bool isUser, isError;
}

sealed class ChatResponse {
  const ChatResponse(this.replyText);
  final String replyText;
  factory ChatResponse.fromJson(Map<String, dynamic> json) {
    switch (json['status']) {
      case 'route_ready':
        return RouteReadyResponse.fromJson(json);
      case 'collecting_info':
        return CollectingInfoResponse(
          json['reply_text'] as String? ?? '請提供更多起點或終點資訊。',
        );
      case 'error':
        return ErrorChatResponse(json['reply_text'] as String? ?? '這次無法規劃路線。');
      default:
        throw const ChatApiException('伺服器回傳未知的對話狀態。');
    }
  }
}

class CollectingInfoResponse extends ChatResponse {
  const CollectingInfoResponse(super.replyText);
}

class ErrorChatResponse extends ChatResponse {
  const ErrorChatResponse(super.replyText);
}

class RouteReadyResponse extends ChatResponse {
  const RouteReadyResponse({
    required String replyText,
    required this.disclaimer,
    required this.routes,
    required this.selectedRouteId,
    required this.dynamicHazards,
    this.googleMapsUrl,
  }) : super(replyText);
  final String disclaimer, selectedRouteId;
  final List<SafeRoute> routes;
  final List<DynamicHazard> dynamicHazards;
  final String? googleMapsUrl;
  SafeRoute get selectedRoute => routes.firstWhere(
    (route) => route.id == selectedRouteId,
    orElse: () => routes.first,
  );
  factory RouteReadyResponse.fromJson(
    Map<String, dynamic> json,
  ) => RouteReadyResponse(
    replyText: json['reply_text'] as String? ?? '',
    disclaimer: json['disclaimer'] as String? ?? '此建議無法保證安全。',
    selectedRouteId: json['selected_route_id'] as String? ?? 'safest',
    routes: (json['routes'] as List<dynamic>? ?? [])
        .map((item) => SafeRoute.fromJson(item as Map<String, dynamic>))
        .toList(),
    dynamicHazards: (json['dynamic_hazards_considered'] as List<dynamic>? ?? [])
        .map((item) => DynamicHazard.fromJson(item as Map<String, dynamic>))
        .toList(),
    googleMapsUrl: json['google_maps_url'] as String?,
  );
  factory RouteReadyResponse.demo(LatLng location, String message) {
    final safest = SafeRoute.demo(location, safest: true);
    final fastest = SafeRoute.demo(location, safest: false);
    return RouteReadyResponse(
      replyText: '示範模式：我已依你的需求規劃兩條路線。較安全路線多走約 4 分鐘，但沿途可求助據點與照明覆蓋較多。',
      disclaimer: '此建議依公開資料與即時資訊產生，無法保證安全；緊急狀況請立即撥打 110 或 119。',
      routes: [safest, fastest],
      selectedRouteId: safest.id,
      dynamicHazards: const [],
      googleMapsUrl: null,
    );
  }
}

class SafeRoute {
  const SafeRoute({
    required this.id,
    required this.label,
    required this.path,
    required this.alpha,
    required this.confidence,
    required this.metrics,
    required this.reasons,
    required this.warnings,
  });
  final String id, label, confidence;
  final List<LatLng> path;
  final double alpha;
  final RouteMetrics metrics;
  final List<String> reasons, warnings;
  factory SafeRoute.fromJson(Map<String, dynamic> json) => SafeRoute(
    id: json['id'] as String,
    label: json['label'] as String? ?? '路線',
    path: _coordinates(json['path_coordinates']),
    alpha: (json['alpha_used'] as num? ?? .6).toDouble(),
    confidence: json['confidence'] as String? ?? 'unknown',
    metrics: RouteMetrics.fromJson(
      json['metrics'] as Map<String, dynamic>? ?? {},
    ),
    reasons: List<String>.from(json['reasons'] as List<dynamic>? ?? []),
    warnings: List<String>.from(json['warnings'] as List<dynamic>? ?? []),
  );
  factory SafeRoute.demo(LatLng start, {required bool safest}) => SafeRoute(
    id: safest ? 'safest' : 'fastest',
    label: safest ? '推薦的較安全路線' : '最快路線',
    path: safest
        ? [
            start,
            LatLng(start.latitude + .002, start.longitude + .002),
            LatLng(start.latitude + .006, start.longitude + .005),
          ]
        : [
            start,
            LatLng(start.latitude + .003, start.longitude + .004),
            LatLng(start.latitude + .006, start.longitude + .005),
          ],
    alpha: safest ? .6 : 0,
    confidence: 'medium',
    metrics: RouteMetrics(
      distanceMeters: safest ? 1420 : 1180,
      durationMinutes: safest ? 18 : 14,
      avgSafetyScore: safest ? .78 : .52,
      litCoverageRatio: safest ? .71 : .45,
      helpPoints: safest ? 5 : 2,
      policeStations: safest ? 1 : 0,
    ),
    reasons: safest
        ? const ['沿途 5 個營業中可求助據點', '比最快路線多走約 4 分鐘，但避開照明不足路段']
        : const [],
    warnings: safest ? const [] : const ['部分路段缺乏路燈資料，照明未納入評分'],
  );
}

class RouteMetrics {
  const RouteMetrics({
    required this.distanceMeters,
    required this.durationMinutes,
    required this.avgSafetyScore,
    required this.litCoverageRatio,
    required this.helpPoints,
    required this.policeStations,
  });
  final int distanceMeters, durationMinutes, helpPoints, policeStations;
  final double avgSafetyScore;
  final double? litCoverageRatio;
  factory RouteMetrics.fromJson(Map<String, dynamic> json) => RouteMetrics(
    distanceMeters: json['distance_m'] as int? ?? 0,
    durationMinutes: json['duration_min_est'] as int? ?? 0,
    avgSafetyScore: (json['avg_safety_score'] as num? ?? 0).toDouble(),
    litCoverageRatio: (json['lit_coverage_ratio'] as num?)?.toDouble(),
    helpPoints: json['help_points_within_50m'] as int? ?? 0,
    policeStations: json['police_within_150m'] as int? ?? 0,
  );
}

class DynamicHazard {
  const DynamicHazard(this.summary);
  final String summary;
  factory DynamicHazard.fromJson(Map<String, dynamic> json) =>
      DynamicHazard(json['summary'] as String? ?? '近期事件');
}

List<LatLng> _coordinates(dynamic value) => (value as List<dynamic>? ?? [])
    .whereType<List>()
    .where((point) => point.length >= 2 && point[0] is num && point[1] is num)
    .map(
      (point) =>
          LatLng((point[0] as num).toDouble(), (point[1] as num).toDouble()),
    )
    .toList();
LatLngBounds _bounds(List<LatLng> points) {
  var minLat = points.first.latitude,
      maxLat = minLat,
      minLng = points.first.longitude,
      maxLng = minLng;
  for (final point in points.skip(1)) {
    minLat = math.min(minLat, point.latitude);
    maxLat = math.max(maxLat, point.latitude);
    minLng = math.min(minLng, point.longitude);
    maxLng = math.max(maxLng, point.longitude);
  }
  return LatLngBounds(
    southwest: LatLng(minLat, minLng),
    northeast: LatLng(maxLat, maxLng),
  );
}

String _distance(int meters) =>
    meters >= 1000 ? '${(meters / 1000).toStringAsFixed(1)} km' : '$meters m';
