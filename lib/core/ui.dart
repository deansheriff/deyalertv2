import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models/models.dart';
import 'i18n.dart';
import 'theme/tokens.dart';

enum PillKind { primary, secondary, danger, inverse }

class PillButton extends StatelessWidget {
  const PillButton(
    this.label, {
    required this.onPressed,
    this.kind = PillKind.primary,
    this.icon,
    this.glow = false,
    this.busy = false,
    super.key,
  });

  final String label;
  final VoidCallback? onPressed;
  final PillKind kind;
  final IconData? icon;
  final bool glow;
  final bool busy;

  @override
  Widget build(BuildContext context) {
    final (background, foreground, border) = switch (kind) {
      PillKind.primary => (
        DeyTokens.primary,
        DeyTokens.onPrimary,
        DeyTokens.primary,
      ),
      PillKind.secondary => (
        DeyTokens.darkSurface,
        DeyTokens.darkOnSurface,
        DeyTokens.darkBorder,
      ),
      PillKind.danger => (
        DeyTokens.darkSurface,
        DeyTokens.danger,
        DeyTokens.danger,
      ),
      PillKind.inverse => (
        DeyTokens.darkOnSurface,
        DeyTokens.onPrimary,
        DeyTokens.darkOnSurface,
      ),
    };
    final enabled = onPressed != null && !busy;
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(DeyTokens.pillRadius),
        boxShadow: glow && enabled
            ? [
                BoxShadow(
                  color: DeyTokens.primary.withValues(alpha: 0.3),
                  blurRadius: 24,
                ),
              ]
            : null,
      ),
      child: Opacity(
        opacity: enabled ? 1 : 0.45,
        child: Material(
          color: background,
          shape: StadiumBorder(side: BorderSide(color: border)),
          child: InkWell(
            customBorder: const StadiumBorder(),
            onTap: enabled ? onPressed : null,
            child: ConstrainedBox(
              constraints: const BoxConstraints(minHeight: 52),
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 12,
                  ),
                  child: busy
                      ? SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: foreground,
                          ),
                        )
                      : Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (icon != null) ...[
                              Icon(icon, size: 20, color: foreground),
                              const SizedBox(width: 8),
                            ],
                            Flexible(
                              child: Text(
                                label,
                                textAlign: TextAlign.center,
                                style: TextStyle(
                                  color: foreground,
                                  fontWeight: FontWeight.w700,
                                  fontSize: 16,
                                ),
                              ),
                            ),
                          ],
                        ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class DeyCard extends StatelessWidget {
  const DeyCard({
    required this.child,
    this.onTap,
    this.padding = const EdgeInsets.all(16),
    this.dashed = false,
    super.key,
  });

  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsets padding;
  final bool dashed;

  @override
  Widget build(BuildContext context) => Material(
    color: DeyTokens.darkSurface,
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
      side: const BorderSide(color: DeyTokens.darkBorder),
    ),
    clipBehavior: Clip.antiAlias,
    child: InkWell(
      onTap: onTap,
      child: Padding(padding: padding, child: child),
    ),
  );
}

Color severityColor(String severity) => switch (severity) {
  'critical' => DeyTokens.severityCritical,
  'high' => DeyTokens.severityHigh,
  'medium' => DeyTokens.severityMedium,
  _ => DeyTokens.severityLow,
};

IconData severityIcon(String severity) => switch (severity) {
  'critical' => Icons.error_outline,
  'high' => Icons.warning_amber_outlined,
  'medium' => Icons.info_outline,
  _ => Icons.flag_outlined,
};

String severityLabel(String severity) => switch (severity) {
  'critical' => 'Critical',
  'high' => 'High',
  'medium' => 'Medium',
  _ => 'Low',
};

class SeverityChip extends StatelessWidget {
  const SeverityChip(this.severity, {super.key});

  final String severity;

