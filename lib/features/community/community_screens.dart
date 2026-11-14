import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/i18n.dart';
import '../../core/services/location_service.dart';
import '../../core/services/repository.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';
import '../report/report_screens.dart';

const maxZones = 5;
const _zoneIcons = {
  'home': Icons.home_outlined,
  'work': Icons.work_outline,
  'school': Icons.school_outlined,
  'other': Icons.place_outlined,
};
const _zoneLabels = {'home': 'Home', 'work': 'Work', 'school': 'School', 'other': 'Other'};

Future<void> _sheet(BuildContext context, Widget child) => showModalBottomSheet<void>(
  context: context,
  isScrollControlled: true,
  backgroundColor: DeyTokens.darkSurface,
  builder: (sheetContext) => SafeArea(
    child: Padding(
      padding: EdgeInsets.fromLTRB(16, 20, 16, 16 + MediaQuery.viewInsetsOf(sheetContext).bottom),
      child: SingleChildScrollView(child: child),
    ),
  ),
);

String _message(Object error) =>
    error is ApiException ? error.message : 'Could not reach Dey Alert. Check your connection.';

// Watch zones

class WatchZonesScreen extends ConsumerWidget {
  const WatchZonesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final zones = ref.watch(zonesProvider);
    final count = zones.asData?.value.length ?? 0;
    return DeyPage(
      title: t('Watch zones'),
      bottom: PillButton(
        t('Add a zone'),
        glow: true,
        icon: Icons.add,
        onPressed: count >= maxZones || zones.isLoading
            ? null
            : () => _sheet(context, const ZoneEditor()),
      ),
      children: [
        const Heading(
          'Places you care about',
          subtitle: "Get alerts for home, work or school, even when you're somewhere else.",
        ),
        const SizedBox(height: 16),
        ...zones.when(
          loading: () => [const Center(child: CircularProgressIndicator())],
          error: (error, _) => [
            CenterMessage(
              icon: Icons.cloud_off_outlined,
              title: 'Could not load your zones',
              body: _message(error),
            ),
          ],
          data: (items) => items.isEmpty
              ? [
                  const DeyCard(
                    dashed: true,
                    child: Row(
                      children: [
                        Icon(Icons.add_location_alt_outlined, color: DeyTokens.primary),
                        SizedBox(width: 12),
                        Expanded(
                          child: Text('No zones yet. Add home first; it takes a few seconds.'),
                        ),
                      ],
                    ),
                  ),
                ]
              : [for (final zone in items) _ZoneCard(zone)],
        ),
        const SizedBox(height: 8),
        Meta('You can add up to $maxZones zones. Alerts follow the same rules as nearby alerts.'),
      ],
    );
  }
}

class _ZoneCard extends ConsumerWidget {
  const _ZoneCard(this.zone);

  final WatchZone zone;

  Future<void> _toggle(WidgetRef ref, BuildContext context, bool active) async {
    try {
      await ref.read(repositoryProvider).updateZone(zone.id, zone.toJson(active: active));
      ref.invalidate(zonesProvider);
    } on ApiException catch (error) {
      if (context.mounted) showMessage(context, error.message);
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: DeyCard(
      onTap: () => _sheet(context, ZoneEditor(zone: zone)),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: DeyTokens.primary.withValues(alpha: zone.isActive ? 0.14 : 0.05),
            ),
            child: Icon(
              _zoneIcons[zone.kind] ?? Icons.place_outlined,
              color: zone.isActive ? DeyTokens.primary : DeyTokens.darkSecondaryText,
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(zone.name, style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 16)),
                Meta(
                  '${(zone.radiusM / 1000).toStringAsFixed(zone.radiusM % 1000 == 0 ? 0 : 1)} km radius'
                  '${zone.isActive ? '' : ' · alerts off'}',
                ),
              ],
            ),
          ),
          Switch(value: zone.isActive, onChanged: (value) => _toggle(ref, context, value)),
        ],
      ),
    ),
  );
}

class ZoneEditor extends ConsumerStatefulWidget {
  const ZoneEditor({this.zone, super.key});

  final WatchZone? zone;

  @override
  ConsumerState<ZoneEditor> createState() => _ZoneEditorState();
}

