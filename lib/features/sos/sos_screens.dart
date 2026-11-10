import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/i18n.dart';
import '../../core/services/location_service.dart';
import '../../core/services/repository.dart';
import '../../core/services/sms_composer.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';
import '../auth/auth_screens.dart';

class HoldToActivate extends StatefulWidget {
  const HoldToActivate({
    required this.onComplete,
    this.duration = const Duration(seconds: 3),
    super.key,
  });

  final VoidCallback onComplete;
  final Duration duration;

  @override
  State<HoldToActivate> createState() => _HoldToActivateState();
}

class _HoldToActivateState extends State<HoldToActivate>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: widget.duration,
  )..addStatusListener(_status);

  void _status(AnimationStatus status) {
    if (status == AnimationStatus.completed) {
      unawaited(HapticFeedback.heavyImpact());
      widget.onComplete();
      _controller.reset();
    }
  }

  void _start(_) {
    unawaited(HapticFeedback.mediumImpact());
    unawaited(_controller.forward(from: 0));
  }

  void _cancel() {
    if (_controller.isAnimating) _controller.reset();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    label: 'SOS. Hold for 3 seconds.',
    child: GestureDetector(
      onTapDown: _start,
      onTapUp: (_) => _cancel(),
      onTapCancel: _cancel,
      child: SizedBox.square(
        dimension: 230,
        child: Stack(
          alignment: Alignment.center,
          children: [
            AnimatedBuilder(
              animation: _controller,
              builder: (_, _) => SizedBox.expand(
                child: CircularProgressIndicator(
                  value: _controller.value,
                  strokeWidth: 8,
                  color: DeyTokens.danger,
                  backgroundColor: DeyTokens.darkBorder,
                ),
              ),
            ),
            Container(
              width: 186,
              height: 186,
              decoration: const BoxDecoration(
                color: DeyTokens.danger,
                shape: BoxShape.circle,
              ),
              child: const Center(
                child: Text(
                  'SOS',
                  style: TextStyle(
                    color: Colors.white,
                    fontSize: 44,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class SosTab extends ConsumerWidget {
  const SosTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final contacts = ref.watch(contactsProvider);
    final accepted = (contacts.asData?.value ??
            ref.read(repositoryProvider).cachedContacts())
        .where((contact) => contact.accepted)
        .toList();
    final none = accepted.isEmpty;
    return SafeArea(
      bottom: false,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 112),
        children: [
          const Text(
            'Emergency SOS',
            style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 14),
          DeyCard(
            onTap: () => context.push('/contacts'),
            child: Row(
              children: [
                const Icon(Icons.groups_outlined),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    none ? 'No trusted contacts yet' : '${accepted.length} contacts',
                  ),
                ),
                Text(
                  none ? 'Add' : 'Manage',
                  style: const TextStyle(color: DeyTokens.primary),
                ),
              ],
            ),
          ),
          const SizedBox(height: 10),
          PillButton(
            t('Call 112'),
            kind: PillKind.danger,
            icon: Icons.call_outlined,
            onPressed: call112,
          ),
          const SizedBox(height: 10),
          PillButton(
            'Tell me when I\'m home safe',
            kind: PillKind.secondary,
            icon: Icons.timer_outlined,
            onPressed: () => context.push('/checkin'),
          ),
          const SizedBox(height: 28),
          if (none) ...[
            const Center(
              child: Text(
                'Add a contact so SOS can reach someone',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
              ),
            ),
            const SizedBox(height: 8),
            const Center(
              child: Meta(
                'SOS opens your SMS app with a message ready to send to your contacts. Uses airtime. Works without internet.',
              ),
            ),
            const SizedBox(height: 16),
            PillButton(
              'Add contact',
              glow: true,
              onPressed: () => context.push('/contacts/edit'),
            ),
          ] else ...[
            Center(
              child: HoldToActivate(
                onComplete: () => context.push('/sos/countdown'),
              ),
            ),
            const SizedBox(height: 16),
            Center(
              child: Text(
                t('Hold for 3 seconds'),
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            const SizedBox(height: 6),
            const Center(
              child: Meta(
                'Opens your SMS app after 10 seconds. Uses airtime. Works without internet.',
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class SosCountdownScreen extends ConsumerStatefulWidget {
  const SosCountdownScreen({this.seconds = 10, super.key});

  final int seconds;

  @override
  ConsumerState<SosCountdownScreen> createState() => _SosCountdownScreenState();
}

class _SosCountdownScreenState extends ConsumerState<SosCountdownScreen> {
  late int _remaining = widget.seconds;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_remaining <= 1) {
        timer.cancel();
        unawaited(_send());
      } else {
        setState(() => _remaining--);
      }
    });
  }

  Future<void> _send() async {
    final repository = ref.read(repositoryProvider);
    final phones = repository
        .cachedContacts()
        .where((contact) => contact.accepted)
        .map((contact) => contact.phone)
        .toList();
    final point = await repository.location.current(
      timeout: const Duration(seconds: 3),
    );
    await openSmsComposer(
      phones,
      sosMessage(lat: point?.lat, lng: point?.lng),
    );
    if (!mounted) return;
    final state = ref.read(appStateProvider);
    final sosId = point == null ? null : await repository.startSos(point);
    if (!mounted) return;
    if (sosId == null) {
      context.go('/sos');
    } else if (state.backgroundDisclosureAccepted) {
      context.go('/sos/live', extra: sosId);
    } else {
      context.go('/sos/disclosure', extra: sosId);
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Spacer(),
            Text(
              '$_remaining',
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 120,
                fontWeight: FontWeight.w800,
                color: DeyTokens.danger,
                fontFeatures: [FontFeature.tabularFigures()],
              ),
            ),
            const Text(
              'Opening your SMS app',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 8),
            const Text(
              'A message will be ready for you to send. Nothing is sent silently.',
              textAlign: TextAlign.center,
              style: TextStyle(color: DeyTokens.darkSecondaryText),
            ),
            const Spacer(),
            PillButton(
              t('Cancel'),
              kind: PillKind.inverse,
              icon: Icons.close,
              onPressed: () {
                _timer?.cancel();
                context.pop();
              },
            ),
          ],
        ),
      ),
    ),
  );
}

class BackgroundDisclosureScreen extends ConsumerWidget {
  const BackgroundDisclosureScreen({required this.sosId, super.key});

  final String sosId;

  @override
  Widget build(BuildContext context, WidgetRef ref) => DeyPage(
    bottom: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PillButton(
          'Agree',
          glow: true,
          onPressed: () async {
            await ref.read(appStateProvider).acceptBackgroundDisclosure();
            if (context.mounted) context.go('/sos/live', extra: sosId);
          },
        ),
        const SizedBox(height: 8),
        PillButton(
          'No thanks',
          kind: PillKind.secondary,
          onPressed: () => context.go('/sos/live', extra: sosId),
        ),
      ],
    ),
    children: [
      const Heading(
        'How Dey Alert uses your background location',
        subtitle:
            'Dey Alert uses your location in the background only during an active SOS or a check-in, even when the app is closed or not in use.',
      ),
      const SizedBox(height: 16),
      for (final (title, body) in const [
        ('Who receives it', 'Your location goes only to your trusted contacts and the verifier for your area. It is never sold or shared with advertisers.'),
        ('Stops when you are safe', 'Background location stops when you end SOS or finish a check-in. Location points are deleted within 24 hours after your SOS ends.'),
        ('Your choice', 'Without this, location sharing stops when your screen turns off.'),
      ])
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: DeyCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                Meta(body),
              ],
            ),
          ),
        ),
    ],
  );
}

