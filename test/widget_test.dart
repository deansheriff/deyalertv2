import 'package:dey_alert/core/router.dart';
import 'package:dey_alert/core/services/offline_queue.dart';
import 'package:dey_alert/core/services/sms_composer.dart';
import 'package:dey_alert/core/theme/app_theme.dart';
import 'package:dey_alert/core/theme/tokens.dart';
import 'package:dey_alert/core/ui.dart';
import 'package:dey_alert/features/auth/auth_screens.dart';
import 'package:dey_alert/features/map/map_tab.dart';
import 'package:dey_alert/features/sos/sos_screens.dart';
import 'package:dey_alert/main.dart';
import 'package:dey_alert/models/models.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class MemoryStore implements QueueStore {
  final items = <String, QueueItem>{};

  @override
  Future<List<QueueItem>> all() async => items.values.toList();

  @override
  Future<void> remove(String clientId) async => items.remove(clientId);

  @override
  Future<void> upsert(QueueItem item) async => items[item.clientId] = item;
}

QueueItem item(String id) =>
    QueueItem(clientId: id, kind: 'incident', path: '/v1/incidents', body: {'client_id': id});

Incident incident({String status = 'unconfirmed', String severity = 'high', double lat = 6.5}) =>
    Incident(
      id: '$lat$status',
      typeCode: 'roadblock',
      lat: lat,
      lng: 3.3,
      status: status,
      severity: severity,
      corroborationCount: 4,
      occurredAt: DateTime(2026),
      expiresAt: DateTime(2100),
      confirmedByLabel: 'Community Chairman, Ward 4',
    );

Widget wrap(Widget child) => MaterialApp(
  theme: AppTheme.dark,
  home: Scaffold(body: Center(child: child)),
);