class _ZoneEditorState extends ConsumerState<ZoneEditor> {
  late final _name = TextEditingController(text: widget.zone?.name ?? 'Home');
  late String _kind = widget.zone?.kind ?? 'home';
  late double _radiusKm = (widget.zone?.radiusM ?? 2000) / 1000;
  LatLngPoint? _point;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final zone = widget.zone;
    if (zone != null) _point = LatLngPoint(zone.lat, zone.lng);
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _here() async {
    final point = await ref.read(repositoryProvider).location.current();
    if (!mounted) return;
    setState(() {
      _point = point;
      _error = point == null ? 'Could not get your location. Pick it on the map.' : null;
    });
  }

  Future<void> _pick() async {
    final picked = await context.push<PickedLocation>('/report/location', extra: _point);
    if (picked != null && mounted) setState(() => _point = picked.point);
  }

  Future<void> _save() async {
    final point = _point;
    if (point == null || _name.text.trim().isEmpty) {
      setState(() => _error = 'Give the zone a name and a location.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final body = {
      'name': _name.text.trim(),
      'kind': _kind,
      'lat': point.lat,
      'lng': point.lng,
      'radius_m': (_radiusKm * 1000).round(),
      'is_active': widget.zone?.isActive ?? true,
    };
    try {
      final repository = ref.read(repositoryProvider);
      final zone = widget.zone;
      if (zone == null) {
        await repository.addZone(body);
      } else {
        await repository.updateZone(zone.id, body);
      }
      ref.invalidate(zonesProvider);
      if (mounted) Navigator.of(context).pop();
    } on Exception catch (error) {
      setState(() => _error = _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _delete() async {
    final zone = widget.zone;
    if (zone == null) return;
    await ref.read(repositoryProvider).deleteZone(zone.id);
    ref.invalidate(zonesProvider);
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Heading(widget.zone == null ? 'Add a watch zone' : 'Edit watch zone'),
      const SizedBox(height: 16),
      Wrap(
        spacing: 8,
        children: [
          for (final entry in _zoneLabels.entries)
            ChoiceChip(
              label: Text(entry.value),
              avatar: Icon(_zoneIcons[entry.key], size: 18),
              selected: _kind == entry.key,
              onSelected: (_) => setState(() {
                if (_name.text == _zoneLabels[_kind]) _name.text = entry.value;
                _kind = entry.key;
              }),
            ),
        ],
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _name,
        maxLength: 40,
        decoration: const InputDecoration(labelText: 'Name', counterText: ''),
      ),
      const SizedBox(height: 12),
      DeyCard(
        child: Row(
          children: [
            Icon(
              _point == null ? Icons.location_searching : Icons.check_circle,
              color: _point == null ? DeyTokens.darkSecondaryText : DeyTokens.primary,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                _point == null
                    ? 'No location yet'
                    : 'Location set (${_point!.lat.toStringAsFixed(4)}, ${_point!.lng.toStringAsFixed(4)})',
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 8),
      Row(
        children: [
          Expanded(
            child: PillButton(
              'Use where I am',
              kind: PillKind.secondary,
              icon: Icons.my_location,
              onPressed: _here,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: PillButton(
              'Pick on map',
              kind: PillKind.secondary,
              icon: Icons.map_outlined,
              onPressed: _pick,
            ),
          ),
        ],
      ),
      const SizedBox(height: 16),
      Text('Alert radius: ${_radiusKm.toStringAsFixed(1)} km'),
      Slider(
        value: _radiusKm,
        min: 0.5,
        max: 10,
        divisions: 19,
        label: '${_radiusKm.toStringAsFixed(1)} km',
        onChanged: (value) => setState(() => _radiusKm = value),
      ),
      if (_error != null) ...[
        Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
        const SizedBox(height: 8),
      ],
      PillButton('Save zone', glow: true, busy: _busy, onPressed: _save),
      if (widget.zone != null) ...[
        const SizedBox(height: 8),
        PillButton('Delete zone', kind: PillKind.danger, onPressed: _delete),
      ],
    ],
  );
}

// Support

const _categories = {
  'bug': 'Something is broken',
  'question': 'Question',
  'abuse': 'Report abuse',
  'idea': 'Idea',
};

class SupportScreen extends ConsumerStatefulWidget {
  const SupportScreen({super.key});

  @override
  ConsumerState<SupportScreen> createState() => _SupportScreenState();
}

class _SupportScreenState extends ConsumerState<SupportScreen> {
  final _message = TextEditingController();
  String _category = 'bug';
  bool _includeInfo = true;
  bool _busy = false;

  @override
  void dispose() {
    _message.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_message.text.trim().length < 5) {
      showMessage(context, 'Tell us a little more first.');
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(repositoryProvider)
          .openTicket(
            _category,
            _message.text.trim(),
            _includeInfo ? '${Platform.operatingSystem} ${Platform.operatingSystemVersion}' : null,
          );
      _message.clear();
      ref.invalidate(ticketsProvider);
      if (mounted) showMessage(context, 'Sent. We usually reply within a day.');
    } on Exception catch (error) {
      if (mounted) showMessage(context, _messageFor(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _messageFor(Object error) =>
      error is ApiException ? error.message : 'Could not reach Dey Alert. Check your connection.';

  @override
  Widget build(BuildContext context) {
    final tickets = ref.watch(ticketsProvider).asData?.value ?? const <SupportTicket>[];
    return DeyPage(
      title: t('Contact support'),
      bottom: PillButton(t('Send message'), glow: true, busy: _busy, onPressed: _send),
      children: [
        const Heading(
          'How can we help?',
          subtitle:
              'Tell us what went wrong or what we can do better. We usually reply within a day.',
        ),
        const SizedBox(height: 16),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final entry in _categories.entries)
              ChoiceChip(
                label: Text(entry.value),
                selected: _category == entry.key,
                onSelected: (_) => setState(() => _category = entry.key),
              ),
          ],
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _message,
          minLines: 4,
          maxLines: 8,
          maxLength: 4000,
          decoration: const InputDecoration(hintText: 'Your message', counterText: ''),
        ),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          value: _includeInfo,
          onChanged: (value) => setState(() => _includeInfo = value ?? false),
          title: const Text('Include my phone model and Android version'),
        ),
        if (_category == 'abuse')
          const Padding(
            padding: EdgeInsets.only(bottom: 8),
            child: Meta('If someone is in danger now, call 112 first.'),
          ),
        if (tickets.isNotEmpty) ...[
          const SizedBox(height: 12),
          const Meta('YOUR CONVERSATIONS'),
          const SizedBox(height: 8),
          for (final ticket in tickets) _TicketCard(ticket),
        ],
      ],
    );
  }
}

class _TicketCard extends ConsumerStatefulWidget {
  const _TicketCard(this.ticket);

  final SupportTicket ticket;

  @override
  ConsumerState<_TicketCard> createState() => _TicketCardState();
}

class _TicketCardState extends ConsumerState<_TicketCard> {
  final _reply = TextEditingController();
  bool _open = false;

  @override
  void dispose() {
    _reply.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_reply.text.trim().isEmpty) return;
    try {
      await ref.read(repositoryProvider).replyTicket(widget.ticket.id, _reply.text.trim());
      _reply.clear();
      ref.invalidate(ticketsProvider);
    } on ApiException catch (error) {
      if (mounted) showMessage(context, error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final ticket = widget.ticket;
    final last = ticket.messages.isEmpty ? null : ticket.messages.last;
    final replied = ticket.status == 'waiting';
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: DeyCard(
        onTap: () => setState(() => _open = !_open),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    _categories[ticket.category] ?? ticket.category,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
                _StatusPill(
                  ticket.status == 'closed'
                      ? 'Closed'
                      : replied
                      ? 'Replied'
                      : 'Open',
                  highlight: replied,
                ),
              ],
            ),
            if (last != null && !_open)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Meta('${last.fromStaff ? 'Dey Alert: ' : 'You: '}${last.body}'),
              ),
            if (_open) ...[
              const SizedBox(height: 10),
              for (final message in ticket.messages)
                Align(
                  alignment: message.fromStaff ? Alignment.centerLeft : Alignment.centerRight,
                  child: Container(
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.all(12),
                    constraints: const BoxConstraints(maxWidth: 280),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(16),
                      color: message.fromStaff
                          ? DeyTokens.primary.withValues(alpha: 0.10)
                          : DeyTokens.darkBackground,
                      border: Border.all(color: DeyTokens.darkBorder),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(message.body),
                        const SizedBox(height: 4),
                        Meta('${message.fromStaff ? 'Dey Alert' : 'You'} · ${timeAgo(message.at)}'),
                      ],
                    ),
                  ),
                ),
              if (ticket.status != 'closed')
                Row(
                  children: [
                    Expanded(
                      child: TextField(
                        controller: _reply,
                        decoration: const InputDecoration(hintText: 'Reply'),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Send reply',
                      onPressed: _send,
                      icon: const Icon(Icons.send, color: DeyTokens.primary),
                    ),
                  ],
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill(this.label, {this.highlight = false});

  final String label;
  final bool highlight;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
    decoration: BoxDecoration(
      borderRadius: BorderRadius.circular(DeyTokens.pillRadius),
      border: Border.all(color: highlight ? DeyTokens.primary : DeyTokens.darkBorder),
      color: highlight ? DeyTokens.primary.withValues(alpha: 0.12) : null,
    ),
    child: Text(
      label,
      style: TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w600,
        color: highlight ? DeyTokens.primary : DeyTokens.darkSecondaryText,
      ),
    ),
  );
}

// Verifier applications

class VerifierApplicationScreen extends ConsumerStatefulWidget {
  const VerifierApplicationScreen({super.key});

  @override
  ConsumerState<VerifierApplicationScreen> createState() => _VerifierApplicationScreenState();
}

class _VerifierApplicationScreenState extends ConsumerState<VerifierApplicationScreen> {
  final _name = TextEditingController();
  final _role = TextEditingController();
  final _reason = TextEditingController();
  int? _lga;
  int? _ward;
  List<Map<String, dynamic>> _wards = const [];
  bool _agreed = false;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _role.dispose();
    _reason.dispose();
    super.dispose();
  }

  Future<void> _chooseLga(int? lga) async {
    setState(() {
      _lga = lga;
      _ward = null;
      _wards = const [];
    });
    if (lga == null) return;
    final wards = await ref.read(repositoryProvider).wards(lga);
    if (mounted && _lga == lga) setState(() => _wards = wards);
  }

  Future<void> _submit() async {
    if (_name.text.trim().length < 2 || _role.text.trim().length < 2 || _lga == null) {
      setState(() => _error = 'Fill in your name, your role and your LGA.');
      return;
    }
    if (_reason.text.trim().length < 20) {
      setState(
        () => _error = 'Tell us a bit more about why people trust you (20 characters or more).',
      );
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(repositoryProvider).applyToVerify({
        'full_name': _name.text.trim(),
        'community_role': _role.text.trim(),
        'lga_id': _lga,
        'ward_id': _ward,
        'reason': _reason.text.trim(),
      });
      ref.invalidate(verifierApplicationProvider);
    } on Exception catch (error) {
      setState(() => _error = _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final info = ref.watch(verifierApplicationProvider);
    return info.when(
      loading: () => const DeyPage(children: [Center(child: CircularProgressIndicator())]),
      error: (error, _) => DeyPage(
        title: t('Become a verifier'),
        children: [
          CenterMessage(
            icon: Icons.cloud_off_outlined,
            title: 'Could not load',
            body: _message(error),
          ),
        ],
      ),
      data: (data) {
        if (data.isVerifier) {
          return DeyPage(
            title: t('Become a verifier'),
            children: const [
              CenterMessage(
                icon: Icons.verified_user_outlined,
                title: "You're a verifier",
                body: 'Open a report in your area to confirm it, close it or post an update.',
              ),
            ],
          );
        }
        if (data.status == 'pending') {
          return DeyPage(
            title: t('Become a verifier'),
            children: [
              CenterMessage(
                icon: Icons.hourglass_top,
                title: 'Application received',
                body:
                    'Our team is reviewing your application for ${data.area ?? 'your area'}. '
                    "We'll update your role when it's approved.",
              ),
            ],
          );
        }
        return DeyPage(
          title: t('Become a verifier'),
          bottom: PillButton(
            'Submit application',
            glow: true,
            busy: _busy,
            onPressed: _agreed ? _submit : null,
          ),
          children: [
            const Heading(
              'Help keep alerts accurate',
              subtitle: 'Verifiers are trusted community members who confirm what is happening in their ward.',
            ),
            const SizedBox(height: 14),
            const DeyCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _Bullet(Icons.check_circle_outline, 'Confirm or close reports nearby'),
                  _Bullet(Icons.campaign_outlined, 'Post updates such as "road now clear"'),
                  _Bullet(Icons.shield_outlined, 'Your title is shown, never your phone number'),
                ],
              ),
            ),
            if (data.status == 'rejected')
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Meta('Your last application was not approved. You can apply again.'),
              ),
            const SizedBox(height: 16),
            TextField(
              controller: _name,
              decoration: const InputDecoration(labelText: 'Full name'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _role,
              decoration: const InputDecoration(
                labelText: 'Your role in the community',
                hintText: 'e.g. CDA chairman, youth leader, imam, pastor',
              ),
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<int>(
              initialValue: _lga,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'LGA'),
              items: [
                for (final lga in data.lgas) DropdownMenuItem(value: lga.id, child: Text(lga.name)),
              ],
              onChanged: _chooseLga,
            ),
            const SizedBox(height: 12),
            DropdownButtonFormField<int>(
              initialValue: _ward,
              isExpanded: true,
              decoration: const InputDecoration(labelText: 'Ward (optional)'),
              items: [
                for (final ward in _wards)
                  DropdownMenuItem(value: ward['id'] as int, child: Text(ward['name'] as String)),
              ],
              onChanged: _wards.isEmpty ? null : (value) => setState(() => _ward = value),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _reason,
              minLines: 3,
              maxLines: 6,
              maxLength: 1000,
              decoration: const InputDecoration(
                labelText: 'Why should people trust you?',
                counterText: '',
              ),
            ),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: _agreed,
              onChanged: (value) => setState(() => _agreed = value ?? false),
              title: const Text(
                'I will only confirm what I know is true, and never share reporters\' details.',
              ),
            ),
            if (_error != null) Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
          ],
        );
      },
    );
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet(this.icon, this.text);

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(
      children: [
        Icon(icon, size: 20, color: DeyTokens.primary),
        const SizedBox(width: 10),
        Expanded(child: Text(text)),
      ],
    ),
  );
}

// Data correction

class CorrectionScreen extends ConsumerStatefulWidget {
  const CorrectionScreen({super.key});

  @override
  ConsumerState<CorrectionScreen> createState() => _CorrectionScreenState();
}

class _CorrectionScreenState extends ConsumerState<CorrectionScreen> {
  final _details = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _details.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_details.text.trim().length < 10) {
      showMessage(context, 'Tell us what is wrong (10 characters or more).');
      return;
    }
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).requestCorrection(_details.text.trim());
      if (!mounted) return;
      showMessage(context, 'Request received. We reply within 30 days.');
      context.pop();
    } on Exception catch (error) {
      if (mounted) showMessage(context, _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    title: t('Correct my data'),
    bottom: PillButton('Send request', glow: true, busy: _busy, onPressed: _send),
    children: [
      const Heading(
        'Something wrong in your data?',
        subtitle:
            'Tell us what to correct. Under Nigerian data protection law we reply within 30 days.',
      ),
      const SizedBox(height: 16),
      TextField(
        controller: _details,
        minLines: 4,
        maxLines: 8,
        maxLength: 2000,
        decoration: const InputDecoration(hintText: 'What should we correct?', counterText: ''),
      ),
      const Meta('You can download or delete your data yourself under Settings → Your data.'),
    ],
  );
}

