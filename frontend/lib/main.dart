import 'dart:convert';
import 'dart:math' as math;
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';

const _fallbackLocation = LatLng(25.0330, 121.5654);
const _routeColor = Color(0xffc39a4b);
// google_maps_flutter 的預設 pin 以 HSV hue 調色；40° 對應
// _routeColor 的金棕色，讓目的地 marker 與路徑保持同一色系。
const _routeMarkerHue = 40.0;
const _warningOrange = Color(0xffff7800);
const _warningMarkerHue = 24.0;
const _compileTimeApiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: '',
);
late final String _apiBaseUrl;
const _demoGoogleMapsUrl =
    'https://www.google.com/maps/dir/%E5%8F%B0%E5%8C%97101%E8%B3%BC%E7%89%A9%E4%B8%AD%E5%BF%83+110%E8%87%BA%E5%8C%97%E5%B8%82%E4%BF%A1%E7%BE%A9%E5%8D%80%E8%A5%BF%E6%9D%91%E9%87%8C%E5%B8%82%E5%BA%9C%E8%B7%AF45+%E8%99%9F/%E5%8F%B0%E4%B8%AD+414%E8%87%BA%E4%B8%AD%E5%B8%82%E7%83%8F%E6%97%A5%E5%8D%80/%E6%97%A5%E6%9C%88%E6%BD%AD+555%E5%8D%97%E6%8A%95%E7%B8%A3%E9%AD%9A%E6%B1%A0%E9%84%89/%E9%AB%98%E9%9B%84+%E9%AB%98%E9%9B%84%E5%B8%82%E9%BC%93%E5%B1%B1%E5%8D%80%E9%BE%8D%E5%AD%90%E9%87%8C/@23.8275968,119.5984759,8z/data=!4m26!4m25!1m5!1m1!1s0x3442abb6da80a7ad:0xacc4d11dc963103c!2m2!1d121.5640212!2d25.0341222!1m5!1m1!1s0x34693ea3df35917f:0xc0c95f36683eb0ad!2m2!1d120.61419!2d24.11006!1m5!1m1!1s0x3468d5e076ee0005:0xec17a6fd5312a528!2m2!1d120.9159131!2d23.8573342!1m5!1m1!1s0x346e04fd209f4835:0x54511e7d86c87c09!2m2!1d120.3014375!2d22.6272772!3e2?entry=ttu&g_ep=EgoyMDI2MDgxMi4wIKXMDSoASAFQAw%3D%3D';

int _indexForAlpha(double alpha) {
  const values = [0.0, 0.3, 0.6, 0.8, 1.0];
  var closestIndex = 0;
  for (var index = 1; index < values.length; index++) {
    if ((values[index] - alpha).abs() < (values[closestIndex] - alpha).abs()) {
      closestIndex = index;
    }
  }
  return closestIndex;
}

String _preferenceLabelForAlpha(double alpha) =>
    const ['快速', '偏快', '平衡', '偏安全', '安全'][_indexForAlpha(alpha)];

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  _apiBaseUrl = await _loadApiBaseUrl();
  runApp(const NavGuardApp());
}

/// `API_BASE_URL` 可供 CI 或臨時測試覆寫；一般開發讀取 assets/.env。
/// 這份前端設定只應含公開的服務網址，不能放 server-side API key。
Future<String> _loadApiBaseUrl() async {
  if (_compileTimeApiBaseUrl.isNotEmpty) return _compileTimeApiBaseUrl;

  try {
    final contents = await rootBundle.loadString('assets/.env');
    for (final rawLine in contents.split('\n')) {
      final line = rawLine.trim();
      if (line.isEmpty || line.startsWith('#')) continue;
      final separator = line.indexOf('=');
      if (separator == -1) continue;
      final key = line.substring(0, separator).trim();
      if (key == 'API_BASE_URL') {
        return line
            .substring(separator + 1)
            .trim()
            .replaceAll(RegExp(r'^"|"$'), '');
      }
    }
  } catch (_) {
    // `.env` 可省略；此時會使用本機示範 response，方便純 UI 開發。
  }
  return '';
}

class NavGuardApp extends StatelessWidget {
  const NavGuardApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'NavGuard',
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
  final _api = NavGuardChatApi();
  final _input = TextEditingController();
  final _chatSheetExtent = ValueNotifier<double>(.4);
  final _chatRevision = ValueNotifier<int>(0);
  final _messages = <ChatMessage>[
    ChatMessage.assistant('你好，我是 NavGuard。告訴我你想從哪裡走到哪裡；我會依公開資料提供較安全的步行建議。'),
  ];
  GoogleMapController? _map;
  BitmapDescriptor? _routeOriginIcon;
  LatLng _location = _fallbackLocation;
  RouteReadyResponse? _routeResponse;
  SafeRoute? _selectedRoute;
  bool _waiting = false;
  bool _isChatSheetOpen = false;
  double _priorityAlpha = .6;

  @override
  void initState() {
    super.initState();
    _loadRouteOriginIcon();
    _getLocation();
    WidgetsBinding.instance.addPostFrameCallback((_) => _showChatSheet());
  }

