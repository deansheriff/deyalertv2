import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:image_picker/image_picker.dart';

import '../../core/i18n.dart';
import '../../core/services/location_service.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';

class PickedLocation {
  const PickedLocation(this.point, this.landmark);

  final LatLngPoint point;
  final String? landmark;
}

class ReportTab extends ConsumerStatefulWidget {
  const ReportTab({super.key});

  @override
  ConsumerState<ReportTab> createState() => _ReportTabState();
}

class _ReportTabState extends ConsumerState<ReportTab> {
  final _description = TextEditingController();
  final _picker = ImagePicker();
  String? _type;
  PickedLocation? _location;
  XFile? _media;
  String? _mediaKind;
  bool _anonymous = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    unawaited(_useCurrentLocation());
  }

  Future<void> _useCurrentLocation() async {
    final point = await ref.read(repositoryProvider).location.current();
    if (point != null && mounted) {
      setState(() => _location = PickedLocation(point, null));
    }
  }

  Future<void> _pickLocation() async {
    final picked = await context.push<PickedLocation>(
      '/report/location',
      extra: _location?.point,
    );
    if (picked != null) setState(() => _location = picked);
  }

  Future<void> _pick(String kind, ImageSource source) async {
    final file = kind == 'image'
        ? await _picker.pickImage(source: source)
        : await _picker.pickVideo(
            source: source,
            maxDuration: const Duration(seconds: 15),
          );
    if (file != null) {
      setState(() {
        _media = file;
        _mediaKind = kind;
      });
    }
  }

  Future<void> _submit() async {
    setState(() => _busy = true);
    final repository = ref.read(repositoryProvider);
    final sent = await repository.submitReport(
      typeCode: _type!,
      lat: _location!.point.lat,
      lng: _location!.point.lng,
      anonymous: _anonymous,
      description: _description.text.trim().isEmpty
          ? null
          : _description.text.trim(),
      locationName: _location!.landmark,
      mediaPath: _media?.path,
      mediaKind: _mediaKind,
    );
    ref.invalidate(feedProvider);
    if (!mounted) return;
    setState(() {
      _busy = false;
      _type = null;
      _media = null;
      _mediaKind = null;
      _description.clear();
    });
    unawaited(context.push('/report/result', extra: sent));
  }

  @override
  void dispose() {
    _description.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SafeArea(
    bottom: false,
    child: ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 112),
      children: [
        Heading(t('Report Incident')),
        const SizedBox(height: 16),
        GridView.count(
          crossAxisCount: 3,
          shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 8,
          crossAxisSpacing: 8,
          childAspectRatio: 1.15,
          children: [
            for (final type in incidentTypes)
              _TypeTile(
                type: type,
                selected: _type == type.code,
                onTap: () => setState(() => _type = type.code),
              ),
          ],
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _description,
          maxLength: 1000,
          maxLines: 4,
          decoration: InputDecoration(
            hintText: t(
              'Describe what happened and where. Do not name people or groups.',
            ),
            hintMaxLines: 3,
          ),
        ),
        const SizedBox(height: 4),
        DeyCard(
          onTap: _pickLocation,
          child: Row(
            children: [
              const Icon(Icons.place_outlined, color: DeyTokens.primary),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  _location == null
                      ? 'Choose location'
                      : _location!.landmark ?? 'Current location',
                ),
              ),
              const Text('Change', style: TextStyle(color: DeyTokens.primary)),
            ],
          ),
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(
              child: PillButton(
                _mediaKind == 'image' ? 'Photo added' : 'Add photo',
                kind: PillKind.secondary,
                icon: Icons.photo_camera_outlined,
                onPressed: () => _pick('image', ImageSource.camera),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: PillButton(
                _mediaKind == 'video' ? 'Video added' : 'Add video',
                kind: PillKind.secondary,
                icon: Icons.videocam_outlined,
                onPressed: () => _pick('video', ImageSource.camera),
              ),
            ),
          ],
        ),
        const SizedBox(height: 10),
        DeyCard(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
          child: SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: Text(t('Report anonymously')),
            subtitle: Text(t('Hidden from other users')),
            value: _anonymous,
            onChanged: (value) => setState(() => _anonymous = value),
          ),
        ),
        const SizedBox(height: 16),
        PillButton(
          t('Submit Report'),
          glow: true,
          busy: _busy,
          onPressed: _type != null && _location != null ? _submit : null,
        ),
        Center(
          child: TextButton(
            onPressed: call112,
            child: const Text(
              'Call 112',
              style: TextStyle(color: DeyTokens.danger),
            ),
          ),
        ),
      ],
    ),
  );
}