// Sanctions and appeals

/// Shown when staff have suspended the account or paused reporting.
class SanctionNotice extends ConsumerWidget {
  const SanctionNotice({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sanctions = ref.watch(sanctionsProvider).asData?.value ?? const <Sanction>[];
    if (sanctions.isEmpty) return const SizedBox.shrink();
    final sanction = sanctions.first;
    final until = sanction.endsAt == null
        ? ''
        : ' until ${MaterialLocalizations.of(context).formatMediumDate(sanction.endsAt!.toLocal())}';
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: DeyCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.gpp_maybe_outlined, color: DeyTokens.accent),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    sanction.kind == 'suspend'
                        ? 'Your account is suspended$until'
                        : 'Reporting is paused$until',
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(sanction.reason),
            const SizedBox(height: 10),
            if (sanction.appealStatus == 'none')
              PillButton(
                'Appeal this decision',
                kind: PillKind.secondary,
                onPressed: () => _sheet(context, _AppealSheet(sanction)),
              )
            else
              Meta(
                sanction.appealStatus == 'pending'
                    ? 'Your appeal is being reviewed.'
                    : 'Your appeal was ${sanction.appealStatus}.',
              ),
          ],
        ),
      ),
    );
  }
}

class _AppealSheet extends ConsumerStatefulWidget {
  const _AppealSheet(this.sanction);