  Future<void> _loadRouteOriginIcon() async {
    const logicalSize = 30.0;
    const pixelRatio = 3.0;
    final recorder = ui.PictureRecorder();
    final canvas = Canvas(recorder)..scale(pixelRatio);
    final center = const Offset(logicalSize / 2, logicalSize / 2);

    canvas.drawCircle(
      center,
      9.5,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.fill
        ..isAntiAlias = true,
    );
    canvas.drawCircle(
      center,
      7.5,
      Paint()
        ..color = _routeColor
        ..style = PaintingStyle.fill
        ..isAntiAlias = true,
    );

    final picture = recorder.endRecording();
    final image = await picture.toImage(
      (logicalSize * pixelRatio).round(),
      (logicalSize * pixelRatio).round(),
    );
    final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
    image.dispose();
    picture.dispose();
    if (byteData == null || !mounted) return;

    setState(() {
      _routeOriginIcon = BitmapDescriptor.bytes(
        byteData.buffer.asUint8List(),
        imagePixelRatio: pixelRatio,
      );
    });
  }

  @override
  void dispose() {
    _input.dispose();
    _chatSheetExtent.dispose();
    _chatRevision.dispose();
    _map?.dispose();
    super.dispose();
  }

  Future<void> _getLocation() async {
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
      final response = await _api.sendMessage(
        message: text,
        location: _location,
        priorityAlpha: _priorityAlpha,
      );
      if (!mounted) return;
      setState(() {
        _messages.add(
          ChatMessage.assistant(
            response.replyText,
            routeResponse: response is RouteReadyResponse ? response : null,
          ),
        );
        if (response is RouteReadyResponse) {
          _routeResponse = response;
          _selectedRoute = response.route;
        }
      });
      _chatRevision.value++;
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

  Future<void> _showRecommendedRoute(RouteReadyResponse response) async {
    setState(() {
      _routeResponse = response;
      _selectedRoute = response.route;
    });
    await _focusRoutes([response.route]);
    if (_isChatSheetOpen && mounted) Navigator.of(context).pop();
  }

  Future<void> _openGoogleMaps() async {
    final url = _routeResponse?.googleMapsUrl ?? _demoGoogleMapsUrl;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('在 Google Maps 開啟'),
        content: const Text(
          'Google Maps 會依自己的演算法重新規劃，實際路線可能與 NavGuard 的推薦路徑略有不同。',
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

  Future<void> _showPointDetails(NearbyPoint point, MapPointKind kind) async {
    await _map?.animateCamera(CameraUpdate.newLatLng(point.position));
    if (!mounted) return;
    await showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      backgroundColor: const Color(0xff242117),
      builder: (context) => _MapPointDetailsSheet(
        point: point,
        kind: kind,
        onOpenGoogleMaps: () => _openPointInGoogleMaps(point),
      ),
    );
  }

  Future<void> _openPointInGoogleMaps(NearbyPoint point) async {
    final parameters = <String, String>{
      'api': '1',
      'query': '${point.lat},${point.lng}',
      if (point.placeId != null) 'query_place_id': point.placeId!,
    };
    final url = Uri.https('www.google.com', '/maps/search/', parameters);
    if (!await launchUrl(url, mode: LaunchMode.externalApplication) &&
        mounted) {
      _showSnackbar('無法在 Google Maps 開啟這個地點。');
    }
  }

  /// 透過系統撥號介面處理緊急電話；不要求 CALL_PHONE 權限，避免 App
  /// 在使用者不知情的情況下直接發話。
  Future<void> _callEmergency() async {
    final opened = await launchUrl(
      Uri(scheme: 'tel', path: '0986620369'),
      mode: LaunchMode.externalApplication,
    );
    if (!opened && mounted) _showSnackbar('無法開啟撥號功能，請直接撥打 110 或 119。');
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
              if (_selectedRoute != null)
                Polyline(
                  polylineId: const PolylineId('recommended-route'),
                  points: _selectedRoute!.path,
                  width: 7,
                  color: _routeColor,
                ),
            },
            markers: {
              if (response != null && _routeOriginIcon != null)
                Marker(
                  markerId: const MarkerId('route-origin'),
                  position:
                      response.origin?.position ??
                      (response.route.path.isNotEmpty
                          ? response.route.path.first
                          : _location),
                  icon: _routeOriginIcon!,
                  anchor: const Offset(.5, .5),
                  infoWindow: InfoWindow(
                    title: response.origin?.displayName('目前位置') ?? '路線起點',
                    snippet: '路線起點',
                  ),
                )
              else if (response == null)
                Marker(
                  markerId: const MarkerId('current-location'),
                  position: _location,
                  infoWindow: const InfoWindow(title: '目前位置'),
                ),
              if (response?.destination != null)
                Marker(
                  markerId: const MarkerId('route-destination'),
                  position: response!.destination!.position,
                  icon: BitmapDescriptor.defaultMarkerWithHue(_routeMarkerHue),
                  infoWindow: InfoWindow(
                    title: response.destination!.displayName('目的地'),
                    snippet: '路線終點',
                  ),
                )
              else if (_selectedRoute?.path.isNotEmpty == true)
                Marker(
                  markerId: const MarkerId('route-destination'),
                  position: _selectedRoute!.path.last,
                  infoWindow: const InfoWindow(title: '目的地'),
                ),
              for (final point
                  in _selectedRoute?.metrics.policeStations ??
                      const <NearbyPoint>[])
                Marker(
                  markerId: MarkerId('police-${point.id}'),
                  position: point.position,
                  icon: BitmapDescriptor.defaultMarkerWithHue(
                    BitmapDescriptor.hueAzure,
                  ),
                  infoWindow: InfoWindow(
                    title: point.displayName('警察局'),
                    snippet: '警察局／可求助地點',
                  ),
                  onTap: () =>
                      _showPointDetails(point, MapPointKind.policeStation),
                ),
              for (final point
                  in _selectedRoute?.metrics.convenienceStores ??
                      const <NearbyPoint>[])
                Marker(
                  markerId: MarkerId('store-${point.id}'),
                  position: point.position,
                  icon: BitmapDescriptor.defaultMarkerWithHue(
                    BitmapDescriptor.hueGreen,
                  ),
                  infoWindow: InfoWindow(
                    title: point.displayName('24 小時便利商店'),
                    snippet: '24 小時便利商店／可求助據點',
                  ),
                  onTap: () =>
                      _showPointDetails(point, MapPointKind.convenienceStore),
                ),
              for (final point
                  in _selectedRoute?.metrics.dangerZones ??
                      const <NearbyPoint>[])
                Marker(
                  markerId: MarkerId('danger-${point.id}'),
                  position: point.position,
                  icon: BitmapDescriptor.defaultMarkerWithHue(
                    _warningMarkerHue,
                  ),
                  infoWindow: InfoWindow(
                    title: point.displayName('需留意地點'),
                    snippet: point.summary ?? '路線沿途需留意的地點',
                  ),
                  onTap: () =>
                      _showPointDetails(point, MapPointKind.dangerZone),
                ),
              for (final point
                  in _selectedRoute?.metrics.avoidedDangerZones ??
                      const <NearbyPoint>[])
                Marker(
                  markerId: MarkerId('avoided-danger-${point.id}'),
                  position: point.position,
                  icon: BitmapDescriptor.defaultMarkerWithHue(
                    _warningMarkerHue,
                  ),
                  infoWindow: InfoWindow(
                    title: '已繞開：${point.displayName('危險區域')}',
                    snippet: point.summary ?? '推薦路線已繞開此需留意地點',
                  ),
                  onTap: () =>
                      _showPointDetails(point, MapPointKind.dangerZone),
                ),
            },
          ),
          Positioned(
            right: 16,
            top: 56,
            child: Semantics(
              button: true,
              label: '緊急撥號',
              child: FloatingActionButton.small(
                heroTag: 'emergency-call',
                tooltip: '緊急撥號',
                backgroundColor: const Color(0xffbd3030),
                foregroundColor: Colors.white,
                onPressed: _callEmergency,
                child: const Icon(Icons.phone_in_talk),
              ),
            ),
          ),
          Positioned(
            right: 16,
            top: 112,
            child: FloatingActionButton.small(
              onPressed: _getLocation,
              child: const Icon(Icons.my_location),
            ),
          ),
          if (response != null)
            Align(
              alignment: Alignment.bottomCenter,
              child: _RouteSummary(
                response: response,
                onOpenGoogleMaps: _openGoogleMaps,
                onContinueChat: () => _showChatSheet(initialExtent: .9),
              ),
            ),
          Positioned(
            right: 16,
            top: 168,
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

  Future<void> _showChatSheet({double initialExtent = .4}) async {
    if (_isChatSheetOpen || !mounted) return;
    _chatSheetExtent.value = initialExtent.clamp(.4, .9).toDouble();
    setState(() => _isChatSheetOpen = true);
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      isDismissible: true,
      // 拖曳只由可見的 sheet 把手處理，避免透明上方區域也能拖動 modal。
      enableDrag: false,
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
                sheetExtent: _chatSheetExtent,
                onInputTap: _expandChatSheet,
                onSend: _sendMessage,
                onShowRecommendedRoute: _showRecommendedRoute,
                priorityAlpha: _priorityAlpha,
                onPriorityChanged: (value) => _priorityAlpha = value,
                onClose: () {
                  FocusScope.of(sheetContext).unfocus();
                  Navigator.of(sheetContext).pop();
                },
              ),
            ),
          ),
        ),
      ),
    );
    if (mounted) setState(() => _isChatSheetOpen = false);
  }

  void _expandChatSheet() {
    _chatSheetExtent.value = .9;
  }
}

