import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:go_router/go_router.dart';
import 'package:permission_handler/permission_handler.dart';

import '../../core/i18n.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../providers/providers.dart';

const _languages = [
  ('en', 'English', 'English'),
  ('pcm', 'Naijá', 'Nigerian Pidgin'),
  ('ha', 'Hausa', 'Hausa'),
  ('yo', 'Yorùbá', 'Yoruba'),
  ('ig', 'Asụsụ Igbo', 'Igbo'),
];

class LanguageScreen extends ConsumerStatefulWidget {
  const LanguageScreen({super.key});

  @override
  ConsumerState<LanguageScreen> createState() => _LanguageScreenState();
}

class _LanguageScreenState extends ConsumerState<LanguageScreen> {
  String _selected = currentLanguage;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 24),
            Heading(t('Choose your language')),
            const SizedBox(height: 20),
            Expanded(
              child: ListView(
                children: [
                  for (final (code, native, english) in _languages)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: LanguageCard(
                        native: native,
                        english: english,
                        available: launchLanguages.contains(code),
                        selected: _selected == code,
                        onTap: () => setState(() => _selected = code),
                      ),
                    ),
                ],
              ),
            ),
            PillButton(
              t('Continue'),
              glow: true,
              onPressed: () =>
                  ref.read(appStateProvider).chooseLanguage(_selected),
            ),
          ],
        ),
      ),
    ),
  );
}

class LanguageCard extends StatelessWidget {
  const LanguageCard({
    required this.native,
    required this.english,
    required this.available,
    required this.selected,
    required this.onTap,
    super.key,
  });

  final String native;
  final String english;
  final bool available;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Opacity(
    opacity: available ? 1 : 0.5,
    child: Material(
      color: DeyTokens.darkSurface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
        side: BorderSide(
          color: selected ? DeyTokens.primary : DeyTokens.darkBorder,
        ),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
        onTap: available ? onTap : null,
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      native,
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    Meta(available ? english : '$english · Coming soon'),
                  ],
                ),
              ),
              Icon(
                selected ? Icons.radio_button_checked : Icons.radio_button_off,
                color: selected
                    ? DeyTokens.primary
                    : DeyTokens.darkSecondaryText,
              ),
            ],
          ),
        ),
      ),
    ),
  );
}

class WelcomeScreen extends ConsumerStatefulWidget {
  const WelcomeScreen({super.key});

  @override
  ConsumerState<WelcomeScreen> createState() => _WelcomeScreenState();
}

class _WelcomeScreenState extends ConsumerState<WelcomeScreen> {
  final _controller = PageController();
  int _page = 0;

  static const _slides = [
    (
      Icons.visibility_outlined,
      'Report what you see',
      'Tell people nearby what is happening on your street.',
    ),
    (
      Icons.notifications_active_outlined,
      'Stay alert near you',
      'Get alerts when people nearby or a verifier confirm a report.',
    ),
    (
      Icons.shield_outlined,
      'Protect your community',
      'Confirm what you can safely see, and flag what is wrong.',
    ),
  ];

  void _finish() => ref.read(appStateProvider).finishOnboarding();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final last = _page == _slides.length - 1;
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            children: [
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(onPressed: _finish, child: Text(t('Skip'))),
              ),
              Expanded(
                child: PageView.builder(
                  controller: _controller,
                  itemCount: _slides.length,
                  onPageChanged: (page) => setState(() => _page = page),
                  itemBuilder: (_, index) {
                    final (icon, title, body) = _slides[index];
                    return Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(icon, size: 96, color: DeyTokens.primary),
                        const SizedBox(height: 32),
                        Text(
                          t(title),
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            fontSize: 26,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                        const SizedBox(height: 10),
                        Text(
                          t(body),
                          textAlign: TextAlign.center,
                          style: const TextStyle(
                            color: DeyTokens.darkSecondaryText,
                          ),
                        ),
                      ],
                    );
                  },
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  for (var i = 0; i < _slides.length; i++)
                    Container(
                      width: i == _page ? 22 : 8,
                      height: 8,
                      margin: const EdgeInsets.all(4),
                      decoration: BoxDecoration(
                        color: i == _page
                            ? DeyTokens.primary
                            : DeyTokens.darkBorder,
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: PillButton(
                  last ? t('Get Started') : t('Next'),
                  kind: last ? PillKind.primary : PillKind.secondary,
                  glow: last,
                  onPressed: last
                      ? _finish
                      : () => _controller.nextPage(
                          duration: const Duration(milliseconds: 250),
                          curve: Curves.easeOut,
                        ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _PermissionScaffold extends StatelessWidget {
  const _PermissionScaffold({
    required this.icon,
    required this.title,
    required this.points,
    required this.allowLabel,
    required this.onAllow,
    required this.onSkip,
    this.denied,
  });

  final IconData icon;
  final String title;
  final List<(String, String)> points;
  final String allowLabel;
  final VoidCallback onAllow;
  final VoidCallback onSkip;
  final Widget? denied;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const SizedBox(height: 24),
          Icon(icon, size: 64, color: DeyTokens.primary),
          const SizedBox(height: 20),
          Heading(title),
          const SizedBox(height: 20),
          for (final (heading, body) in points)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: DeyCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      heading,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 4),
                    Meta(body),
                  ],
                ),
              ),
            ),
          const SizedBox(height: 12),
          PillButton(allowLabel, glow: true, onPressed: onAllow),
          TextButton(onPressed: onSkip, child: const Text('Not now')),
          ?denied,
        ],
      ),
    ),
  );
}

