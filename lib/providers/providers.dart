import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../core/i18n.dart';
import '../core/services/location_service.dart';
import '../core/services/push_service.dart';
import '../core/services/repository.dart';
import '../models/models.dart';

final repositoryProvider = Provider<Repository>(
  (_) => throw UnimplementedError('Provided in main'),
);
final appStateProvider = ChangeNotifierProvider<AppState>(
  (_) => throw UnimplementedError('Provided in main'),
);
final pushProvider = Provider<PushService>((_) => PushService());

const defaultCenter = LatLngPoint(6.5244, 3.3792);

class AppState extends ChangeNotifier {
  AppState(this.prefs) {
    currentLanguage = prefs.getString('language') ?? 'en';
    _dataSaver = prefs.getBool('data_saver') ?? false;
  }

  final SharedPreferences prefs;
  late bool _dataSaver;

  bool get languageChosen => prefs.getBool('language_chosen') ?? false;
  bool get onboarded => prefs.getBool('onboarded') ?? false;
  bool get profileDone => prefs.getBool('profile_done') ?? false;
  bool get backgroundDisclosureAccepted =>
      prefs.getBool('bg_disclosure') ?? false;
  bool get dataSaver => _dataSaver;

  Future<void> _set(String key, bool value) async {
    await prefs.setBool(key, value);
    notifyListeners();
  }

  Future<void> chooseLanguage(String code) async {
    currentLanguage = code;
    await prefs.setString('language', code);
    await _set('language_chosen', true);
  }

  Future<void> finishOnboarding() => _set('onboarded', true);
  Future<void> finishProfile() => _set('profile_done', true);
  Future<void> acceptBackgroundDisclosure() => _set('bg_disclosure', true);

  Future<void> setDataSaver(bool value) async {
    _dataSaver = value;
    await _set('data_saver', value);
  }

  void authChanged() => notifyListeners();
}

final profileProvider = FutureProvider.autoDispose<UserProfile>(
  (ref) => ref.watch(repositoryProvider).profile(),
);

class FilterNotifier extends Notifier<FeedFilter> {
  @override
  FeedFilter build() => const FeedFilter();

  void set(FeedFilter filter) => state = filter;
}

final filterProvider = NotifierProvider<FilterNotifier, FeedFilter>(
  FilterNotifier.new,
);

final centerProvider = FutureProvider<LatLngPoint>((ref) async {
  final repository = ref.watch(repositoryProvider);
  final point = await repository.location.current();
  if (point != null) return point;
  try {
    final profile = await repository.profile();
    if (profile.hasHome) return LatLngPoint(profile.homeLat!, profile.homeLng!);
  } on Exception {
    // Fall through to the default area.
  }
  return defaultCenter;
});

final feedProvider = FutureProvider<FeedResult>((ref) async {
  final repository = ref.watch(repositoryProvider);
  final filter = ref.watch(filterProvider);
  final center = await ref.watch(centerProvider.future);
  return repository.feed(center.lat, center.lng, filter);
});

final contactsProvider = FutureProvider.autoDispose<List<TrustedContact>>(
  (ref) => ref.watch(repositoryProvider).contacts(),
);

final pendingCountProvider = FutureProvider.autoDispose<int>(
  (ref) => ref.watch(repositoryProvider).queue.pendingCount(),
);

Stream<AuthState> authStream() =>
    Supabase.instance.client.auth.onAuthStateChange;