class _ChatPanel extends StatefulWidget {
  const _ChatPanel({
    required this.messages,
    required this.controller,
    required this.chatRevision,
    required this.isWaiting,
    required this.sheetExtent,
    required this.onInputTap,
    required this.onSend,
    required this.onShowRecommendedRoute,
    required this.priorityAlpha,
    required this.onPriorityChanged,
    required this.onClose,
  });
  final List<ChatMessage> messages;
  final TextEditingController controller;
  final ValueListenable<int> chatRevision;
  final bool Function() isWaiting;
  final ValueNotifier<double> sheetExtent;
  final VoidCallback onInputTap;
  final VoidCallback onSend;
  final ValueChanged<RouteReadyResponse> onShowRecommendedRoute;
  final double priorityAlpha;
  final ValueChanged<double> onPriorityChanged;
  final VoidCallback onClose;

  @override
  State<_ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends State<_ChatPanel> {
  int? _lastScrolledRevision;
  final _messageScrollController = ScrollController();
  double _downwardDragAtMin = 0;
  double _dismissDragOffset = 0;
  late double _extent;
  bool _isDraggingSheet = false;
  late int _preferenceIndex;

  @override
  void initState() {
    super.initState();
    _extent = widget.sheetExtent.value;
    _preferenceIndex = _indexForAlpha(widget.priorityAlpha);
    widget.sheetExtent.addListener(_handleRequestedExtent);
  }

  @override
  void didUpdateWidget(covariant _ChatPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.sheetExtent != widget.sheetExtent) {
      oldWidget.sheetExtent.removeListener(_handleRequestedExtent);
      _extent = widget.sheetExtent.value;
      widget.sheetExtent.addListener(_handleRequestedExtent);
    }
    if (oldWidget.priorityAlpha != widget.priorityAlpha) {
      _preferenceIndex = _indexForAlpha(widget.priorityAlpha);
    }
  }