Widget _deniedCard(String message) => DeyCard(
  child: Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Meta(message),
      const SizedBox(height: 12),
      const PillButton(
        'Open settings',
        kind: PillKind.secondary,
        onPressed: openAppSettings,
      ),
    ],
  ),
);

class LocationPermissionScreen extends ConsumerStatefulWidget {
  const LocationPermissionScreen({super.key});

  @override
  ConsumerState<LocationPermissionScreen> createState() =>
      _LocationPermissionScreenState();
}

class _LocationPermissionScreenState
    extends ConsumerState<LocationPermissionScreen> {
  bool _denied = false;

  Future<void> _allow() async {
    final result = await ref
        .read(repositoryProvider)
        .location
        .requestPermission();
    if (!mounted) return;
    if (result == LocationPermission.deniedForever ||
        result == LocationPermission.denied) {
      setState(() => _denied = true);
    } else {
      context.go('/permissions/notifications');
    }
  }

  @override
  Widget build(BuildContext context) => _PermissionScaffold(
    icon: Icons.near_me_outlined,
    title: 'Enable location',
    points: const [
      ('Report where it happened', 'Mark where it happened so people nearby can check it.'),
      ('Alerts near you', 'Get alerts for incidents within your chosen radius.'),
      ('When it is used', 'Used while the app is open, and during SOS or a check-in.'),
    ],
    allowLabel: 'Allow location',
    onAllow: _allow,
    onSkip: () => context.go('/permissions/notifications'),
    denied: _denied
        ? _deniedCard(
            'Turn on location in your phone settings to report and get nearby alerts.',
          )
        : null,
  );
}

class NotificationPermissionScreen extends ConsumerStatefulWidget {
  const NotificationPermissionScreen({super.key});

  @override
  ConsumerState<NotificationPermissionScreen> createState() =>
      _NotificationPermissionScreenState();
}

class _NotificationPermissionScreenState
    extends ConsumerState<NotificationPermissionScreen> {
  bool _denied = false;

  Future<void> _allow() async {
    final status = await Permission.notification.request();
    await ref.read(pushProvider).requestPermission();
    if (!mounted) return;
    if (status.isGranted) {
      context.go('/permissions/battery');
    } else {
      setState(() => _denied = true);
    }
  }

  @override
  Widget build(BuildContext context) => _PermissionScaffold(
    icon: Icons.notifications_outlined,
    title: 'Enable notifications',
    points: const [
      ('Alerts need this', 'Alerts only reach you with notifications on.'),
      ('Only what matters', 'You get alerts once a report is corroborated or confirmed.'),
    ],
    allowLabel: 'Allow notifications',
    onAllow: _allow,
    onSkip: () => context.go('/permissions/battery'),
    denied: _denied
        ? _deniedCard('Turn on notifications in your phone settings to get alerts.')
        : null,
  );
}

class BatteryGuideScreen extends StatefulWidget {
  const BatteryGuideScreen({super.key});

  @override
  State<BatteryGuideScreen> createState() => _BatteryGuideScreenState();
}

class _BatteryGuideScreenState extends State<BatteryGuideScreen> {
  static const _brands = {
    'Tecno': 'Phone Master',
    'Infinix': 'Phone Master',
    'itel': 'Phone Master',
    'Samsung': 'Device care',
    'Xiaomi': 'Security',
  };
  String _brand = 'Tecno';

  @override
  Widget build(BuildContext context) {
    final steps = [
      'Open Settings, then Apps, then Dey Alert.',
      'Open Battery and choose No restrictions or Don\'t optimise.',
      'Turn on Auto-start if your phone shows it. You may find it in ${_brands[_brand]}.',
      'Open your recent apps and lock Dey Alert so it stays running.',
    ];
    return DeyPage(
      title: 'Keep alerts active',
      bottom: PillButton(
        "I've done this",
        glow: true,
        onPressed: () => context.go('/profile-setup'),
      ),
      children: [
        const Meta('Battery saving can stop alerts when your screen is locked.'),
        const SizedBox(height: 16),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final brand in _brands.keys)
              ChoiceChip(
                label: Text(brand),
                selected: _brand == brand,
                onSelected: (_) => setState(() => _brand = brand),
              ),
          ],
        ),
        const SizedBox(height: 16),
        for (var i = 0; i < steps.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: DeyCard(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  CircleAvatar(
                    radius: 14,
                    backgroundColor: DeyTokens.darkBorder,
                    child: Text('${i + 1}'),
                  ),
                  const SizedBox(width: 12),
                  Expanded(child: Text(steps[i])),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
