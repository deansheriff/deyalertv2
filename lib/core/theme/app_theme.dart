import 'package:flutter/material.dart';

import 'tokens.dart';

abstract final class AppTheme {
  static final ThemeData dark = _build(
    brightness: Brightness.dark,
    primary: DeyTokens.primary,
    onPrimary: DeyTokens.onPrimary,
    background: DeyTokens.darkBackground,
    surface: DeyTokens.darkSurface,
    onSurface: DeyTokens.darkOnSurface,
  );

  static final ThemeData light = _build(
    brightness: Brightness.light,
    primary: DeyTokens.primaryOnLight,
    onPrimary: Colors.white,
    background: DeyTokens.lightBackground,
    surface: DeyTokens.lightSurface,
    onSurface: DeyTokens.lightOnSurface,
  );

  static ThemeData _build({
    required Brightness brightness,
    required Color primary,
    required Color onPrimary,
    required Color background,
    required Color surface,
    required Color onSurface,
  }) {
    final colorScheme = ColorScheme.fromSeed(
      seedColor: primary,
      brightness: brightness,
      primary: primary,
      onPrimary: onPrimary,
      secondary: DeyTokens.accent,
      onSecondary: Colors.black,
      error: DeyTokens.danger,
      surface: surface,
      onSurface: onSurface,
    );
    return ThemeData(
      colorScheme: colorScheme,
      scaffoldBackgroundColor: background,
      fontFamily: DeyTokens.fontFamily,
      materialTapTargetSize: MaterialTapTargetSize.padded,
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: surface,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 20,
          vertical: 16,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
          borderSide: const BorderSide(color: DeyTokens.darkBorder),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
          borderSide: const BorderSide(color: DeyTokens.darkBorder),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(DeyTokens.cardRadius),
          borderSide: BorderSide(color: primary),
        ),
      ),
      switchTheme: SwitchThemeData(
        thumbColor: WidgetStateProperty.resolveWith(
          (states) => states.contains(WidgetState.selected)
              ? onPrimary
              : DeyTokens.darkSecondaryText,
        ),
        trackColor: WidgetStateProperty.resolveWith(
          (states) =>
              states.contains(WidgetState.selected) ? primary : surface,
        ),
      ),
    );
  }
}
