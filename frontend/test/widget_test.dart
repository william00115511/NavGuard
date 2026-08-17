// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'package:flutter_test/flutter_test.dart';

import 'package:safeway_frontend/main.dart';

void main() {
  testWidgets('shows the safety chat controls', (WidgetTester tester) async {
    await tester.pumpWidget(const NavGuardApp());
    await tester.pump();
    await tester.pumpAndSettle();

    expect(find.textContaining('夜間步行輔助建議'), findsOneWidget);
    expect(find.textContaining('前往台北市政府'), findsOneWidget);
    expect(find.textContaining('你好，我是 NavGuard'), findsOneWidget);
  });

  test(
    'parses route markers and top-level warnings from the new API format',
    () {
      final response =
          ChatResponse.fromJson({
                'status': 'route_ready',
                'disclaimer': '此建議無法保證安全。',
                'route': {
                  'path_coordinates': [
                    [25.04, 121.53],
                    [25.05, 121.54],
                  ],
                  'alpha_used': 0.6,
                  'metrics': {
                    'distance_m': 1200.4,
                    'duration_min_est': 15.6,
                    'avg_safety_score': 0.72,
                    'lit_coverage_ratio': 0.8,
                    'convenience_stores': [
                      {
                        'place_id': 'store-place-id',
                        'lat': 25.041,
                        'lng': 121.531,
                        'name': '24 小時便利商店',
                      },
                    ],
                    'police_stations': [
                      {
                        'place_id': 'police-place-id',
                        'lat': 25.042,
                        'lng': 121.532,
                        'name': '測試派出所',
                      },
                    ],
                    'danger_zones': [
                      {
                        'place_id': null,
                        'lat': 25.043,
                        'lng': 121.533,
                        'name': '需留意路口',
                        'summary': '夜間人潮較複雜。',
                      },
                    ],
                    'avoided_danger_zones': [
                      {
                        'place_id': 'avoided-danger-id',
                        'lat': 25.044,
                        'lng': 121.534,
                        'name': '已繞開路口',
                        'summary': '推薦路線已繞開此處。',
                      },
                    ],
                  },
                },
                'warnings': [
                  {'code': 'missing_data_category', 'category': 'street_light'},
                ],
                'origin': {
                  'lat': 25.04,
                  'lng': 121.53,
                  'place_id': null,
                  'name': null,
                },
                'destination': {
                  'lat': 25.05,
                  'lng': 121.54,
                  'place_id': 'destination-place-id',
                  'name': '台北市政府',
                },
                'dynamic_hazards_considered': [],
              })
              as RouteReadyResponse;

      expect(
        response.route.metrics.convenienceStores.single.placeId,
        'store-place-id',
      );
      expect(response.route.metrics.policeStations.single.name, '測試派出所');
      expect(response.route.metrics.dangerZones.single.summary, '夜間人潮較複雜。');
      expect(
        response.route.metrics.avoidedDangerZones.single.placeId,
        'avoided-danger-id',
      );
      expect(response.warnings.single, contains('street_light'));
      expect(response.origin?.position.latitude, 25.04);
      expect(response.origin?.displayName('目前位置'), '目前位置');
      expect(response.destination?.placeId, 'destination-place-id');
      expect(response.destination?.displayName('目的地'), '台北市政府');
    },
  );

  test('keeps route parsing compatible when endpoint fields are absent', () {
    final response =
        ChatResponse.fromJson({
              'status': 'route_ready',
              'route': {
                'path_coordinates': [
                  [25.04, 121.53],
                  [25.05, 121.54],
                ],
                'metrics': <String, dynamic>{},
              },
            })
            as RouteReadyResponse;

    expect(response.origin, isNull);
    expect(response.destination, isNull);
  });
}
