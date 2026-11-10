import 'dart:async';
import 'dart:math';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';

import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';

const clusterThreshold = 50;

class MapCluster {
  MapCluster(this.items);

  final List<Incident> items;

  double get lat => items.map((i) => i.lat).reduce((a, b) => a + b) / items.length;
  double get lng => items.map((i) => i.lng).reduce((a, b) => a + b) / items.length;
}

List<MapCluster> clusterIncidents(List<Incident> incidents, double cellDegrees) {
  if (incidents.length <= clusterThreshold) {
    return incidents.map((i) => MapCluster([i])).toList();
  }
  final cells = <(int, int), List<Incident>>{};
  for (final incident in incidents) {
    final key = (
      (incident.lat / cellDegrees).floor(),
      (incident.lng / cellDegrees).floor(),
    );
    cells.putIfAbsent(key, () => []).add(incident);
  }
  return cells.values.map(MapCluster.new).toList();
}

/// Colour shows severity; the ring shows verification.
Future<BitmapDescriptor> incidentPin(String severity, String status) async {
  const size = 96.0;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  const center = Offset(size / 2, size / 2);
  final color = severityColor(severity);
  canvas
    ..drawCircle(center, 34, Paint()..color = DeyTokens.darkBackground)
    ..drawCircle(center, 22, Paint()..color = color);
  final ring = Paint()
    ..color = status == 'confirmed' ? Colors.white : color
    ..style = PaintingStyle.stroke
    ..strokeWidth = 5;
  if (status == 'unconfirmed') {
    const dashes = 12;
    final rect = Rect.fromCircle(center: center, radius: 34);
    for (var i = 0; i < dashes; i++) {
      canvas.drawArc(rect, i * 2 * pi / dashes, pi / dashes, false, ring);
    }
  } else {
    canvas.drawCircle(center, 34, ring);
  }
  if (status == 'confirmed') {
    canvas.drawCircle(
      const Offset(74, 22),
      15,
      Paint()..color = Colors.white,
    );
    final tick = Paint()
      ..color = DeyTokens.darkBackground
      ..style = PaintingStyle.stroke
      ..strokeWidth = 4;
    canvas.drawPath(
      Path()
        ..moveTo(67, 22)
        ..lineTo(72, 28)
        ..lineTo(82, 16),
      tick,
    );
  }
  final image = await recorder.endRecording().toImage(size.toInt(), size.toInt());
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  return BitmapDescriptor.bytes(
    bytes!.buffer.asUint8List(),
    width: 40,
    height: 40,
  );
}

class MapTab extends ConsumerStatefulWidget {
  const MapTab({super.key});

  @override
  ConsumerState<MapTab> createState() => _MapTabState();
}

class _MapTabState extends ConsumerState<MapTab> {
  final _pins = <String, BitmapDescriptor>{};
  GoogleMapController? _controller;
  Incident? _selected;

  Future<void> _preparePins(List<Incident> incidents) async {
    var added = false;
    for (final incident in incidents) {
      final key = '${incident.severity}|${incident.status}';
      if (!_pins.containsKey(key)) {
        _pins[key] = await incidentPin(incident.severity, incident.status);
        added = true;
      }
    }
    if (added && mounted) setState(() {});
  }

  Set<Marker> _markers(List<Incident> incidents) {
    final clusters = clusterIncidents(incidents, 0.01);
    return {
      for (final cluster in clusters)
        Marker(
          markerId: MarkerId(cluster.items.first.id),
          position: LatLng(cluster.lat, cluster.lng),
          icon:
              _pins['${cluster.items.first.severity}|${cluster.items.first.status}'] ??
              BitmapDescriptor.defaultMarker,
          infoWindow: cluster.items.length > 1
              ? InfoWindow(title: '${cluster.items.length} reports here')
              : InfoWindow.noText,
          onTap: cluster.items.length == 1
              ? () => setState(() => _selected = cluster.items.first)
              : () => unawaited(
                  _controller?.animateCamera(
                    CameraUpdate.newLatLngZoom(
                      LatLng(cluster.lat, cluster.lng),
                      15,
                    ),
                  ),
                ),
        ),
    };
  }

  @override
  Widget build(BuildContext context) {
    final center = ref.watch(centerProvider);
    final feed = ref.watch(feedProvider);
    final incidents = feed.asData?.value.incidents ?? const <Incident>[];
    unawaited(_preparePins(incidents));
    final start = center.asData?.value ?? defaultCenter;
    return Stack(
      children: [
        GoogleMap(
          initialCameraPosition: CameraPosition(
            target: LatLng(start.lat, start.lng),
            zoom: 14,
          ),
          onMapCreated: (controller) => _controller = controller,
          markers: _markers(incidents),
          myLocationEnabled: center.hasValue,
          myLocationButtonEnabled: false,
          zoomControlsEnabled: false,
          mapToolbarEnabled: false,
          onTap: (_) => setState(() => _selected = null),
        ),
        SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    if (feed.asData?.value.cachedAt != null)
                      _Chip(
                        'Last updated ${timeAgo(feed.asData!.value.cachedAt!)}',
                      ),
                    const Spacer(),
                    _RoundButton(
                      icon: Icons.tune,
                      onTap: () => showFilterSheet(context),
                    ),
                  ],
                ),
                const Spacer(),
                Align(
                  alignment: Alignment.centerRight,
                  child: _RoundButton(
                    icon: Icons.my_location,
                    onTap: () async {
                      ref.invalidate(centerProvider);
                      final point = await ref.read(centerProvider.future);
                      await _controller?.animateCamera(
                        CameraUpdate.newLatLng(LatLng(point.lat, point.lng)),
                      );
                    },
                  ),
                ),
                const SizedBox(height: 92),
              ],
            ),
          ),
        ),
        if (_selected != null)
          Positioned(
            left: 12,
            right: 12,
            bottom: 104,
            child: IncidentSummaryCard(
              _selected!,
              onTap: () => context.push('/incident/${_selected!.id}'),
            ),
          ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
    decoration: BoxDecoration(
      color: DeyTokens.darkSurface,
      borderRadius: BorderRadius.circular(DeyTokens.pillRadius),
      border: Border.all(color: DeyTokens.darkBorder),
    ),
    child: Text(text, style: const TextStyle(fontSize: 12)),
  );
}