  final Sanction sanction;

  @override
  ConsumerState<_AppealSheet> createState() => _AppealSheetState();
}

class _AppealSheetState extends ConsumerState<_AppealSheet> {
  final _text = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_text.text.trim().length < 10) {
      showMessage(context, 'Explain why in at least 10 characters.');
      return;
    }
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).appeal(widget.sanction.id, _text.text.trim());
      ref.invalidate(sanctionsProvider);
      if (mounted) Navigator.of(context).pop();
    } on Exception catch (error) {
      if (mounted) showMessage(context, _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Heading('Appeal', subtitle: 'A different staff member reviews your appeal.'),
      const SizedBox(height: 12),
      TextField(
        controller: _text,
        minLines: 3,
        maxLines: 6,
        maxLength: 2000,
        decoration: const InputDecoration(hintText: 'Why should this be lifted?', counterText: ''),
      ),
      PillButton('Send appeal', glow: true, busy: _busy, onPressed: _send),
    ],
  );
}

// Announcements

const _dismissedKey = 'dismissed_announcements';

class AnnouncementBanner extends ConsumerStatefulWidget {
  const AnnouncementBanner({super.key});

  @override
  ConsumerState<AnnouncementBanner> createState() => _AnnouncementBannerState();
}

class _AnnouncementBannerState extends ConsumerState<AnnouncementBanner> {
  Set<String> _dismissed = {};