  @override
  void dispose() {
    widget.sheetExtent.removeListener(_handleRequestedExtent);
    _messageScrollController.dispose();
    super.dispose();
  }

  void _handleRequestedExtent() {
    final requested = widget.sheetExtent.value.clamp(.4, .9).toDouble();
    if (!mounted || requested == _extent) return;
    setState(() {
      _isDraggingSheet = false;
      _extent = requested;
      _dismissDragOffset = 0;
    });
  }

  void _setExtent(double size) {
    final next = size.clamp(.4, .9).toDouble();
    if (size == .4) FocusScope.of(context).unfocus();
    setState(() {
      _isDraggingSheet = false;
      _extent = next;
      _dismissDragOffset = 0;
    });
    if (widget.sheetExtent.value != next) widget.sheetExtent.value = next;
  }

  void _setPreference(int value) {
    const alphaByIndex = [0.0, 0.3, 0.6, 0.8, 1.0];
    setState(() => _preferenceIndex = value);
    widget.onPriorityChanged(alphaByIndex[value]);
  }

  Future<void> _showPreferencePicker() async {
    var selected = _preferenceIndex;
    await showDialog<void>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('設定路線偏好'),
          content: SizedBox(
            width: 280,
            child: _RoutePreferenceBar(
              value: selected,
              onChanged: (value) => setDialogState(() => selected = value),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () {
                _setPreference(selected);
                Navigator.pop(context);
              },
              child: const Text('套用'),
            ),
          ],
        ),
      ),
    );
  }

  void _startSheetDrag(DragStartDetails details) {
    _downwardDragAtMin = 0;
    _dismissDragOffset = 0;
    FocusScope.of(context).unfocus();
    setState(() => _isDraggingSheet = true);
  }

  void _resizeSheet(DragUpdateDetails details) {
    if (details.primaryDelta == null) return;
    final delta = details.primaryDelta!;
    if (_dismissDragOffset > 0) {
      setState(() {
        _dismissDragOffset = (_dismissDragOffset + delta)
            .clamp(0, 240)
            .toDouble();
        _downwardDragAtMin = _dismissDragOffset;
      });
      return;
    }
    if (_extent <= .401 && delta > 0) {
      setState(() {
        _dismissDragOffset = (_dismissDragOffset + delta)
            .clamp(0, 240)
            .toDouble();
        _downwardDragAtMin = _dismissDragOffset;
      });
      return;
    }
    if (delta < 0) _downwardDragAtMin = 0;
    final height = MediaQuery.sizeOf(context).height;
    setState(() {
      _extent = (_extent - delta / height).clamp(.4, .9).toDouble();
    });
  }

  void _settleSheet(DragEndDetails details) {
    final velocity = details.primaryVelocity ?? 0;
    if (_extent <= .401 && (_downwardDragAtMin >= 48 || velocity > 350)) {
      widget.onClose();
      return;
    }
    final target = velocity < -350
        ? .9
        : velocity > 350
        ? .4
        : _extent >= .65
        ? .9
        : .4;
    _setExtent(target);
  }

  void _scrollToLatest(ScrollController scrollController, int revision) {
    if (_lastScrolledRevision == revision) return;
    _lastScrolledRevision = revision;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !scrollController.hasClients) return;
      scrollController.animateTo(
        scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 180),
        curve: Curves.easeOut,
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) => TweenAnimationBuilder<double>(
        tween: Tween<double>(end: _dismissDragOffset),
        duration: _isDraggingSheet
            ? Duration.zero
            : const Duration(milliseconds: 180),
        curve: Curves.easeOut,
        builder: (context, animatedOffset, child) => Transform.translate(
          offset: Offset(0, animatedOffset),
          child: child,
        ),
        child: Align(
          alignment: Alignment.bottomCenter,
          child: TweenAnimationBuilder<double>(
            tween: Tween<double>(end: _extent),
            duration: _isDraggingSheet
                ? Duration.zero
                : const Duration(milliseconds: 220),
            curve: Curves.easeOut,
            builder: (context, animatedExtent, child) => SizedBox(
              width: double.infinity,
              height: constraints.maxHeight * animatedExtent,
              child: child,
            ),
            child: Container(
              padding: EdgeInsets.fromLTRB(
                16,
                0,
                16,
                MediaQuery.paddingOf(context).bottom + 10,
              ),
              decoration: const BoxDecoration(
                color: Color(0xff17150f),
                borderRadius: BorderRadius.vertical(top: Radius.circular(28)),
              ),
              child: Column(
                children: [
                  SizedBox(
                    height: 42,
                    width: double.infinity,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        GestureDetector(
                          behavior: HitTestBehavior.opaque,
                          onVerticalDragStart: _startSheetDrag,
                          onVerticalDragUpdate: _resizeSheet,
                          onVerticalDragEnd: _settleSheet,
                          child: SizedBox(
                            width: double.infinity,
                            height: 42,
                            child: Padding(
                              padding: const EdgeInsets.only(top: 8),
                              child: Center(
                                child: Container(
                                  width: 42,
                                  height: 4,
                                  decoration: BoxDecoration(
                                    color: Colors.white38,
                                    borderRadius: BorderRadius.circular(4),
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  Expanded(
                    child: ValueListenableBuilder<int>(
                      valueListenable: widget.chatRevision,
                      builder: (context, revision, _) {
                        _scrollToLatest(_messageScrollController, revision);
                        final waiting = widget.isWaiting();
                        return ListView.builder(
                          controller: _messageScrollController,
                          itemCount: widget.messages.length + (waiting ? 1 : 0),
                          itemBuilder: (context, index) {
                            if (waiting && index == widget.messages.length) {
                              return const _TypingBubble();
                            }
                            final message = widget.messages[index];
                            return _MessageBubble(
                              message: message,
                              onShowRecommendedRoute:
                                  widget.onShowRecommendedRoute,
                            );
                          },
                        );
                      },
                    ),
                  ),
                  Row(
                    children: [
                      IconButton.outlined(
                        tooltip: '設定路線偏好',
                        onPressed: _showPreferencePicker,
                        icon: const Icon(Icons.tune),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: TextField(
                          controller: widget.controller,
                          onTap: widget.onInputTap,
                          onSubmitted: (_) {
                            if (!widget.isWaiting()) widget.onSend();
                          },
                          textInputAction: TextInputAction.send,
                          decoration: const InputDecoration(
                            hintText: '例如：前往台北市政府',
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.all(
                                Radius.circular(999),
                              ),
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
                        valueListenable: widget.chatRevision,
                        builder: (context, _, _) => IconButton.filled(
                          onPressed: widget.isWaiting() ? null : widget.onSend,
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
        ),
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({
    required this.message,
    required this.onShowRecommendedRoute,
  });
  final ChatMessage message;
  final ValueChanged<RouteReadyResponse> onShowRecommendedRoute;

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
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (message.isUser)
            Text(message.text)
          else
            MarkdownBody(
              data: message.text,
              selectable: true,
              styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context))
                  .copyWith(
                    p: const TextStyle(color: Color(0xffeee8db)),
                    strong: const TextStyle(
                      color: Color(0xffeee8db),
                      fontWeight: FontWeight.w700,
                    ),
                    a: const TextStyle(
                      color: Color(0xfff3c765),
                      decoration: TextDecoration.underline,
                    ),
                  ),
              onTapLink: (_, href, _) {
                if (href != null) {
                  launchUrl(
                    Uri.parse(href),
                    mode: LaunchMode.externalApplication,
                  );
                }
              },
            ),
          if (message.routeResponse?.googleMapsUrl != null) ...[
            const SizedBox(height: 8),
            Chip(
              avatar: const Icon(Icons.shield_outlined, size: 17),
              label: Text(
                '路線偏好：${_preferenceLabelForAlpha(message.routeResponse!.route.alpha)}',
              ),
              visualDensity: VisualDensity.compact,
            ),
            const SizedBox(height: 4),
            FilledButton.icon(
              onPressed: () => onShowRecommendedRoute(message.routeResponse!),
              icon: const Icon(Icons.route_outlined, size: 18),
              label: const Text('查看推薦路線'),
            ),
          ],
        ],
      ),
    ),
  );
}

class _TypingBubble extends StatefulWidget {
  const _TypingBubble();

  @override
  State<_TypingBubble> createState() => _TypingBubbleState();
}

class _TypingBubbleState extends State<_TypingBubble>
    with SingleTickerProviderStateMixin {
  late final AnimationController _gradientController = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  )..repeat();

  @override
  void dispose() {
    _gradientController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Align(
    alignment: Alignment.centerLeft,
    child: Padding(
      padding: const EdgeInsets.only(bottom: 8),
      // 此時尚未收到後端的 status，不能假設一定會進入 route_ready。
      // collecting_info 只是在補齊起訖點，不會規劃路線。
      child: AnimatedBuilder(
        animation: _gradientController,
        builder: (context, child) {
          final shift = _gradientController.value * 3 - 1.5;
          return ShaderMask(
            blendMode: BlendMode.srcIn,
            shaderCallback: (bounds) => LinearGradient(
              begin: Alignment(shift - 1, 0),
              end: Alignment(shift + 1, 0),
              colors: const [
                Color(0xff8e6a2e),
                Color(0xffffe09a),
                Color(0xff8e6a2e),
              ],
            ).createShader(bounds),
            child: child,
          );
        },
        child: const Text('NavGuard 正在理解你的需求…'),
      ),
    ),
  );
}

class _RoutePreferenceBar extends StatelessWidget {
  const _RoutePreferenceBar({required this.value, required this.onChanged});

  final int value;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) => Semantics(
    slider: true,
    label: '路線偏好：${['快速', '偏快', '平衡', '偏安全', '安全'][value]}',
    child: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('路線偏好', style: TextStyle(fontSize: 12)),
        SliderTheme(
          data: SliderTheme.of(context).copyWith(
            activeTrackColor: const Color(0xffc39a4b),
            inactiveTrackColor: const Color(0xff4c4738),
            thumbColor: const Color(0xffe3bd70),
            overlayColor: const Color(0x33e3bd70),
            trackHeight: 5,
          ),
          child: Slider(
            value: value.toDouble(),
            min: 0,
            max: 4,
            divisions: 4,
            onChanged: (next) => onChanged(next.round()),
          ),
        ),
        const Padding(
          padding: EdgeInsets.symmetric(horizontal: 10),
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Text('快速', style: TextStyle(fontSize: 11, color: Colors.white70)),
              Text('偏快', style: TextStyle(fontSize: 11, color: Colors.white70)),
              Text('平衡', style: TextStyle(fontSize: 11, color: Colors.white70)),
              Text(
                '偏安全',
                style: TextStyle(fontSize: 11, color: Colors.white70),
              ),
              Text('安全', style: TextStyle(fontSize: 11, color: Colors.white70)),
            ],
          ),
        ),
      ],
    ),
  );
}

class _RouteSummary extends StatelessWidget {
  const _RouteSummary({
    required this.response,
    required this.onOpenGoogleMaps,
    required this.onContinueChat,
  });
  final RouteReadyResponse response;
  final VoidCallback onOpenGoogleMaps;
  final VoidCallback onContinueChat;

  @override
  Widget build(BuildContext context) {
    final selected = response.route;
    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(
        16,
        18,
        16,
        MediaQuery.paddingOf(context).bottom + 8,
      ),
      decoration: const BoxDecoration(
        color: Color(0xee242117),
        borderRadius: BorderRadius.vertical(top: Radius.circular(26)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(Icons.trip_origin, size: 18, color: Color(0xff71b97a)),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  response.origin?.displayName('目前位置') ?? '起點',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          Padding(
            padding: const EdgeInsets.only(left: 8),
            child: Container(width: 2, height: 12, color: Colors.white24),
          ),
          Row(
            children: [
              const Icon(Icons.location_on, size: 19, color: Color(0xffd99b65)),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  response.destination?.displayName('目的地') ?? '終點',
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${_distance(selected.metrics.distanceMeters)} · 約 ${selected.metrics.durationMinutes} 分鐘',
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
                text: '${selected.metrics.convenienceStores.length} 間便利商店',
              ),
              _MetricChip(
                icon: Icons.local_police_outlined,
                text: '${selected.metrics.policeStations.length} 間警察局',
              ),
              if (selected.metrics.dangerZones.isNotEmpty)
                _MetricChip(
                  icon: Icons.warning_amber_rounded,
                  text: '${selected.metrics.dangerZones.length} 個需留意地點',
                ),
              if (selected.metrics.avoidedDangerZones.isNotEmpty)
                _MetricChip(
                  icon: Icons.alt_route_rounded,
                  text:
                      '已繞開 ${selected.metrics.avoidedDangerZones.length} 個危險區域',
                ),
            ],
          ),
          for (final warning in response.warnings.take(1))
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
}

class _MetricChip extends StatelessWidget {
  const _MetricChip({required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) =>
      Chip(avatar: Icon(icon, size: 16), label: Text(text));
}

enum MapPointKind { convenienceStore, policeStation, dangerZone }

class _MapPointDetailsSheet extends StatelessWidget {
  const _MapPointDetailsSheet({
    required this.point,
    required this.kind,
    required this.onOpenGoogleMaps,
  });

  final NearbyPoint point;
  final MapPointKind kind;
  final VoidCallback onOpenGoogleMaps;

  IconData get _icon => switch (kind) {
    MapPointKind.convenienceStore => Icons.storefront_outlined,
    MapPointKind.policeStation => Icons.local_police_outlined,
    MapPointKind.dangerZone => Icons.warning_amber_rounded,
  };

  Color get _color => switch (kind) {
    MapPointKind.convenienceStore => const Color(0xff66bb6a),
    MapPointKind.policeStation => const Color(0xff42a5f5),
    MapPointKind.dangerZone => _warningOrange,
  };

  String get _category => switch (kind) {
    MapPointKind.convenienceStore => '24 小時便利商店／可求助據點',
    MapPointKind.policeStation => '警察局／可求助地點',
    MapPointKind.dangerZone => '需留意地點',
  };

  String get _fallbackName => switch (kind) {
    MapPointKind.convenienceStore => '24 小時便利商店',
    MapPointKind.policeStation => '警察局',
    MapPointKind.dangerZone => '需留意地點',
  };

  @override
  Widget build(BuildContext context) => SafeArea(
    top: false,
    child: Padding(
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 18),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              CircleAvatar(
                backgroundColor: _color.withValues(alpha: .18),
                foregroundColor: _color,
                child: Icon(_icon),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      point.displayName(_fallbackName),
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 3),
                    Text(
                      _category,
                      style: TextStyle(color: _color, fontSize: 13),
                    ),
                  ],
                ),
              ),
            ],
          ),
          if (point.summary?.trim().isNotEmpty == true) ...[
            const SizedBox(height: 14),
            Text(point.summary!, style: const TextStyle(fontSize: 15)),
          ],
          const SizedBox(height: 12),
          Row(
            children: [
              const Icon(Icons.location_on_outlined, size: 18),
              const SizedBox(width: 7),
              Text(
                '${point.lat.toStringAsFixed(6)}, ${point.lng.toStringAsFixed(6)}',
                style: const TextStyle(color: Colors.white70),
              ),
            ],
          ),
          if (kind == MapPointKind.dangerZone) ...[
            const SizedBox(height: 12),
            const Text(
              '此標示僅供路線判斷參考，不代表目前一定有危險。',
              style: TextStyle(color: Color(0xffc9c0aa), fontSize: 12),
            ),
          ],
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: onOpenGoogleMaps,
              icon: const Icon(Icons.map_outlined),
              label: const Text('在 Google Maps 查看'),
            ),
          ),
        ],
      ),
    ),
  );
}

