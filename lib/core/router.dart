import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../features/auth/auth_screens.dart';
import '../features/community/community_screens.dart';
import '../features/feed/all_reports_tab.dart';
import '../features/feed/feed_tab.dart';
import '../features/incidents/incident_screens.dart';
import '../features/onboarding/onboarding_screens.dart';
import '../features/report/report_screens.dart';
import '../features/settings/profile_screens.dart';
import '../features/sos/sos_screens.dart';
import '../models/models.dart';
import '../providers/providers.dart';
import 'i18n.dart';
import 'services/location_service.dart';
import 'services/repository.dart';
import 'theme/tokens.dart';

final rootNavigatorKey = GlobalKey<NavigatorState>();

String? gateRedirect({
  required String location,
  required bool languageChosen,
  required bool onboarded,
  required bool signedIn,
  required bool profileDone,
}) {
  if (!languageChosen) return location == '/language' ? null : '/language';
  if (!onboarded) return location == '/welcome' ? null : '/welcome';
  if (!signedIn) {
    return location == '/sign-in' || location == '/verify' ? null : '/sign-in';
  }
  if (!profileDone) {
    const allowed = {
      '/permissions/location',
      '/permissions/notifications',
      '/permissions/battery',
      '/profile-setup',
      '/report/location',
    };
    return allowed.contains(location) ? null : '/permissions/location';
  }
  const setup = {
    '/language',
    '/welcome',
    '/sign-in',
    '/verify',
    '/permissions/location',
    '/permissions/notifications',
    '/profile-setup',
  };
  return setup.contains(location) ? '/all' : null;
}

GoRouter buildRouter(AppState state, Repository repository) => GoRouter(
  navigatorKey: rootNavigatorKey,
  initialLocation: '/all',
  refreshListenable: state,
  redirect: (_, routerState) => gateRedirect(
    location: routerState.matchedLocation,
    languageChosen: state.languageChosen,
    onboarded: state.onboarded,
    signedIn: repository.signedIn,
    profileDone: state.profileDone,
  ),
  routes: [
    GoRoute(path: '/language', builder: (_, _) => const LanguageScreen()),
    GoRoute(path: '/welcome', builder: (_, _) => const WelcomeScreen()),
    GoRoute(path: '/sign-in', builder: (_, _) => const SignInScreen()),
    GoRoute(
      path: '/verify',
      builder: (_, routerState) =>
          VerifyCodeScreen(target: routerState.extra! as SignInTarget),
    ),
    GoRoute(
      path: '/permissions/location',
      builder: (_, _) => const LocationPermissionScreen(),
    ),
    GoRoute(
      path: '/permissions/notifications',
      builder: (_, _) => const NotificationPermissionScreen(),
    ),
    GoRoute(path: '/permissions/battery', builder: (_, _) => const BatteryGuideScreen()),
    GoRoute(path: '/profile-setup', builder: (_, _) => const ProfileSetupScreen()),
    ShellRoute(
      builder: (_, routerState, child) =>
          HomeShell(location: routerState.matchedLocation, child: child),
      routes: [
        GoRoute(
          path: '/all',
          pageBuilder: (_, _) => const NoTransitionPage(child: AllReportsTab()),
        ),
        GoRoute(path: '/feed', pageBuilder: (_, _) => const NoTransitionPage(child: FeedTab())),
        GoRoute(path: '/report', pageBuilder: (_, _) => const NoTransitionPage(child: ReportTab())),
        GoRoute(path: '/sos', pageBuilder: (_, _) => const NoTransitionPage(child: SosTab())),
        GoRoute(path: '/profile', pageBuilder: (_, _) => const NoTransitionPage(child: ProfileTab())),
      ],
    ),
    GoRoute(
      path: '/report/location',
      builder: (_, routerState) =>
          LocationPickerScreen(initial: routerState.extra as LatLngPoint?),
    ),
    GoRoute(
      path: '/report/result',
      builder: (_, routerState) => ReportResultScreen(sent: routerState.extra! as bool),
    ),
    GoRoute(
      path: '/incident/:id',
      builder: (_, routerState) =>
          IncidentDetailScreen(id: routerState.pathParameters['id']!),
    ),
    GoRoute(path: '/sos/countdown', builder: (_, _) => const SosCountdownScreen()),
    GoRoute(
      path: '/sos/disclosure',
      builder: (_, routerState) =>
          BackgroundDisclosureScreen(sosId: routerState.extra! as String),
    ),
    GoRoute(
      path: '/sos/live',
      builder: (_, routerState) => SosLiveScreen(sosId: routerState.extra! as String),
    ),
    GoRoute(path: '/sos/resolved', builder: (_, _) => const SosResolvedScreen()),
    GoRoute(path: '/contacts', builder: (_, _) => const ContactsScreen()),
    GoRoute(
      path: '/contacts/edit',
      builder: (_, routerState) =>
          ContactEditScreen(contact: routerState.extra as TrustedContact?),
    ),
    GoRoute(path: '/checkin', builder: (_, _) => const CheckinSetupScreen()),
    GoRoute(
      path: '/checkin/active',
      builder: (_, routerState) {
        final (id, dueAt) = routerState.extra! as (String, DateTime);
        return CheckinActiveScreen(id: id, dueAt: dueAt);
      },
    ),
    GoRoute(path: '/notifications', builder: (_, _) => const NotificationsScreen()),
    GoRoute(path: '/my-reports', builder: (_, _) => const MyReportsScreen()),
    GoRoute(path: '/settings', builder: (_, _) => const SettingsScreen()),
    GoRoute(path: '/settings/language', builder: (_, _) => const LanguageSettingsScreen()),
    GoRoute(path: '/settings/quiet', builder: (_, _) => const QuietHoursScreen()),
    GoRoute(path: '/settings/account', builder: (_, _) => const AccountDataScreen()),
    GoRoute(path: '/legal', builder: (_, _) => const LegalScreen()),
    GoRoute(path: '/zones', builder: (_, _) => const WatchZonesScreen()),
    GoRoute(path: '/support', builder: (_, _) => const SupportScreen()),
    GoRoute(path: '/verifier/apply', builder: (_, _) => const VerifierApplicationScreen()),
    GoRoute(path: '/settings/correction', builder: (_, _) => const CorrectionScreen()),
  ],
);