class _TypeTile extends StatelessWidget {
  const _TypeTile({
    required this.type,
    required this.selected,
    required this.onTap,
  });

  final IncidentType type;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
    color: DeyTokens.darkSurface,
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
      side: BorderSide(
        color: selected ? DeyTokens.primary : DeyTokens.darkBorder,
        width: selected ? 2 : 1,
      ),
    ),
    child: InkWell(
      borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.all(6),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(type.icon),
            const SizedBox(height: 4),
            Text(
              type.label,
              textAlign: TextAlign.center,
              maxLines: 2,
              style: const TextStyle(fontSize: 12),
            ),
          ],
        ),
      ),
    ),
  );
}

class LocationPickerScreen extends ConsumerStatefulWidget {
  const LocationPickerScreen({this.initial, super.key});

  final LatLngPoint? initial;

  @override
  ConsumerState<LocationPickerScreen> createState() =>
      _LocationPickerScreenState();
}

class _LocationPickerScreenState extends ConsumerState<LocationPickerScreen> {
  final _landmark = TextEditingController();
  GoogleMapController? _controller;
  late LatLng _target = LatLng(
    widget.initial?.lat ?? defaultCenter.lat,
    widget.initial?.lng ?? defaultCenter.lng,
  );

  @override
  void dispose() {
    _landmark.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      backgroundColor: DeyTokens.darkBackground,
      title: const Text('Pin the location'),
    ),
    body: Column(
      children: [
        Expanded(
          child: Stack(
            alignment: Alignment.center,
            children: [
              GoogleMap(
                initialCameraPosition: CameraPosition(target: _target, zoom: 16),
                onMapCreated: (controller) => _controller = controller,
                onCameraMove: (position) => _target = position.target,
                myLocationEnabled: true,
                myLocationButtonEnabled: false,
                zoomControlsEnabled: false,
              ),
              const Padding(
                padding: EdgeInsets.only(bottom: 40),
                child: Icon(Icons.place, size: 48, color: DeyTokens.primary),
              ),
            ],
          ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextField(
                  controller: _landmark,
                  decoration: const InputDecoration(
                    hintText: 'Nearby landmark, e.g. filling station',
                  ),
                ),
                const SizedBox(height: 10),
                PillButton(
                  'Use my location',
                  kind: PillKind.secondary,
                  icon: Icons.my_location,
                  onPressed: () async {
                    final point = await ref
                        .read(repositoryProvider)
                        .location
                        .current();
                    if (point != null) {
                      await _controller?.animateCamera(
                        CameraUpdate.newLatLng(LatLng(point.lat, point.lng)),
                      );
                    }
                  },
                ),
                const SizedBox(height: 10),
                PillButton(
                  'Confirm location',
                  glow: true,
                  onPressed: () => context.pop(
                    PickedLocation(
                      LatLngPoint(_target.latitude, _target.longitude),
                      _landmark.text.trim().isEmpty
                          ? null
                          : _landmark.text.trim(),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    ),
  );
}

class ReportResultScreen extends ConsumerWidget {
  const ReportResultScreen({required this.sent, super.key});

  final bool sent;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pending = ref.watch(pendingCountProvider).asData?.value ?? 0;
    return DeyPage(
      bottom: PillButton(
        'Back to feed',
        glow: true,
        onPressed: () => context.go('/feed'),
      ),
      children: [
        const SizedBox(height: 32),
        Icon(
          sent ? Icons.check_circle_outline : Icons.schedule,
          size: 72,
          color: sent ? DeyTokens.primary : DeyTokens.accent,
        ),
        const SizedBox(height: 16),
        if (sent) ...[
          Heading(
            t('Report sent'),
            subtitle: 'Reports stay Unverified until 3 people nearby or a verifier confirm them.',
          ),
          const SizedBox(height: 16),
          for (final (title, body) in const [
            ('Shown as Unverified', 'Your report appears in the feed and on the map, labelled Unverified.'),
            ('Community check', 'It needs 3 people nearby or a verifier to confirm it.'),
            ('Alerts', 'People nearby get an alert once it is corroborated.'),
          ])
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: DeyCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
                    Meta(body),
                  ],
                ),
              ),
            ),
        ] else ...[
          Heading(
            t('Saved. Will send when you are online'),
            subtitle: 'Stored on your phone. $pending waiting to send.',
          ),
          const SizedBox(height: 16),
          PillButton(
            t('Retry now'),
            kind: PillKind.secondary,
            onPressed: () async {
              final repository = ref.read(repositoryProvider);
              await repository.queue.retryFailed();
              await repository.flushQueue();
              ref.invalidate(pendingCountProvider);
            },
          ),
        ],
      ],
    );
  }
}
