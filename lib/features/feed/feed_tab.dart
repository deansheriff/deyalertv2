import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/i18n.dart';
import '../../core/services/repository.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../providers/providers.dart';
import '../community/community_screens.dart';
import '../map/map_tab.dart';

class FeedTab extends ConsumerWidget {
  const FeedTab({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(feedProvider);
    return SafeArea(
      bottom: false,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 8, 8),
            child: Row(
              children: [
                const Expanded(
                  child: Text(
                    'Near you',
                    style: TextStyle(fontSize: 26, fontWeight: FontWeight.w700),
                  ),
                ),
                IconButton(
                  tooltip: 'Notifications',
                  icon: const Icon(Icons.notifications_none),
                  onPressed: () => context.push('/notifications'),
                ),
                IconButton(
                  tooltip: 'Filter',
                  icon: const Icon(Icons.tune),
                  onPressed: () => showFilterSheet(context),
                ),
              ],
            ),
          ),
          Expanded(
            child: feed.when(
              loading: () => const FeedSkeleton(),
              error: (error, _) => CenterMessage(
                icon: Icons.cloud_off_outlined,
                title: error is ApiException
                    ? t("You're offline")
                    : 'Something went wrong',
                body: error.toString(),
                action: SizedBox(
                  width: 200,
                  child: PillButton(
                    t('Retry now'),
                    onPressed: () => ref.invalidate(feedProvider),
                  ),
                ),
              ),
              data: (result) => RefreshIndicator(
                onRefresh: () async {
                  await ref.read(repositoryProvider).flushQueue();
                  ref
                    ..invalidate(announcementsProvider)
                    ..invalidate(feedProvider);
                  await ref.read(feedProvider.future);
                },
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 112),
                  children: [
                    const SanctionNotice(),
                    const AnnouncementBanner(),
                    if (result.isCached)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: Meta(
                          'Offline. Last updated ${timeAgo(result.cachedAt!)}.',
                        ),
                      ),
                    if (result.incidents.isEmpty)
                      SizedBox(
                        height: 380,
                        child: CenterMessage(
                          icon: Icons.verified_user_outlined,
                          title: t('All quiet in your area'),
                          body: 'No reports nearby in this time window.',
                        ),
                      ),
                    for (final incident in result.incidents)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 10),
                        child: IncidentSummaryCard(
                          incident,
                          onTap: () => context.push('/incident/${incident.id}'),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class FeedSkeleton extends StatelessWidget {
  const FeedSkeleton({super.key});

  @override
  Widget build(BuildContext context) => ListView.separated(
    padding: const EdgeInsets.all(16),
    itemCount: 5,
    separatorBuilder: (_, _) => const SizedBox(height: 10),
    itemBuilder: (_, _) => Container(
      height: 104,
      decoration: BoxDecoration(
        color: DeyTokens.darkSurface,
        borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
        border: Border.all(color: DeyTokens.darkBorder),
      ),
    ),
  );
}