  @override
  Widget build(BuildContext context) {
    final color = severityColor(severity);
    final solid = severity == 'high' || severity == 'critical';
    final foreground = solid ? DeyTokens.onPrimary : color;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: solid ? color : null,
        borderRadius: BorderRadius.circular(DeyTokens.pillRadius),
        border: Border.all(color: color),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(severityIcon(severity), size: 14, color: foreground),
          const SizedBox(width: 4),
          Text(
            severityLabel(severity),
            style: TextStyle(
              color: foreground,
              fontSize: 12,
              fontWeight: FontWeight.w700,
            ),
          ),
        ],
      ),
    );
  }
}

class VerificationBadge extends StatelessWidget {
  const VerificationBadge(this.incident, {super.key});

  final Incident incident;

  @override
  Widget build(BuildContext context) {
    final (icon, text, color) = switch (incident.status) {
      'confirmed' => (
        Icons.verified_outlined,
        'Confirmed by ${incident.confirmedByLabel ?? 'a verifier'}',
        Colors.white,
      ),
      'corroborated' => (
        Icons.groups_outlined,
        'Corroborated by ${incident.corroborationCount} people',
        DeyTokens.darkOnSurface,
      ),
      'resolved' => (
        Icons.check_circle_outline,
        'Resolved',
        DeyTokens.darkSecondaryText,
      ),
      'expired' => (
        Icons.history,
        'Expired',
        DeyTokens.darkSecondaryText,
      ),
      _ => (
        Icons.radio_button_unchecked,
        t('Unverified'),
        DeyTokens.darkSecondaryText,
      ),
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 15, color: color),
        const SizedBox(width: 5),
        Flexible(
          child: Text(
            text,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(color: color, fontSize: 12),
          ),
        ),
      ],
    );
  }
}

class Heading extends StatelessWidget {
  const Heading(this.text, {this.subtitle, super.key});

  final String text;
  final String? subtitle;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        text,
        style: const TextStyle(
          fontSize: 28,
          fontWeight: FontWeight.w700,
          height: 1.2,
          letterSpacing: -0.5,
        ),
      ),
      if (subtitle != null) ...[
        const SizedBox(height: 8),
        Text(subtitle!, style: const TextStyle(color: DeyTokens.darkSecondaryText)),
      ],
    ],
  );
}

class Meta extends StatelessWidget {
  const Meta(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: const TextStyle(color: DeyTokens.darkSecondaryText, fontSize: 13),
  );
}

class CenterMessage extends StatelessWidget {
  const CenterMessage({
    required this.icon,
    required this.title,
    required this.body,
    this.action,
    super.key,
  });

  final IconData icon;
  final String title;
  final String body;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 40, color: DeyTokens.darkSecondaryText),
          const SizedBox(height: 12),
          Text(
            title,
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 6),
          Text(
            body,
            textAlign: TextAlign.center,
            style: const TextStyle(color: DeyTokens.darkSecondaryText),
          ),
          if (action != null) ...[const SizedBox(height: 16), action!],
        ],
      ),
    ),
  );
}

class DeyPage extends StatelessWidget {
  const DeyPage({required this.children, this.title, this.bottom, super.key});

  final String? title;
  final List<Widget> children;
  final Widget? bottom;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      backgroundColor: DeyTokens.darkBackground,
      scrolledUnderElevation: 0,
      title: title == null
          ? null
          : Text(title!, style: const TextStyle(fontWeight: FontWeight.w700)),
    ),
    body: ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
      children: children,
    ),
    bottomNavigationBar: bottom == null
        ? null
        : SafeArea(
            child: Padding(padding: const EdgeInsets.all(16), child: bottom),
          ),
  );
}

String timeAgo(DateTime time) {
  final elapsed = DateTime.now().difference(time);
  if (elapsed.inMinutes < 1) return 'just now';
  if (elapsed.inHours < 1) return '${elapsed.inMinutes}m ago';
  if (elapsed.inDays < 1) return '${elapsed.inHours}h ago';
  return '${elapsed.inDays}d ago';
}

Future<void> call112() => launchUrl(Uri.parse('tel:112'));

void showMessage(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}