class NavGuardChatApi {
  String? _sessionId;

  bool get isConfigured => _apiBaseUrl.isNotEmpty;

  Future<ChatResponse> sendMessage({
    required String message,
    required LatLng location,
    required double priorityAlpha,
  }) async {
    if (!isConfigured) return RouteReadyResponse.demo(location, message);
    _sessionId ??= await _createSession(location);
    var response = await _postMessage(
      sessionId: _sessionId!,
      message: message,
      location: location,
      priorityAlpha: priorityAlpha,
    );
    // Session 過期時依新合約重新交握一次，再重送原訊息。
    if (response.statusCode == 404) {
      _sessionId = await _createSession(location);
      response = await _postMessage(
        sessionId: _sessionId!,
        message: message,
        location: location,
        priorityAlpha: priorityAlpha,
      );
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _errorFromResponse(response);
    }
    return ChatResponse.fromJson(
      jsonDecode(response.body) as Map<String, dynamic>,
    );
  }

  Future<String> _createSession(LatLng location) async {
    final response = await http
        .post(
          _uri('/api/session'),
          headers: _headers,
          body: jsonEncode({'user_location': _locationJson(location)}),
        )
        .timeout(const Duration(seconds: 20));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw _errorFromResponse(response);
    }
    final body = jsonDecode(response.body) as Map<String, dynamic>;
    final sessionId = body['session_id'] as String?;
    if (sessionId == null || sessionId.isEmpty) {
      throw const ChatApiException('伺服器未回傳有效的 session_id。');
    }
    return sessionId;
  }

  Future<http.Response> _postMessage({
    required String sessionId,
    required String message,
    required LatLng location,
    required double priorityAlpha,
  }) => http
      .post(
        _uri('/api/chat'),
        headers: _headers,
        body: jsonEncode({
          'session_id': sessionId,
          'message': message,
          'user_location': _locationJson(location),
          'priority_alpha': priorityAlpha,
        }),
      )
      .timeout(const Duration(seconds: 60));

  Uri _uri(String path) =>
      Uri.parse('${_apiBaseUrl.replaceFirst(RegExp(r'/$'), '')}$path');
  Map<String, String> get _headers => const {
    'Content-Type': 'application/json',
  };
  Map<String, double> _locationJson(LatLng location) => {
    'lat': location.latitude,
    'lng': location.longitude,
  };
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

