import 'dart:async';

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../../core/i18n.dart';
import '../../core/services/repository.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';

final _incidentProvider = FutureProvider.autoDispose.family<Incident?, String>(
  (ref, id) => ref.watch(repositoryProvider).incident(id),
);
final _timelineProvider = FutureProvider.autoDispose
    .family<List<TimelineEvent>, String>(
      (ref, id) => ref.watch(repositoryProvider).timeline(id),
    );
final _mediaProvider = FutureProvider.autoDispose
    .family<List<IncidentMedia>, String>(
      (ref, id) => ref.watch(repositoryProvider).media(id),
    );

const _eventLabels = {
  'created': 'Reported',
  'corroborated': 'Corroborated',
  'confirmed': 'Confirmed',
  'extended': 'Time extended',
  'resolved': 'Resolved',
  'expired': 'Expired',
};

class IncidentDetailScreen extends ConsumerWidget {
  const IncidentDetailScreen({required this.id, super.key});

  final String id;

  void _refresh(WidgetRef ref) {
    ref
      ..invalidate(_incidentProvider(id))
      ..invalidate(_timelineProvider(id))
      ..invalidate(feedProvider);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final incident = ref.watch(_incidentProvider(id));
    return incident.when(
      loading: () => const Scaffold(body: Center(child: CircularProgressIndicator())),
      error: (error, _) => DeyPage(
        children: [
          CenterMessage(
            icon: Icons.cloud_off_outlined,
            title: 'Could not load this report',
            body: 'Check your connection and try again.',
            action: SizedBox(
              width: 200,
              child: PillButton(
                t('Retry now'),
                onPressed: () => ref.invalidate(_incidentProvider(id)),
              ),
            ),
          ),
        ],
      ),
      data: (value) => value == null
          ? const DeyPage(
              children: [
                CenterMessage(
                  icon: Icons.visibility_off_outlined,
                  title: 'This report is not available',
                  body: 'It may be under review or no longer active.',
                ),
              ],
            )
          : _Detail(value, onChanged: () => _refresh(ref)),
    );
  }
}

class _Detail extends ConsumerWidget {
  const _Detail(this.incident, {required this.onChanged});

  final Incident incident;
  final VoidCallback onChanged;

  String get _banner => switch (incident.status) {
    'confirmed' => 'Confirmed by ${incident.confirmedByLabel ?? 'a verifier'}',
    'corroborated' => 'Corroborated by ${incident.corroborationCount} people nearby',
    'resolved' => 'Resolved',
    'expired' => 'Expired',
    _ => 'Unverified report',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final media = ref.watch(_mediaProvider(incident.id)).asData?.value ?? const [];
    final timeline = ref.watch(_timelineProvider(incident.id)).asData?.value ?? const [];
    final canVerify = ref.watch(profileProvider).asData?.value.canVerify ?? false;
    final dataSaver = ref.watch(appStateProvider).dataSaver;
    return DeyPage(
      title: 'Incident',
      children: [
        DeyCard(
          child: Row(
            children: [
              Icon(
                incident.status == 'unconfirmed'
                    ? Icons.radio_button_unchecked
                    : Icons.verified_outlined,
                color: incident.status == 'unconfirmed'
                    ? DeyTokens.darkSecondaryText
                    : Colors.white,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(_banner, style: const TextStyle(fontWeight: FontWeight.w600)),
              ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        ClipRRect(
          borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
          child: SizedBox(
            height: 170,
            child: GoogleMap(
              initialCameraPosition: CameraPosition(
                target: LatLng(incident.lat, incident.lng),
                zoom: 15,
              ),
              markers: {
                Marker(
                  markerId: const MarkerId('incident'),
                  position: LatLng(incident.lat, incident.lng),
                ),
              },
              liteModeEnabled: true,
              zoomControlsEnabled: false,
              myLocationButtonEnabled: false,
            ),
          ),
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            Expanded(
              child: Text(
                incident.title,
                style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w700),
              ),
            ),
            SeverityChip(incident.severity),
          ],
        ),
        const SizedBox(height: 4),
        Meta(
          [
            timeAgo(incident.occurredAt),
            if (incident.locationName != null) incident.locationName!,
          ].join(' · '),
        ),
        if (incident.description != null) ...[
          const SizedBox(height: 12),
          Text(incident.description!),
        ],
        if (media.isNotEmpty) ...[
          const SizedBox(height: 14),
          SizedBox(
            height: 130,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              itemCount: media.length,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (_, index) => BlurredMedia(
                url: ref.read(repositoryProvider).mediaUrl(
                  dataSaver
                      ? media[index].thumbPath ?? media[index].path
                      : media[index].path,
                ),
                blurred: media[index].blurred,
              ),
            ),
          ),
        ],
        const SizedBox(height: 16),
        if (incident.isLive) ...[
          PillButton(
            t('Corroborate'),
            glow: true,
            icon: Icons.how_to_reg_outlined,
            onPressed: () => _sheet(context, CorroborateSheet(incident, onChanged)),
          ),
          const SizedBox(height: 10),
        ],
        PillButton(
          t('Flag'),
          kind: PillKind.secondary,
          icon: Icons.flag_outlined,
          onPressed: () => _sheet(context, FlagSheet(incident, onChanged)),
        ),
        if (canVerify) ...[
          const SizedBox(height: 10),
          PillButton(
            'Verifier actions',
            kind: PillKind.secondary,
            icon: Icons.verified_user_outlined,
            onPressed: () => _sheet(context, VerifierSheet(incident, onChanged)),
          ),
        ],
        const SizedBox(height: 20),
        const Meta('TIMELINE'),
        const SizedBox(height: 8),
        for (final event in timeline)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              children: [
                const Icon(Icons.circle, size: 8, color: DeyTokens.darkSecondaryText),
                const SizedBox(width: 10),
                Expanded(child: Text(_eventLabels[event.type] ?? event.type)),
                Meta(timeAgo(event.at)),
              ],
            ),
          ),
      ],
    );
  }
}