  @override
  void initState() {
    super.initState();
    _dismissed = (ref.read(repositoryProvider).prefs.getStringList(_dismissedKey) ?? []).toSet();
  }

  Future<void> _dismiss(int id) async {
    setState(() => _dismissed = {..._dismissed, '$id'});
    await ref.read(repositoryProvider).prefs.setStringList(_dismissedKey, _dismissed.toList());
  }

  @override
  Widget build(BuildContext context) {
    final items = (ref.watch(announcementsProvider).asData?.value ?? const <Announcement>[])
        .where((item) => !_dismissed.contains('${item.id}'))
        .toList();
    return Column(
      children: [
        for (final item in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Container(
              padding: const EdgeInsets.fromLTRB(14, 12, 4, 12),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
                color: DeyTokens.accent.withValues(alpha: 0.08),
                border: Border.all(color: DeyTokens.accent.withValues(alpha: 0.45)),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.campaign_outlined, color: DeyTokens.accent),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          item.area == null ? item.title : 'Notice for ${item.area}: ${item.title}',
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 2),
                        Text(item.body),
                      ],
                    ),
                  ),
                  IconButton(
                    tooltip: 'Dismiss',
                    icon: const Icon(Icons.close, size: 18),
                    onPressed: () => _dismiss(item.id),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

// Verifier updates on an incident

/// Follow-ups stay open while a report is live, including after it is confirmed.
bool takesFollowUps(Incident incident) =>
    const {'unconfirmed', 'corroborated', 'confirmed'}.contains(incident.status);

class IncidentUpdates extends ConsumerWidget {
  const IncidentUpdates(
    this.incidentId, {
    required this.canPost,
    this.canFollowUp = false,
    super.key,
  });

  final String incidentId;

  /// Verifiers and staff post official updates.
  final bool canPost;

  /// Anyone signed in can add a follow-up while the report is live.
  final bool canFollowUp;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final updates =
        ref.watch(incidentUpdatesProvider(incidentId)).asData?.value ?? const <IncidentUpdate>[];
    if (updates.isEmpty && !canPost && !canFollowUp) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 20),
        const Meta('UPDATES'),
        const SizedBox(height: 8),
        for (final update in updates)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: DeyCard(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Icon(
                    update.isCommunity ? Icons.person_outline : Icons.verified_user_outlined,
                    size: 20,
                    color: update.isCommunity ? DeyTokens.darkSecondaryText : DeyTokens.primary,
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(update.body),
                        const SizedBox(height: 4),
                        Meta('${update.author} · ${timeAgo(update.at)}'),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),
        if (updates.isEmpty)
          const Padding(
            padding: EdgeInsets.only(bottom: 10),
            child: Meta('No updates yet. Seen something since? Add a follow-up.'),
          ),
        if (canFollowUp)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: PillButton(
              t('Add a follow-up'),
              icon: Icons.add_comment_outlined,
              onPressed: () => _sheet(context, _FollowUpSheet(incidentId)),
            ),
          ),
        if (canPost)
          PillButton(
            'Post an update',
            kind: PillKind.secondary,
            icon: Icons.campaign_outlined,
            onPressed: () => _sheet(context, _PostUpdateSheet(incidentId)),
          ),
      ],
    );
  }
}