class ChatApiException implements Exception {
  const ChatApiException(this.message);
  final String message;
}

class ChatMessage {
  const ChatMessage(
    this.text, {
    required this.isUser,
    this.isError = false,
    this.routeResponse,
  });
  factory ChatMessage.user(String text) => ChatMessage(text, isUser: true);
  factory ChatMessage.assistant(
    String text, {
    bool isError = false,
    RouteReadyResponse? routeResponse,
  }) => ChatMessage(
    text,
    isUser: false,
    isError: isError,
    routeResponse: routeResponse,
  );
  final String text;
  final bool isUser, isError;
  final RouteReadyResponse? routeResponse;
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
    required this.route,
    required this.warnings,
    required this.dynamicHazards,
    required this.origin,
    required this.destination,
    this.googleMapsUrl,
  }) : super(replyText);
  final String disclaimer;
  final SafeRoute route;
  final List<String> warnings;
  final List<DynamicHazard> dynamicHazards;
  final RouteEndpoint? origin;
  final RouteEndpoint? destination;
  final String? googleMapsUrl;
  factory RouteReadyResponse.fromJson(Map<String, dynamic> json) {
    final route = SafeRoute.fromJson(
      json['route'] as Map<String, dynamic>? ?? {},
    );
    return RouteReadyResponse(
      // route_ready 依新合約不提供 reply_text；摘要完全由結構化 metrics 組成。
      replyText:
          '已規劃較安全的步行路線：沿途有 ${route.metrics.convenienceStores.length} 間 24 小時便利商店與 ${route.metrics.policeStations.length} 間警察局。',
      disclaimer: json['disclaimer'] as String? ?? '此建議無法保證安全。',
      route: route,
      warnings: _warningsFromJson(
        // 新格式在頂層；保留 route.warnings fallback 方便前後端滾動更新。
        json['warnings'] ??
            (json['route'] as Map<String, dynamic>?)?['warnings'],
      ),
      dynamicHazards:
          (json['dynamic_hazards_considered'] as List<dynamic>? ?? [])
              .map(
                (item) => DynamicHazard.fromJson(item as Map<String, dynamic>),
              )
              .toList(),
      origin: RouteEndpoint.fromJson(json['origin']),
      destination: RouteEndpoint.fromJson(json['destination']),
      googleMapsUrl: json['google_maps_url'] as String?,
    );
  }
  factory RouteReadyResponse.demo(LatLng location, String message) {
    final safest = SafeRoute.demo(location, safest: true);
    return RouteReadyResponse(
      replyText: '示範模式：已規劃一條較安全的步行路線。',
      disclaimer: '此建議依公開資料與即時資訊產生，無法保證安全；緊急狀況請立即撥打 110 或 119。',
      route: safest,
      warnings: const [],
      dynamicHazards: const [],
      origin: RouteEndpoint(
        lat: location.latitude,
        lng: location.longitude,
        name: '目前位置',
      ),
      destination: RouteEndpoint(
        lat: safest.path.last.latitude,
        lng: safest.path.last.longitude,
        name: '示範目的地',
      ),
      googleMapsUrl: null,
    );
  }
}