class _RoundButton extends StatelessWidget {
  const _RoundButton({required this.icon, required this.onTap});

  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
    color: DeyTokens.darkSurface,
    shape: const CircleBorder(side: BorderSide(color: DeyTokens.darkBorder)),
    child: InkWell(
      customBorder: const CircleBorder(),
      onTap: onTap,
      child: SizedBox.square(dimension: 48, child: Icon(icon)),
    ),
  );
}

class IncidentSummaryCard extends StatelessWidget {
  const IncidentSummaryCard(this.incident, {this.onTap, super.key});

  final Incident incident;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final type = incidentType(incident.typeCode);
    final quiet = incident.status == 'unconfirmed';
    final title = TextStyle(
      fontSize: 16,
      fontWeight: FontWeight.w700,
      color: quiet ? DeyTokens.darkSecondaryText : DeyTokens.darkOnSurface,
    );
    return DeyCard(
      onTap: onTap,
      padding: const EdgeInsets.all(14),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(type.icon, color: DeyTokens.darkSecondaryText),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(incident.title, style: title),
                const SizedBox(height: 2),
                Text(
                  [
                    timeAgo(incident.occurredAt),
                    if (incident.locationName != null) incident.locationName!,
                  ].join(' · '),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: DeyTokens.darkSecondaryText,
                    fontSize: 13,
                  ),
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    SeverityChip(incident.severity),
                    VerificationBadge(incident),
                  ],
                ),
                if (incident.description != null) ...[
                  const SizedBox(height: 6),
                  Text(
                    incident.description!,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: DeyTokens.darkSecondaryText,
                      fontSize: 13,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

Future<void> showFilterSheet(BuildContext context) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: DeyTokens.darkSurface,
      builder: (_) => const FilterSheet(),
    );

class FilterSheet extends ConsumerStatefulWidget {
  const FilterSheet({super.key});

  @override
  ConsumerState<FilterSheet> createState() => _FilterSheetState();
}

class _FilterSheetState extends ConsumerState<FilterSheet> {
  late FeedFilter _filter = ref.read(filterProvider);

  static const _windows = {
    '1h': Duration(hours: 1),
    '6h': Duration(hours: 6),
    '24h': Duration(hours: 24),
    '7 days': Duration(days: 7),
  };

  void _toggleType(String code) {
    final types = {..._filter.types};
    types.contains(code) ? types.remove(code) : types.add(code);
    setState(() => _filter = _filter.copyWith(types: types));
  }

  void _toggleStatus(String status) {
    final statuses = {..._filter.statuses};
    statuses.contains(status) ? statuses.remove(status) : statuses.add(status);
    setState(() => _filter = _filter.copyWith(statuses: statuses));
  }

  @override
  Widget build(BuildContext context) => SafeArea(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Filter incidents',
              style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 14),
            const Meta('Incident type'),
            const SizedBox(height: 6),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final type in incidentTypes)
                  FilterChip(
                    avatar: Icon(type.icon, size: 16),
                    label: Text(type.label),
                    selected: _filter.types.contains(type.code),
                    onSelected: (_) => _toggleType(type.code),
                  ),
              ],
            ),
            const SizedBox(height: 14),
            const Meta('Status'),
            Wrap(
              spacing: 6,
              children: [
                for (final (code, label) in const [
                  ('unconfirmed', 'Unverified'),
                  ('corroborated', 'Corroborated'),
                  ('confirmed', 'Confirmed'),
                ])
                  FilterChip(
                    label: Text(label),
                    selected: _filter.statuses.contains(code),
                    onSelected: (_) => _toggleStatus(code),
                  ),
              ],
            ),
            const SizedBox(height: 14),
            Meta('Radius: ${_filter.radiusKm.round()} km'),
            Slider(
              min: 1,
              max: 20,
              divisions: 19,
              value: _filter.radiusKm,
              onChanged: (value) =>
                  setState(() => _filter = _filter.copyWith(radiusKm: value)),
            ),
            const Meta('Time window'),
            Wrap(
              spacing: 6,
              children: [
                for (final entry in _windows.entries)
                  ChoiceChip(
                    label: Text(entry.key),
                    selected: _filter.window == entry.value,
                    onSelected: (_) => setState(
                      () => _filter = _filter.copyWith(window: entry.value),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 18),
            Row(
              children: [
                Expanded(
                  child: PillButton(
                    'Reset',
                    kind: PillKind.secondary,
                    onPressed: () =>
                        setState(() => _filter = const FeedFilter()),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: PillButton(
                    'Apply',
                    onPressed: () {
                      ref.read(filterProvider.notifier).set(_filter);
                      Navigator.pop(context);
                    },
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    ),
  );
}