class _PostUpdateSheet extends ConsumerStatefulWidget {
  const _PostUpdateSheet(this.incidentId);

  final String incidentId;

  @override
  ConsumerState<_PostUpdateSheet> createState() => _PostUpdateSheetState();
}

class _PostUpdateSheetState extends ConsumerState<_PostUpdateSheet> {
  final _text = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _post() async {
    if (_text.text.trim().length < 2) return;
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).postIncidentUpdate(widget.incidentId, _text.text.trim());
      ref.invalidate(incidentUpdatesProvider(widget.incidentId));
      if (mounted) Navigator.of(context).pop();
    } on Exception catch (error) {
      if (mounted) showMessage(context, _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Heading(
        'Post an update',
        subtitle: 'Everyone who opens this report sees it, with your title.',
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final quick in const [
            'Road now clear',
            'Police on the scene',
            'Situation calm',
            'Still ongoing',
          ])
            ActionChip(label: Text(quick), onPressed: () => _text.text = quick),
        ],
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _text,
        maxLength: 500,
        minLines: 2,
        maxLines: 4,
        decoration: const InputDecoration(hintText: 'What is happening now?', counterText: ''),
      ),
      PillButton('Post update', glow: true, busy: _busy, onPressed: _post),
    ],
  );
}

class _FollowUpSheet extends ConsumerStatefulWidget {
  const _FollowUpSheet(this.incidentId);