Future<void> _sheet(BuildContext context, Widget child) => showModalBottomSheet<void>(
  context: context,
  isScrollControlled: true,
  backgroundColor: DeyTokens.darkSurface,
  builder: (_) => SafeArea(
    child: Padding(
      padding: EdgeInsets.fromLTRB(
        16,
        20,
        16,
        16 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: SingleChildScrollView(child: child),
    ),
  ),
);

class BlurredMedia extends StatefulWidget {
  const BlurredMedia({required this.url, required this.blurred, super.key});

  final String url;
  final bool blurred;

  @override
  State<BlurredMedia> createState() => _BlurredMediaState();
}

class _BlurredMediaState extends State<BlurredMedia> {
  late bool _hidden = widget.blurred;

  @override
  Widget build(BuildContext context) => GestureDetector(
    onTap: () => setState(() => _hidden = false),
    child: ClipRRect(
      borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
      child: SizedBox(
        width: 170,
        child: _hidden
            ? const ColoredBox(
                color: DeyTokens.darkBorder,
                child: Center(child: Text('Tap to view')),
              )
            : CachedNetworkImage(imageUrl: widget.url, fit: BoxFit.cover),
      ),
    ),
  );
}

class CorroborateSheet extends ConsumerStatefulWidget {
  const CorroborateSheet(this.incident, this.onDone, {super.key});

  final Incident incident;
  final VoidCallback onDone;

  @override
  ConsumerState<CorroborateSheet> createState() => _CorroborateSheetState();
}

class _CorroborateSheetState extends ConsumerState<CorroborateSheet> {
  bool _busy = false;
  String? _message;

  Future<void> _confirm() async {
    setState(() {
      _busy = true;
      _message = null;
    });
    final repository = ref.read(repositoryProvider);
    final point = await repository.location.current();
    if (point == null) {
      setState(() {
        _busy = false;
        _message = 'Location is off. Turn on location to confirm.';
      });
      return;
    }
    try {
      if (repository.deviceId == null) await repository.registerDevice(point: point);
      await repository.corroborate(widget.incident.id, point);
      widget.onDone();
      if (mounted) Navigator.pop(context);
    } on ApiException catch (error) {
      setState(() {
        _busy = false;
        _message = error.code == 'too_far'
            ? 'Too far away. You need to be within 2 km to confirm.'
            : error.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Text(
        'Are you near this and can you confirm it?',
        style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
      ),
      const SizedBox(height: 8),
      const Meta('Only confirm what you can safely see. We check that you are within 2 km.'),
      if (_message != null) ...[
        const SizedBox(height: 12),
        Text(_message!, style: const TextStyle(color: DeyTokens.accent)),
      ],
      const SizedBox(height: 16),
      PillButton('Confirm', glow: true, busy: _busy, onPressed: _confirm),
    ],
  );
}

class FlagSheet extends ConsumerStatefulWidget {
  const FlagSheet(this.incident, this.onDone, {super.key});

  final Incident incident;
  final VoidCallback onDone;

  @override
  ConsumerState<FlagSheet> createState() => _FlagSheetState();
}

class _FlagSheetState extends ConsumerState<FlagSheet> {
  static const _reasons = {
    'false_report': 'False report',
    'duplicate': 'Duplicate',
    'spam': 'Spam',
    'inappropriate': 'Inappropriate',
    'names_individual': 'Names an individual',
  };
  final _notes = TextEditingController();
  String? _reason;
  bool _busy = false;
  bool _done = false;
  String? _error;

  Future<void> _submit() async {
    setState(() => _busy = true);
    final repository = ref.read(repositoryProvider);
    try {
      final point = await repository.location.current();
      await repository.flag(
        widget.incident.id,
        _reason!,
        _notes.text.trim().isEmpty ? null : _notes.text.trim(),
        point,
      );
      widget.onDone();
      setState(() => _done = true);
    } on ApiException catch (error) {
      setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_done) {
      return const Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.check_circle_outline, size: 48, color: DeyTokens.primary),
          SizedBox(height: 10),
          Text('Thanks. A moderator will review this if needed.'),
        ],
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'Flag this report',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        RadioGroup<String>(
          groupValue: _reason,
          onChanged: (value) => setState(() => _reason = value),
          child: Column(
            children: [
              for (final entry in _reasons.entries)
                RadioListTile<String>(
                  contentPadding: EdgeInsets.zero,
                  title: Text(entry.value),
                  value: entry.key,
                ),
            ],
          ),
        ),
        TextField(
          controller: _notes,
          maxLength: 140,
          decoration: const InputDecoration(hintText: 'Notes (optional)'),
        ),
        if (_error != null) Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
        const SizedBox(height: 8),
        PillButton(
          'Submit',
          busy: _busy,
          onPressed: _reason == null ? null : _submit,
        ),
      ],
    );
  }
}

