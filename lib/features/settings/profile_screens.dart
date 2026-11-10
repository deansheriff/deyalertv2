import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/i18n.dart';
import '../../core/services/location_service.dart';
import '../../core/services/repository.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';
import '../onboarding/onboarding_screens.dart';
import '../report/report_screens.dart';

class ProfileSetupScreen extends ConsumerStatefulWidget {
  const ProfileSetupScreen({super.key});

  @override
  ConsumerState<ProfileSetupScreen> createState() => _ProfileSetupScreenState();
}

class _ProfileSetupScreenState extends ConsumerState<ProfileSetupScreen> {
  final _name = TextEditingController();
  LatLngPoint? _home;
  double _radius = 5;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_useGps());
  }

  Future<void> _useGps() async {
    final point = await ref.read(repositoryProvider).location.current();
    if (point != null && mounted) setState(() => _home = point);
  }

  Future<void> _pick() async {
    final picked = await context.push<PickedLocation>(
      '/report/location',
      extra: _home,
    );
    if (picked != null) setState(() => _home = picked.point);
  }

  Future<void> _save() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final repository = ref.read(repositoryProvider);
    try {
      await repository.updateProfile({
        'display_name': ?(_name.text.trim().isEmpty ? null : _name.text.trim()),
        'alert_radius_km': _radius.roundToDouble(),
        'language': currentLanguage,
        'home_lat': _home!.lat,
        'home_lng': _home!.lng,
      });
      await repository.registerDevice(
        fcmToken: await ref.read(pushProvider).token(),
        point: _home,
      );
      await ref.read(appStateProvider).finishProfile();
    } on ApiException catch (error) {
      setState(() {
        _busy = false;
        _error = error.message;
      });
    }
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    bottom: PillButton(
      'Save',
      glow: true,
      busy: _busy,
      onPressed: _home == null ? null : _save,
    ),
    children: [
      const Heading(
        'Set up your profile',
        subtitle: 'Choose the area you want alerts for.',
      ),
      const SizedBox(height: 20),
      TextField(
        controller: _name,
        decoration: const InputDecoration(labelText: 'Name (optional)'),
      ),
      const SizedBox(height: 6),
      const Meta('Not shown on your reports.'),
      const SizedBox(height: 14),
      DeyCard(
        onTap: _pick,
        child: Row(
          children: [
            const Icon(Icons.home_outlined, color: DeyTokens.primary),
            const SizedBox(width: 12),
            Expanded(child: Text(_home == null ? 'Choose home area' : 'Home area set')),
            const Text('Change', style: TextStyle(color: DeyTokens.primary)),
          ],
        ),
      ),
      const SizedBox(height: 18),
      Meta('Alert radius: ${_radius.round()} km'),
      Slider(
        min: 1,
        max: 20,
        divisions: 19,
        value: _radius,
        onChanged: (value) => setState(() => _radius = value),
      ),
      const Meta('You will get alerts for corroborated and confirmed incidents inside this radius.'),
      const SizedBox(height: 8),
      const Meta('Your home area is saved to your account to send you nearby alerts. Other users cannot see it.'),
      if (_error != null) ...[
        const SizedBox(height: 8),
        Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
      ],
    ],
  );
}

class _Row extends StatelessWidget {
  const _Row(this.title, {this.subtitle, this.onTap, this.trailing});

  final String title;
  final String? subtitle;
  final VoidCallback? onTap;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: DeyCard(
      onTap: onTap,
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
                if (subtitle != null) Meta(subtitle!),
              ],
            ),
          ),
          trailing ?? const Icon(Icons.chevron_right, color: DeyTokens.darkSecondaryText),
        ],
      ),
    ),
  );
}