  final String incidentId;

  @override
  ConsumerState<_FollowUpSheet> createState() => _FollowUpSheetState();
}

class _FollowUpSheetState extends ConsumerState<_FollowUpSheet> {
  final _text = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _post() async {
    if (_text.text.trim().length < 2) return;
    setState(() => _busy = true);
    try {
      await ref.read(repositoryProvider).postFollowup(widget.incidentId, _text.text.trim());
      ref.invalidate(incidentUpdatesProvider(widget.incidentId));
      if (!mounted) return;
      Navigator.of(context).pop();
      showMessage(context, 'Thanks. Your follow-up is posted.');
    } on Exception catch (error) {
      if (mounted) showMessage(context, _message(error));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Heading(
        t('Add a follow-up'),
        subtitle: "Share what you've seen since. Your name is never shown.",
      ),
      const SizedBox(height: 12),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final quick in const [
            'Still happening',
            'Police arrived',
            "It's over",
            'Road clear',
          ])
            ActionChip(label: Text(quick), onPressed: () => _text.text = quick),
        ],
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _text,
        maxLength: 500,
        minLines: 2,
        maxLines: 5,
        decoration: const InputDecoration(hintText: 'What is happening now?', counterText: ''),
      ),
      const Meta("Follow-ups are checked. Don't name people or share phone numbers."),
      const SizedBox(height: 10),
      PillButton('Post follow-up', glow: true, busy: _busy, onPressed: _post),
    ],
  );
}