class VerifierSheet extends ConsumerStatefulWidget {
  const VerifierSheet(this.incident, this.onDone, {super.key});

  final Incident incident;
  final VoidCallback onDone;

  @override
  ConsumerState<VerifierSheet> createState() => _VerifierSheetState();
}

class _VerifierSheetState extends ConsumerState<VerifierSheet> {
  final _note = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _act(String action, {int? minutes}) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(repositoryProvider).verify(
        widget.incident.id,
        action,
        note: _note.text.trim().isEmpty ? null : _note.text.trim(),
        extendMinutes: minutes,
      );
      widget.onDone();
      if (mounted) Navigator.pop(context);
    } on ApiException catch (error) {
      setState(() {
        _busy = false;
        _error = error.message;
      });
    }
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Text(
        'Verifier actions',
        style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
      ),
      const SizedBox(height: 14),
      PillButton('Confirm', glow: true, busy: _busy, onPressed: () => _act('confirm')),
      const SizedBox(height: 8),
      PillButton('Resolve', kind: PillKind.secondary, busy: _busy, onPressed: () => _act('resolve')),
      const SizedBox(height: 10),
      const Meta('Extend'),
      const SizedBox(height: 6),
      Row(
        children: [
          for (final (label, minutes) in const [('1h', 60), ('3h', 180), ('6h', 360)])
            Expanded(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 3),
                child: PillButton(
                  '+$label',
                  kind: PillKind.secondary,
                  busy: _busy,
                  onPressed: () => _act('extend', minutes: minutes),
                ),
              ),
            ),
        ],
      ),
      const SizedBox(height: 10),
      TextField(
        controller: _note,
        maxLength: 140,
        decoration: const InputDecoration(hintText: 'Private note (optional)'),
      ),
      const Meta('Notes are visible only to verifiers and moderators.'),
      if (_error != null) Text(_error!, style: const TextStyle(color: DeyTokens.accent)),
      const SizedBox(height: 10),
      PillButton('Mark as false', kind: PillKind.danger, busy: _busy, onPressed: () => _act('mark_false')),
    ],
  );
}