class RouteEndpoint {
  const RouteEndpoint({
    required this.lat,
    required this.lng,
    this.placeId,
    this.name,
  });

  final double lat, lng;
  final String? placeId, name;
  LatLng get position => LatLng(lat, lng);
  String displayName(String fallback) =>
      name?.trim().isNotEmpty == true ? name! : fallback;

  static RouteEndpoint? fromJson(dynamic value) {
    if (value is! Map) return null;
    final json = Map<String, dynamic>.from(value);
    final lat = json['lat'];
    final lng = json['lng'];
    if (lat is! num || lng is! num) return null;
    return RouteEndpoint(
      lat: lat.toDouble(),
      lng: lng.toDouble(),
      placeId: json['place_id'] as String?,
      name: json['name'] as String?,
    );
  }
}

class SafeRoute {
  const SafeRoute({
    required this.path,
    required this.alpha,
    required this.metrics,
  });
  final List<LatLng> path;
  final double alpha;
  final RouteMetrics metrics;
  factory SafeRoute.fromJson(Map<String, dynamic> json) => SafeRoute(
    path: _coordinates(json['path_coordinates']),
    alpha: (json['alpha_used'] as num? ?? .6).toDouble(),
    metrics: RouteMetrics.fromJson(
      json['metrics'] as Map<String, dynamic>? ?? {},
    ),
  );
  factory SafeRoute.demo(LatLng start, {required bool safest}) => SafeRoute(
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
    metrics: RouteMetrics(
      distanceMeters: safest ? 1420 : 1180,
      durationMinutes: safest ? 18 : 14,
      avgSafetyScore: safest ? .78 : .52,
      litCoverageRatio: safest ? .71 : .45,
      convenienceStores: safest
          ? [
              NearbyPoint(
                id: 'demo-help',
                lat: start.latitude + .002,
                lng: start.longitude + .002,
                name: '示範 24 小時便利商店',
              ),
            ]
          : const [],
      policeStations: safest
          ? [
              NearbyPoint(
                id: 'demo-police',
                lat: start.latitude + .004,
                lng: start.longitude + .004,
                name: '示範警察局',
              ),
            ]
          : const [],
      dangerZones: safest
          ? [
              NearbyPoint(
                id: 'demo-danger',
                lat: start.latitude + .003,
                lng: start.longitude + .003,
                name: '示範需留意地點',
                summary: '此處為示範資料，請留意周遭環境。',
              ),
            ]
          : const [],
      avoidedDangerZones: const [],
    ),
  );
}