class ProfileTab extends ConsumerWidget {
  const ProfileTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(profileProvider).asData?.value;
    return SafeArea(
      bottom: false,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 112),
        children: [
          Heading(profile?.displayName ?? t('Profile'), subtitle: profile?.phone),
          const SizedBox(height: 18),
          _Row(
            'Alert radius',
            subtitle: profile == null ? null : '${profile.alertRadiusKm.round()} km',
            onTap: () => context.push('/settings'),
          ),
          _Row('My reports', onTap: () => context.push('/my-reports')),
          _Row('Notifications', onTap: () => context.push('/notifications')),
          _Row(t('Settings'), onTap: () => context.push('/settings')),
          const SizedBox(height: 8),
          PillButton(
            t('Sign out'),
            kind: PillKind.danger,
            onPressed: () => ref.read(repositoryProvider).signOut(),
          ),
        ],
      ),
    );
  }
}

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  Future<void> _update(WidgetRef ref, BuildContext context, Map<String, dynamic> values) async {
    try {
      await ref.read(repositoryProvider).updateProfile(values);
      ref.invalidate(profileProvider);
    } on ApiException catch (error) {
      if (context.mounted) showMessage(context, error.message);
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(profileProvider).asData?.value;
    final dataSaver = ref.watch(appStateProvider).dataSaver;
    return DeyPage(
      title: t('Settings'),
      children: [
        if (profile != null) ...[
          DeyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Alert radius: ${profile.alertRadiusKm.round()} km'),
                Slider(
                  min: 1,
                  max: 20,
                  divisions: 19,
                  value: profile.alertRadiusKm,
                  onChanged: (_) {},
                  onChangeEnd: (value) =>
                      _update(ref, context, {'alert_radius_km': value.roundToDouble()}),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          _Row(
            'SMS alerts',
            subtitle: 'Opt in to SMS for confirmed critical incidents',
            trailing: Switch(
              value: profile.smsAlertsOptIn,
              onChanged: (value) => _update(ref, context, {'sms_alerts_opt_in': value}),
            ),
          ),
          _Row(
            'Quiet hours',
            subtitle: profile.quietStart == null
                ? 'Off'
                : '${profile.quietStart!.substring(0, 5)} to ${profile.quietEnd!.substring(0, 5)}',
            onTap: () => context.push('/settings/quiet'),
          ),
        ],
        _Row('Language', subtitle: currentLanguage == 'pcm' ? 'Pidgin' : 'English', onTap: () => context.push('/settings/language')),
        _Row(
          'Data saver',
          subtitle: 'Thumbnails only',
          trailing: Switch(
            value: dataSaver,
            onChanged: (value) => ref.read(appStateProvider).setDataSaver(value),
          ),
        ),
        const _Row(
          'Background location',
          subtitle: 'Used in the background only during SOS or a check-in.',
          trailing: SizedBox.shrink(),
        ),
        _Row('Keep alerts active', subtitle: 'Battery settings', onTap: () => context.push('/permissions/battery')),
        _Row('Your data', onTap: () => context.push('/settings/account')),
        _Row('Terms and privacy', onTap: () => context.push('/legal')),
      ],
    );
  }
}

class LanguageSettingsScreen extends ConsumerStatefulWidget {
  const LanguageSettingsScreen({super.key});

  @override
  ConsumerState<LanguageSettingsScreen> createState() => _LanguageSettingsScreenState();
}

class _LanguageSettingsScreenState extends ConsumerState<LanguageSettingsScreen> {
  @override
  Widget build(BuildContext context) => DeyPage(
    title: 'Language',
    children: [
      for (final (code, native, english) in const [
        ('en', 'English', 'English'),
        ('pcm', 'Naijá', 'Nigerian Pidgin'),
        ('ha', 'Hausa', 'Hausa'),
        ('yo', 'Yorùbá', 'Yoruba'),
        ('ig', 'Asụsụ Igbo', 'Igbo'),
      ])
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: LanguageCard(
            native: native,
            english: english,
            available: launchLanguages.contains(code),
            selected: currentLanguage == code,
            onTap: () async {
              await ref.read(appStateProvider).chooseLanguage(code);
              try {
                await ref.read(repositoryProvider).updateProfile({'language': code});
              } on ApiException {
                // The local choice still applies.
              }
              setState(() {});
            },
          ),
        ),
    ],
  );
}

class QuietHoursScreen extends ConsumerStatefulWidget {
  const QuietHoursScreen({super.key});

  @override
  ConsumerState<QuietHoursScreen> createState() => _QuietHoursScreenState();
}

class _QuietHoursScreenState extends ConsumerState<QuietHoursScreen> {
  TimeOfDay _start = const TimeOfDay(hour: 22, minute: 0);
  TimeOfDay _end = const TimeOfDay(hour: 6, minute: 0);
  bool _busy = false;

  String _format(TimeOfDay time) =>
      '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}:00';

  Future<void> _submit(Map<String, dynamic> values) async {
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).updateProfile(values);
      ref.invalidate(profileProvider);
      if (mounted) context.pop();
    } on ApiException catch (error) {
      if (mounted) {
        showMessage(context, error.message);
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _choose(bool start) async {
    final picked = await showTimePicker(context: context, initialTime: start ? _start : _end);
    if (picked != null) setState(() => start ? _start = picked : _end = picked);
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    title: 'Quiet hours',
    bottom: PillButton(
      'Save',
      glow: true,
      busy: _busy,
      onPressed: () => _submit({
        'quiet_hours_start': _format(_start),
        'quiet_hours_end': _format(_end),
      }),
    ),
    children: [
      _Row('Starts', subtitle: _start.format(context), onTap: () => _choose(true)),
      _Row('Ends', subtitle: _end.format(context), onTap: () => _choose(false)),
      const Meta('Confirmed critical alerts still come through.'),
      TextButton(
        onPressed: _busy ? null : () => _submit({'clear_quiet_hours': true}),
        child: const Text('Turn off quiet hours'),
      ),
    ],
  );
}

class AccountDataScreen extends ConsumerStatefulWidget {
  const AccountDataScreen({super.key});

  @override
  ConsumerState<AccountDataScreen> createState() => _AccountDataScreenState();
}

class _AccountDataScreenState extends ConsumerState<AccountDataScreen> {
  bool _understood = false;
  bool _busy = false;

  Future<void> _copyData() async {
    final data = await ref.read(repositoryProvider).exportData();
    await Clipboard.setData(ClipboardData(text: jsonEncode(data)));
    if (mounted) showMessage(context, 'Your data was copied.');
  }

  Future<void> _delete() async {
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).deleteAccount();
    } on ApiException catch (error) {
      if (mounted) {
        showMessage(context, error.message);
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    title: 'Your data',
    children: [
      DeyCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Download my data', style: TextStyle(fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            const Meta('Copies your profile, contacts, and reports.'),
            const SizedBox(height: 12),
            PillButton('Copy my data', kind: PillKind.secondary, onPressed: _copyData),
          ],
        ),
      ),
      const SizedBox(height: 10),
      DeyCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Delete my account', style: TextStyle(fontWeight: FontWeight.w600, color: DeyTokens.danger)),
            const SizedBox(height: 4),
            const Meta('This removes your profile, devices, and trusted contacts. Your reports stay but are no longer linked to you. This cannot be undone.'),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: _understood,
              onChanged: (value) => setState(() => _understood = value ?? false),
              title: const Text('I understand'),
            ),
            PillButton(
              'Permanently delete account',
              kind: PillKind.danger,
              busy: _busy,
              onPressed: _understood ? _delete : null,
            ),
          ],
        ),
      ),
    ],
  );
}

