import 'package:flutter/material.dart';

/// Design tokens mirrored from design/stitch/design-system.md.
abstract final class DeyTokens {
  static const Color primary = Color(0xFFA6EF1F);
  static const Color primaryOnLight = Color(0xFF4C7A00);
  static const Color onPrimary = Color(0xFF0A0C0A);
  static const Color accent = Color(0xFFF5A623);
  static const Color danger = Color(0xFFFF3B30);

  static const Color darkBackground = Color(0xFF0A0C0A);
  static const Color darkSurface = Color(0xFF141714);
  static const Color darkBorder = Color(0xFF3A413A);
  static const Color darkOnSurface = Color(0xFFF2F5F3);
  static const Color darkSecondaryText = Color(0xFFB4BCB4);

  static const Color lightBackground = Color(0xFFF7F9F8);
  static const Color lightSurface = Color(0xFFFFFFFF);
  static const Color lightOnSurface = Color(0xFF111614);

  static const Color severityLow = Color(0xFF5B9BD5);
  static const Color severityMedium = Color(0xFFF5A623);
  static const Color severityHigh = Color(0xFFF2711C);
  static const Color severityCritical = Color(0xFFFF3B30);

  static const double cardRadius = 20;
  static const double pillRadius = 999;
  static const double minTapTarget = 48;
  static const String fontFamily = 'Inter';
}
