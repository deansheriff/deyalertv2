import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:sentry_flutter/sentry_flutter.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'core/config/env.dart';
import 'core/router.dart';
import 'core/services/offline_queue.dart';
import 'core/services/repository.dart';
import 'core/theme/app_theme.dart';
import 'core/ui.dart';
import 'providers/providers.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  if (!Env.isConfigured) {
    runApp(const MissingConfigApp());
    return;
  }
  if (Env.sentryDsn.isEmpty) {
    await _start();
  } else {
    await SentryFlutter.init(
      (options) => options.dsn = Env.sentryDsn,
      appRunner: _start,
    );
  }
}

Future<void> _start() async {
  await Supabase.initialize(
    url: Env.supabaseUrl,
    publishableKey: Env.supabaseAnonKey,
  );
  final prefs = await SharedPreferences.getInstance();
  final database = await SqliteQueueStore.open();
  final repository = Repository(OfflineQueue(SqliteQueueStore(database)), prefs);
  final appState = AppState(prefs);
  final router = buildRouter(appState, repository);
  authStream().listen((_) => appState.authChanged());
  Connectivity().onConnectivityChanged.listen((results) {
    if (!results.contains(ConnectivityResult.none) && repository.signedIn) {
      unawaited(repository.flushQueue());
    }
  });

  runApp(
    ProviderScope(
      overrides: [
        repositoryProvider.overrideWithValue(repository),
        appStateProvider.overrideWith((_) => appState),
      ],
      child: DeyAlertApp(router: router),
    ),
  );
}

class DeyAlertApp extends ConsumerStatefulWidget {
  const DeyAlertApp({required this.router, super.key});

  final GoRouter router;

  @override
  ConsumerState<DeyAlertApp> createState() => _DeyAlertAppState();
}

class _DeyAlertAppState extends ConsumerState<DeyAlertApp> {
  @override
  void initState() {
    super.initState();
    unawaited(
      ref
          .read(pushProvider)
          .init((incidentId) => widget.router.push('/incident/$incidentId')),
    );
    unawaited(_sync());
  }

  Future<void> _sync() async {
    final repository = ref.read(repositoryProvider);
    if (!repository.signedIn) return;
    try {
      await repository.flushQueue();
      final point = await repository.location.current();
      await repository.registerDevice(
        fcmToken: await ref.read(pushProvider).token(),
        point: point,
      );
      await repository.contacts();
    } on ApiException {
      // Offline at launch. The queue retries when connectivity returns.
    }
  }

  @override
  Widget build(BuildContext context) {
    ref.watch(appStateProvider);
    return MaterialApp.router(
      title: 'Dey Alert',
      theme: AppTheme.light,
      darkTheme: AppTheme.dark,
      themeMode: ThemeMode.dark,
      routerConfig: widget.router,
    );
  }
}

class MissingConfigApp extends StatelessWidget {
  const MissingConfigApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Dey Alert',
    theme: AppTheme.light,
    darkTheme: AppTheme.dark,
    themeMode: ThemeMode.dark,
    home: const Scaffold(
      body: CenterMessage(
        icon: Icons.settings_outlined,
        title: 'Dey Alert',
        body: 'This build is missing its server configuration.',
      ),
    ),
  );
}