class MyReportsScreen extends ConsumerWidget {
  const MyReportsScreen({super.key});

  static const _outcomes = {
    'unconfirmed': ('Unverified', 'Waiting for people nearby or a verifier.'),
    'corroborated': ('Corroborated', 'People nearby confirmed this.'),
    'confirmed': ('Confirmed', 'A verifier confirmed this.'),
    'resolved': ('Resolved', 'This has been marked as over.'),
    'expired': ('Expired', 'The alert period ended.'),
    'under_review': ('Under review', 'A moderator is checking this.'),
    'false_report': ('Marked false', 'A verifier or moderator marked this as false.'),
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) => DeyPage(
    title: 'My reports',
    children: [
      FutureBuilder(
        future: ref.read(repositoryProvider).myReports(),
        builder: (_, snapshot) {
          if (!snapshot.hasData) return const LinearProgressIndicator();
          if (snapshot.data!.isEmpty) {
            return const CenterMessage(
              icon: Icons.description_outlined,
              title: 'No reports yet',
              body: 'Reports you file will appear here.',
            );
          }
          return Column(
            children: [
              for (final report in snapshot.data!)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Builder(
                    builder: (context) {
                      final (label, explanation) =
                          _outcomes[report['status']] ?? ('Unknown', '');
                      return DeyCard(
                        onTap: () => context.push('/incident/${report['id']}'),
                        child: Row(
                          children: [
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    incidentType(report['type_code'] as String).label,
                                    style: const TextStyle(fontWeight: FontWeight.w600),
                                  ),
                                  Meta(explanation),
                                ],
                              ),
                            ),
                            Chip(label: Text(label)),
                          ],
                        ),
                      );
                    },
                  ),
                ),
            ],
          );
        },
      ),
    ],
  );
}

class NotificationsScreen extends ConsumerStatefulWidget {
  const NotificationsScreen({super.key});

  @override
  ConsumerState<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends ConsumerState<NotificationsScreen> {
  late final _items = ref.read(repositoryProvider).notifications();

  @override
  Widget build(BuildContext context) => DeyPage(
    title: 'Notifications',
    children: [
      FutureBuilder(
        future: _items,
        builder: (_, snapshot) {
          if (snapshot.hasError) {
            return const CenterMessage(
              icon: Icons.cloud_off_outlined,
              title: "You're offline",
              body: 'Notifications will load when you are back online.',
            );
          }
          if (!snapshot.hasData) return const LinearProgressIndicator();
          if (snapshot.data!.isEmpty) {
            return const CenterMessage(
              icon: Icons.notifications_none,
              title: "You're all caught up",
              body: 'Past alerts will appear here.',
            );
          }
          return Column(
            children: [
              for (final item in snapshot.data!)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: DeyCard(
                    onTap: () {
                      unawaited(ref.read(repositoryProvider).markRead(item['id'] as String));
                      final incident = item['incident_id'];
                      if (incident != null) unawaited(context.push('/incident/$incident'));
                    },
                    child: Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                item['title'] as String,
                                style: TextStyle(
                                  fontWeight: item['read_at'] == null ? FontWeight.w700 : FontWeight.w400,
                                ),
                              ),
                              if (item['body'] != null) Meta(item['body'] as String),
                              Meta(timeAgo(DateTime.parse(item['created_at'] as String))),
                            ],
                          ),
                        ),
                        if (item['read_at'] == null)
                          const Icon(Icons.circle, size: 8, color: DeyTokens.accent),
                      ],
                    ),
                  ),
                ),
            ],
          );
        },
      ),
    ],
  );
}

class LegalScreen extends StatelessWidget {
  const LegalScreen({super.key});

  @override
  Widget build(BuildContext context) => const DeyPage(
    title: 'Terms and privacy',
    children: [
      CenterMessage(
        icon: Icons.gavel_outlined,
        title: 'Coming before launch',
        body: 'Final terms and privacy text will come from legal counsel.',
      ),
    ],
  );
}
