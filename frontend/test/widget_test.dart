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
    await tester.pumpWidget(const SafewayApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.textContaining('夜間步行輔助建議'), findsOneWidget);
    expect(find.textContaining('從台北車站走到公館夜市'), findsOneWidget);
    expect(find.textContaining('晚上好，我是 Safeway'), findsOneWidget);
  });
}