void main() {
  testWidgets('a build without configuration explains itself', (tester) async {
    await tester.pumpWidget(const MissingConfigApp());
    expect(find.text('This build is missing its server configuration.'), findsOneWidget);
  });

  test('themes use the brand tokens', () {
    expect(AppTheme.dark.colorScheme.primary, DeyTokens.primary);
    expect(AppTheme.light.colorScheme.primary, DeyTokens.primaryOnLight);
    for (final theme in [AppTheme.dark, AppTheme.light]) {
      expect(theme.colorScheme.secondary, DeyTokens.accent);
      expect(theme.colorScheme.error, DeyTokens.danger);
    }
  });

  group('offline queue', () {
    test('sends once and clears the item', () async {
      final queue = OfflineQueue(MemoryStore());
      await queue.enqueue(item('a'));
      var calls = 0;
      expect(
        await queue.flush((_) async {
          calls++;
          return SendOutcome.sent;
        }),
        1,
      );
      expect(calls, 1);
      expect(await queue.pendingCount(), 0);
    });

    test('backs off after a network failure and retries when due', () async {
      var now = DateTime(2026);
      final queue = OfflineQueue(MemoryStore(), clock: () => now);
      await queue.enqueue(item('a'));
      await queue.flush((_) async => SendOutcome.retry);
      expect((await queue.store.all()).single.attempts, 1);

      var calls = 0;
      Future<SendOutcome> count(QueueItem _) async {
        calls++;
        return SendOutcome.sent;
      }

      await queue.flush(count);
      expect(calls, 0);
      now = now.add(const Duration(minutes: 5));
      await queue.flush(count);
      expect(calls, 1);
    });

    test('a rejected report stops retrying and can be retried by hand', () async {
      final queue = OfflineQueue(MemoryStore());
      await queue.enqueue(item('a'));
      await queue.flush((_) async => SendOutcome.rejected);
      expect((await queue.store.all()).single.state, QueueState.failed);
      expect(await queue.flush((_) async => SendOutcome.sent), 0);
      await queue.retryFailed();
      expect(await queue.flush((_) async => SendOutcome.sent), 1);
    });

    test('keeps the same id across retries so the server can dedupe', () async {
      final queue = OfflineQueue(MemoryStore());
      await queue.enqueue(item('a'));
      final seen = <String>[];
      Future<SendOutcome> record(QueueItem sending) async {
        seen.add(sending.clientId);
        return SendOutcome.retry;
      }

      await queue.flush(record);
      final stored = (await queue.store.all()).single;
      await queue.store.upsert(stored.copyWith(nextAttemptAt: 0));
      await queue.flush(record);
      expect(seen, ['a', 'a']);
    });

    test('maps HTTP status to outcome', () {
      expect(outcomeForStatus(null), SendOutcome.retry);
      expect(outcomeForStatus(201), SendOutcome.sent);
      expect(outcomeForStatus(429), SendOutcome.retry);
      expect(outcomeForStatus(503), SendOutcome.retry);
      expect(outcomeForStatus(422), SendOutcome.rejected);
    });
  });

  group('SOS composer', () {
    test('message has plain coordinates and a map link', () {
      final message = sosMessage(lat: 6.5, lng: 3.3);
      expect(message, contains('6.50000, 3.30000'));
      expect(message, contains('maps.google.com/?q=6.50000,3.30000'));
    });

    test('message still works without a location', () {
      expect(sosMessage(), contains('could not get my location'));
    });

    test('composer URI addresses every contact and keeps the body intact', () {
      final uri = smsComposerUri(['+2348011111111', '+2348022222222'], 'help & now');
      expect(uri.scheme, 'sms');
      expect(uri.path, '+2348011111111,+2348022222222');
      expect(uri.query, 'body=help%20%26%20now');
    });
  });

  group('hold to activate', () {
    testWidgets('fires only after the full hold', (tester) async {
      var fired = 0;
      await tester.pumpWidget(wrap(HoldToActivate(onComplete: () => fired++)));
      final gesture = await tester.startGesture(tester.getCenter(find.text('SOS')));
      await tester.pump(const Duration(milliseconds: 200));
      await tester.pump(const Duration(seconds: 2));
      expect(fired, 0);
      await tester.pump(const Duration(milliseconds: 1500));
      expect(fired, 1);
      await gesture.up();
    });

    testWidgets('releasing early cancels', (tester) async {
      var fired = 0;
      await tester.pumpWidget(wrap(HoldToActivate(onComplete: () => fired++)));
      final gesture = await tester.startGesture(tester.getCenter(find.text('SOS')));
      await tester.pump(const Duration(milliseconds: 200));
      await tester.pump(const Duration(seconds: 2));
      await gesture.up();
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      expect(fired, 0);
    });
  });

  group('labels', () {
    testWidgets('unverified never reads as confirmed', (tester) async {
      await tester.pumpWidget(wrap(VerificationBadge(incident())));
      expect(find.text('Unverified'), findsOneWidget);
      expect(find.textContaining('Confirmed'), findsNothing);
    });

    testWidgets('confirmed names who confirmed it', (tester) async {
      await tester.pumpWidget(wrap(VerificationBadge(incident(status: 'confirmed'))));
      expect(find.text('Confirmed by Community Chairman, Ward 4'), findsOneWidget);
    });

    for (final (severity, label) in const [
      ('low', 'Low'),
      ('medium', 'Medium'),
      ('high', 'High'),
      ('critical', 'Critical'),
    ]) {
      testWidgets('$severity severity has a text label and icon', (tester) async {
        await tester.pumpWidget(wrap(SeverityChip(severity)));
        expect(find.text(label), findsOneWidget);
        expect(find.byType(Icon), findsOneWidget);
      });
    }

    test('severity colours are never brand lime', () {
      for (final severity in ['low', 'medium', 'high', 'critical']) {
        expect(severityColor(severity), isNot(DeyTokens.primary));
      }
    });
  });

  group('phone numbers', () {
    test('accepts common Nigerian formats', () {
      for (final input in ['08023456789', '8023456789', '+2348023456789', '234 802 345 6789']) {
        expect(normalizeNigerianPhone(input), '+2348023456789');
      }
    });

    test('rejects invalid numbers', () {
      for (final input in ['12345', '05023456789', '']) {
        expect(normalizeNigerianPhone(input), isNull);
      }
    });
  });

  group('map clustering', () {
    test('leaves small sets unclustered', () {
      expect(clusterIncidents([incident(), incident(lat: 6.6)], 0.01), hasLength(2));
    });

    test('groups nearby pins above the threshold', () {
      final many = [for (var i = 0; i < 60; i++) incident(lat: 6.5 + i * 0.0001)];
      expect(clusterIncidents(many, 0.01).length, lessThan(5));
    });
  });

  group('navigation gates', () {
    String? gate(String location, {bool language = true, bool onboarded = true, bool signedIn = true, bool profile = true}) =>
        gateRedirect(
          location: location,
          languageChosen: language,
          onboarded: onboarded,
          signedIn: signedIn,
          profileDone: profile,
        );

    test('walks through setup in order', () {
      expect(gate('/map', language: false), '/language');
      expect(gate('/map', onboarded: false), '/welcome');
      expect(gate('/map', signedIn: false), '/phone');
      expect(gate('/map', profile: false), '/permissions/location');
      expect(gate('/map'), isNull);
    });

    test('signed-in users cannot return to setup', () {
      expect(gate('/phone'), '/map');
    });

    test('battery guide stays reachable from settings', () {
      expect(gate('/permissions/battery'), isNull);
    });
  });

  group('filters', () {
    test('match type and status', () {
      const filter = FeedFilter(types: {'roadblock'}, statuses: {'confirmed'});
      expect(filter.matches(incident(status: 'confirmed')), isTrue);
      expect(filter.matches(incident()), isFalse);
    });

    test('only windows past 24 hours include history', () {
      expect(const FeedFilter().includesHistory, isFalse);
      expect(const FeedFilter(window: Duration(days: 7)).includesHistory, isTrue);
    });
  });

  test('incident round-trips through the offline cache format', () {
    final original = incident(status: 'confirmed');
    final restored = Incident.fromJson(original.toJson());
    expect(restored.status, 'confirmed');
    expect(restored.confirmedByLabel, original.confirmedByLabel);
  });
}