class SosLiveScreen extends ConsumerStatefulWidget {
  const SosLiveScreen({required this.sosId, super.key});

  final String sosId;

  @override
  ConsumerState<SosLiveScreen> createState() => _SosLiveScreenState();
}

class _SosLiveScreenState extends ConsumerState<SosLiveScreen> {
  StreamSubscription<LatLngPoint>? _sharing;
  DateTime? _lastUpdate;

  @override
  void initState() {
    super.initState();
    final repository = ref.read(repositoryProvider);
    _sharing = repository.location.sharingStream().listen((point) async {
      try {
        await repository.pingSos(widget.sosId, point);
        if (mounted) setState(() => _lastUpdate = DateTime.now());
      } on ApiException {
        // The next position retries; SMS already went out from the composer.
      }
    });
  }

  Future<void> _end(String action, String route) async {
    await _sharing?.cancel();
    try {
      await ref.read(repositoryProvider).endSos(widget.sosId, action);
    } on ApiException {
      if (mounted) showMessage(context, 'Could not reach Dey Alert. Tell your contacts directly.');
    }
    if (mounted) context.go(route);
  }

  @override
  void dispose() {
    unawaited(_sharing?.cancel());
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final count = ref
        .watch(contactsProvider)
        .asData
        ?.value
        .where((contact) => contact.accepted)
        .length;
    return PopScope(
      canPop: false,
      child: Scaffold(
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Spacer(),
                const Icon(Icons.sensors, size: 88, color: DeyTokens.danger),
                const SizedBox(height: 16),
                Text(
                  'Sharing your location with ${count ?? 'your'} contacts',
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 6),
                Center(
                  child: Meta(
                    _lastUpdate == null ? 'Starting...' : 'Updated ${timeAgo(_lastUpdate!)}',
                  ),
                ),
                const Spacer(),
                PillButton(
                  t("I'm safe"),
                  glow: true,
                  onPressed: () => _end('resolve', '/sos/resolved'),
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: PillButton(
                        t('Call 112'),
                        kind: PillKind.danger,
                        onPressed: call112,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: PillButton(
                        'Cancel SOS',
                        kind: PillKind.secondary,
                        onPressed: () => _end('cancel', '/sos'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class SosResolvedScreen extends StatelessWidget {
  const SosResolvedScreen({super.key});

  @override
  Widget build(BuildContext context) => DeyPage(
    bottom: PillButton('Return home', glow: true, onPressed: () => context.go('/map')),
    children: const [
      SizedBox(height: 40),
      Icon(Icons.check_circle_outline, size: 72, color: DeyTokens.primary),
      SizedBox(height: 16),
      Heading(
        'You are marked safe',
        subtitle: 'Your contacts will get a text saying you are safe.',
      ),
    ],
  );
}

class ContactsScreen extends ConsumerWidget {
  const ContactsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final contacts = ref.watch(contactsProvider);
    return DeyPage(
      title: 'Trusted contacts',
      bottom: PillButton(
        'Add contact',
        glow: true,
        icon: Icons.add,
        onPressed: () => context.push('/contacts/edit'),
      ),
      children: [
        const Meta('SOS Lite opens a text to contacts who have accepted.'),
        const SizedBox(height: 12),
        ...contacts.when(
          loading: () => const [LinearProgressIndicator()],
          error: (error, _) => [Meta(error.toString())],
          data: (list) => list.isEmpty
              ? const [
                  CenterMessage(
                    icon: Icons.group_add_outlined,
                    title: 'No contacts yet',
                    body: 'Add someone you trust. They will be asked to agree first.',
                  ),
                ]
              : [
                  for (final contact in list)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: Dismissible(
                        key: ValueKey(contact.id),
                        direction: DismissDirection.endToStart,
                        background: Container(
                          alignment: Alignment.centerRight,
                          padding: const EdgeInsets.only(right: 20),
                          child: const Icon(Icons.delete_outline, color: DeyTokens.danger),
                        ),
                        confirmDismiss: (_) async {
                          try {
                            await ref.read(repositoryProvider).deleteContact(contact.id);
                            ref.invalidate(contactsProvider);
                            return true;
                          } on ApiException catch (error) {
                            if (context.mounted) showMessage(context, error.message);
                            return false;
                          }
                        },
                        child: DeyCard(
                          onTap: () => context.push('/contacts/edit', extra: contact),
                          child: Row(
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(contact.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                                    Meta(
                                      [contact.phone, if (contact.relationship != null) contact.relationship!].join(' · '),
                                    ),
                                  ],
                                ),
                              ),
                              Chip(
                                label: Text(switch (contact.consentStatus) {
                                  'accepted' => 'Accepted',
                                  'declined' => 'Declined',
                                  'revoked' => 'Withdrawn',
                                  _ => 'Pending',
                                }),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
        ),
      ],
    );
  }
}

class ContactEditScreen extends ConsumerStatefulWidget {
  const ContactEditScreen({this.contact, super.key});

  final TrustedContact? contact;

  @override
  ConsumerState<ContactEditScreen> createState() => _ContactEditScreenState();
}

class _ContactEditScreenState extends ConsumerState<ContactEditScreen> {
  late final _name = TextEditingController(text: widget.contact?.name);
  late final _phone = TextEditingController(text: widget.contact?.phone);
  late String? _relationship = widget.contact?.relationship;
  bool _busy = false;
  String? _error;

  bool get _editing => widget.contact != null;

  Future<void> _save() async {
    final phone = normalizeNigerianPhone(_phone.text);
    if (_name.text.trim().isEmpty || phone == null) {
      setState(() => _error = 'Enter a name and a valid Nigerian number.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final repository = ref.read(repositoryProvider);
    try {
      if (_editing) {
        await repository.updateContact(widget.contact!.id, _name.text.trim(), _relationship);
      } else {
        await repository.addContact(_name.text.trim(), phone, _relationship);
      }
      ref.invalidate(contactsProvider);
      if (mounted) context.pop();
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
    _phone.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    title: _editing ? 'Edit contact' : 'Add contact',
    bottom: PillButton('Save', glow: true, busy: _busy, onPressed: _save),
    children: [
      TextField(controller: _name, decoration: const InputDecoration(labelText: 'Name')),
      const SizedBox(height: 12),
      TextField(
        controller: _phone,
        enabled: !_editing,
        keyboardType: TextInputType.phone,
        decoration: const InputDecoration(labelText: 'Phone number'),
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 6,
        children: [
          for (final option in const ['Family', 'Friend', 'Neighbour', 'Other'])
            ChoiceChip(
              label: Text(option),
              selected: _relationship == option,
              onSelected: (_) => setState(() => _relationship = option),
            ),
        ],
      ),
      const SizedBox(height: 14),
      const Meta('They will get a one-time text asking permission before they can receive SOS messages.'),
      if (_error != null) ...[
        const SizedBox(height: 8),
        Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
      ],
    ],
  );
}

class CheckinSetupScreen extends ConsumerStatefulWidget {
  const CheckinSetupScreen({super.key});

  @override
  ConsumerState<CheckinSetupScreen> createState() => _CheckinSetupScreenState();
}

class _CheckinSetupScreenState extends ConsumerState<CheckinSetupScreen> {
  int _minutes = 30;
  bool _busy = false;

  Future<void> _start() async {
    setState(() => _busy = true);
    final repository = ref.read(repositoryProvider);
    final point = await repository.location.current();
    if (point == null) {
      if (mounted) {
        showMessage(context, 'Turn on location to start a check-in.');
        setState(() => _busy = false);
      }
      return;
    }
    try {
      // The server fires one minute after the on-screen prompt, which is the grace period.
      final data = await repository.startCheckin(_minutes + 1, point);
      if (mounted) {
        context.go(
          '/checkin/active',
          extra: (data['id'] as String, DateTime.now().add(Duration(minutes: _minutes))),
        );
      }
    } on ApiException catch (error) {
      if (mounted) {
        showMessage(context, error.message);
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    title: 'Safe check-in',
    bottom: PillButton('Start', glow: true, busy: _busy, onPressed: _start),
    children: [
      const Heading(
        "Tell me when I'm home safe",
        subtitle: "If you don't check in on time, Dey Alert texts your trusted contacts your last location. You need a connection to start.",
      ),
      const SizedBox(height: 20),
      Wrap(
        spacing: 8,
        children: [
          for (final (label, minutes) in const [('30 min', 30), ('1 hour', 60), ('2 hours', 120), ('4 hours', 240)])
            ChoiceChip(
              label: Text(label),
              selected: _minutes == minutes,
              onSelected: (_) => setState(() => _minutes = minutes),
            ),
        ],
      ),
    ],
  );
}

class CheckinActiveScreen extends ConsumerStatefulWidget {
  const CheckinActiveScreen({required this.id, required this.dueAt, super.key});

  final String id;
  final DateTime dueAt;

  @override
  ConsumerState<CheckinActiveScreen> createState() => _CheckinActiveScreenState();
}

class _CheckinActiveScreenState extends ConsumerState<CheckinActiveScreen> {
  Timer? _timer;
  late Duration _left = widget.dueAt.difference(DateTime.now());

  bool get _overdue => _left <= Duration.zero;

  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      setState(() => _left = widget.dueAt.difference(DateTime.now()));
    });
  }

  Future<void> _safe() async {
    try {
      await ref.read(repositoryProvider).endSos(widget.id, 'checkin');
      if (mounted) context.go('/sos');
    } on ApiException catch (error) {
      if (mounted) showMessage(context, error.message);
    }
  }

  Future<void> _alertNow() async {
    final repository = ref.read(repositoryProvider);
    final point = await repository.location.current();
    if (point == null) {
      if (mounted) showMessage(context, 'Could not get your location.');
      return;
    }
    final sosId = await repository.startSos(point);
    await repository.endSos(widget.id, 'cancel');
    if (mounted && sosId != null) context.go('/sos/live', extra: sosId);
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  String get _clock {
    final total = _left.isNegative ? 0 : _left.inSeconds;
    final hours = total ~/ 3600;
    final minutes = (total % 3600) ~/ 60;
    final seconds = total % 60;
    String two(int n) => n.toString().padLeft(2, '0');
    return hours > 0 ? '$hours:${two(minutes)}:${two(seconds)}' : '${two(minutes)}:${two(seconds)}';
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Spacer(),
            Text(
              _overdue ? 'Are you OK?' : _clock,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 56,
                fontWeight: FontWeight.w800,
                fontFeatures: [FontFeature.tabularFigures()],
              ),
            ),
            const SizedBox(height: 10),
            Center(
              child: Meta(
                _overdue
                    ? "Your check-in time has passed. If you don't respond, your trusted contacts will get a text with your last location."
                    : 'Check in before the timer ends.',
              ),
            ),
            const Spacer(),
            PillButton(t("I'm safe"), glow: true, onPressed: _safe),
            const SizedBox(height: 10),
            PillButton(
              'Alert my contacts now',
              kind: PillKind.danger,
              onPressed: _alertNow,
            ),
          ],
        ),
      ),
    ),
  );
}