class RouteMetrics {
  const RouteMetrics({
    required this.distanceMeters,
    required this.durationMinutes,
    required this.avgSafetyScore,
    required this.litCoverageRatio,
    required this.convenienceStores,
    required this.policeStations,
    required this.dangerZones,
    required this.avoidedDangerZones,
  });
  final int distanceMeters, durationMinutes;
  final double avgSafetyScore;
  final double? litCoverageRatio;
  factory RouteMetrics.fromJson(Map<String, dynamic> json) => RouteMetrics(
    // 後端依 API contract 回傳浮點數（例如 455.3m、5.8 分鐘）；
    // 顯示卡片使用整數，因此在這裡才四捨五入，不能直接 cast 成 int。
    distanceMeters: (json['distance_m'] as num? ?? 0).round(),
    durationMinutes: (json['duration_min_est'] as num? ?? 0).round(),
    avgSafetyScore: (json['avg_safety_score'] as num? ?? 0).toDouble(),
    litCoverageRatio: (json['lit_coverage_ratio'] as num?)?.toDouble(),
    convenienceStores: _nearbyPoints(
      // help_points 是舊後端欄位，僅作相容 fallback。
      json['convenience_stores'] ?? json['help_points'],
    ),
    policeStations: _nearbyPoints(json['police_stations']),
    dangerZones: _nearbyPoints(json['danger_zones']),
    avoidedDangerZones: _nearbyPoints(json['avoided_danger_zones']),
  );
  final List<NearbyPoint> convenienceStores,
      policeStations,
      dangerZones,
      avoidedDangerZones;
}

class NearbyPoint {
  const NearbyPoint({
    required this.id,
    required this.lat,
    required this.lng,
    this.name,
    this.placeId,
    this.summary,
  });
  final String id;
  final double lat, lng;
  final String? name;
  final String? placeId;
  final String? summary;
  LatLng get position => LatLng(lat, lng);
  String displayName(String fallback) =>
      name?.trim().isNotEmpty == true ? name! : fallback;

  factory NearbyPoint.fromJson(Map<String, dynamic> json) => NearbyPoint(
    id:
        (json['place_id'] ?? json['id'])?.toString() ??
        '${json['lat']}-${json['lng']}',
    lat: (json['lat'] as num? ?? 0).toDouble(),
    lng: (json['lng'] as num? ?? 0).toDouble(),
    name: json['name'] as String?,
    placeId: json['place_id'] as String?,
    summary: json['summary'] as String?,
  );
}

class DynamicHazard {
  const DynamicHazard(this.summary);
  final String summary;
  factory DynamicHazard.fromJson(Map<String, dynamic> json) =>
      DynamicHazard(json['summary'] as String? ?? '近期事件');
}

List<NearbyPoint> _nearbyPoints(dynamic value) =>
    (value as List<dynamic>? ?? [])
        .whereType<Map>()
        .map((item) => NearbyPoint.fromJson(Map<String, dynamic>.from(item)))
        // 後端無此類資料時回傳 null；若資料不完整，則不在地圖上畫到 (0, 0)。
        .where((point) => point.lat != 0 || point.lng != 0)
        .toList();

List<String> _warningsFromJson(dynamic value) => (value as List<dynamic>? ?? [])
    .whereType<Map>()
    .map((warning) => _warningText(Map<String, dynamic>.from(warning)))
    .toList();

String _warningText(Map<String, dynamic> warning) {
  switch (warning['code']) {
    case 'missing_data_category':
      return '缺少 ${warning['category'] ?? '部分'} 資料，未納入評分。';
    case 'unknown_hazard_category':
      return '即時事件類別 ${warning['category'] ?? ''} 未登記，已採低權重處理。';
    case 'hazard_expired':
      return '即時事件已過期，未納入本次計算。';
    default:
      return '部分安全資料可能不足。';
  }
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
