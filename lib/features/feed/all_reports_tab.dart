import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/i18n.dart';
import '../../core/services/location_service.dart';
import '../../core/services/repository.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
import '../../providers/providers.dart';
import '../map/map_tab.dart';
import 'feed_tab.dart';

/// Filter chips: label, status, severity, hours.
const _filters = [
  ('All', null, null, null),
  ('Confirmed', 'confirmed', null, null),
  ('Unverified', 'unverified', null, null),
  ('Last 24 h', null, null, 24),
  ('Critical', null, 'critical', null),
];

double distanceKm(LatLngPoint a, double lat, double lng) {
  const earthKm = 6371.0;
  double rad(double degrees) => degrees * math.pi / 180;
  final dLat = rad(lat - a.lat);
  final dLng = rad(lng - a.lng);
  final h =
      math.pow(math.sin(dLat / 2), 2) +
      math.cos(rad(a.lat)) * math.cos(rad(lat)) * math.pow(math.sin(dLng / 2), 2);
  return 2 * earthKm * math.asin(math.sqrt(h));
}

/// Reports from everywhere, so people can follow places they aren't in.
class AllReportsTab extends ConsumerStatefulWidget {
  const AllReportsTab({super.key});

  @override
  ConsumerState<AllReportsTab> createState() => _AllReportsTabState();
}

class _AllReportsTabState extends ConsumerState<AllReportsTab> {
  final _search = TextEditingController();
  Timer? _debounce;
  int _filter = 0;
  List<ReportListItem> _items = const [];
  String? _nextBefore;
  bool _loading = true;
  bool _loadingMore = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  Future<ReportPage> _page({String? before}) {
    final (_, status, severity, hours) = _filters[_filter];
    return ref
        .read(repositoryProvider)
        .allReports(
          query: _search.text,
          status: status,
          severity: severity,
          sinceHours: hours,
          before: before,
        );
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final page = await _page();
      if (!mounted) return;
      setState(() {
        _items = page.items;
        _nextBefore = page.nextBefore;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _loadMore() async {
    final before = _nextBefore;
    if (before == null || _loadingMore) return;
    setState(() => _loadingMore = true);
    try {
      final page = await _page(before: before);
      if (!mounted) return;
      setState(() {
        _items = [..._items, ...page.items];
        _nextBefore = page.nextBefore;
      });
    } on ApiException catch (error) {
      if (mounted) showMessage(context, error.message);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  void _searchChanged(String _) {
    _debounce?.cancel();
    _debounce = Timer(const Duration(milliseconds: 400), _load);
  }

  @override
  Widget build(BuildContext context) {
    final here = ref.watch(centerProvider).asData?.value;
    return SafeArea(
      bottom: false,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 0),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        t('All reports'),
                        style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w700),
                      ),
                      const Meta('Across Nigeria, newest first.'),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Notifications',
                  icon: const Icon(Icons.notifications_none),
                  onPressed: () => context.push('/notifications'),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
            child: TextField(
              controller: _search,
              onChanged: _searchChanged,
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: 'Search by place, LGA, state or type',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: _search.text.isEmpty
                    ? null
                    : IconButton(
                        tooltip: 'Clear',
                        icon: const Icon(Icons.close),
                        onPressed: () {
                          _search.clear();
                          unawaited(_load());
                        },
                      ),
              ),
            ),
          ),
          SizedBox(
            height: 44,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              itemCount: _filters.length,
              separatorBuilder: (_, _) => const SizedBox(width: 8),
              itemBuilder: (_, index) => ChoiceChip(
                label: Text(_filters[index].$1),
                selected: _filter == index,
                onSelected: (_) {
                  setState(() => _filter = index);
                  unawaited(_load());
                },
              ),
            ),
          ),
          const SizedBox(height: 4),
          Expanded(child: _body(here)),
        ],
      ),
    );
  }

  Widget _body(LatLngPoint? here) {
    if (_loading && _items.isEmpty) return const FeedSkeleton();
    if (_error != null && _items.isEmpty) {
      return CenterMessage(
        icon: Icons.cloud_off_outlined,
        title: t("You're offline"),
        body: _error!,
        action: SizedBox(width: 200, child: PillButton(t('Retry now'), onPressed: _load)),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 112),
        children: [
          if (_items.isEmpty)
            const SizedBox(
              height: 360,
              child: CenterMessage(
                icon: Icons.travel_explore,
                title: 'Nothing matches',
                body: 'Try another place or filter. Reports stay listed for 7 days.',
              ),
            ),
          for (final item in _items)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: IncidentSummaryCard(
                item.incident,
                area: item.area,
                updateCount: item.updateCount,
                distanceKm: here == null
                    ? null
                    : distanceKm(here, item.incident.lat, item.incident.lng),
                onTap: () => context.push('/incident/${item.incident.id}'),
              ),
            ),
          if (_nextBefore != null)
            PillButton(
              'Load more',
              kind: PillKind.secondary,
              busy: _loadingMore,
              onPressed: _loadMore,
            ),
          if (_loading && _items.isNotEmpty)
            const Padding(
              padding: EdgeInsets.all(12),
              child: Center(child: CircularProgressIndicator()),
            ),
        ],
      ),
    );
  }
}