class HomeShell extends StatelessWidget {
  const HomeShell({required this.location, required this.child, super.key});

  final String location;
  final Widget child;

  static const _tabs = [
    ('/all', Icons.public, 'All'),
    ('/feed', Icons.near_me_outlined, 'Near you'),
    ('/report', Icons.add_alert_outlined, 'Report'),
    ('/sos', Icons.emergency_outlined, 'SOS'),
    ('/profile', Icons.person_outline, 'Profile'),
  ];

  @override
  Widget build(BuildContext context) => Scaffold(
    extendBody: true,
    body: child,
    bottomNavigationBar: SafeArea(
      child: Container(
        margin: const EdgeInsets.fromLTRB(12, 0, 12, 10),
        padding: const EdgeInsets.all(6),
        decoration: BoxDecoration(
          color: DeyTokens.darkSurface,
          borderRadius: BorderRadius.circular(DeyTokens.pillRadius),
          border: Border.all(color: DeyTokens.darkBorder),
        ),
        child: Row(
          children: [
            for (final (path, icon, label) in _tabs)
              Expanded(
                child: _NavItem(
                  icon: icon,
                  label: t(label),
                  active: location == path,
                  danger: path == '/sos',
                  onTap: () => context.go(path),
                ),
              ),
          ],
        ),
      ),
    ),
  );
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.icon,
    required this.label,
    required this.active,
    required this.danger,
    required this.onTap,
  });

  final IconData icon;
  final String label;
  final bool active;
  final bool danger;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = active
        ? DeyTokens.onPrimary
        : danger
        ? DeyTokens.danger
        : DeyTokens.darkSecondaryText;
    return Material(
      color: active ? DeyTokens.primary : Colors.transparent,
      shape: const StadiumBorder(),
      child: InkWell(
        customBorder: const StadiumBorder(),
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 52),
          // The bottom slot allows the whole screen height; take only what the tab needs.
          child: Column(
            mainAxisSize: MainAxisSize.min,
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(icon, size: 22, color: color),
              Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.w600),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
