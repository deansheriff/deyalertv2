import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/i18n.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../providers/providers.dart';

String? normalizeNigerianPhone(String input) {
  final digits = input.replaceAll(RegExp(r'\D'), '');
  final local = digits.startsWith('234')
      ? digits.substring(3)
      : digits.startsWith('0')
      ? digits.substring(1)
      : digits;
  if (!RegExp(r'^[789][01]\d{8}$').hasMatch(local)) return null;
  return '+234$local';
}

class PhoneScreen extends ConsumerStatefulWidget {
  const PhoneScreen({super.key});

  @override
  ConsumerState<PhoneScreen> createState() => _PhoneScreenState();
}

class _PhoneScreenState extends ConsumerState<PhoneScreen> {
  final _phone = TextEditingController();
  bool _agreed = false;
  bool _busy = false;
  String? _error;

  Future<void> _send() async {
    final phone = normalizeNigerianPhone(_phone.text);
    if (phone == null) {
      setState(() => _error = 'Enter a valid Nigerian mobile number.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(repositoryProvider).sendCode(phone);
      if (mounted) unawaited(context.push('/otp', extra: phone));
    } on AuthException catch (error) {
      setState(
        () => _error = error.statusCode == '429'
            ? 'Too many attempts. Try again later.'
            : 'We could not send a code. Try again.',
      );
    } on Exception {
      setState(() => _error = 'Could not reach Dey Alert. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    bottom: PillButton(
      t('Send code'),
      glow: true,
      busy: _busy,
      onPressed: _agreed ? _send : null,
    ),
    children: [
      Heading(
        t('Enter your phone number'),
        subtitle: 'We send a code by SMS to confirm this is your number.',
      ),
      const SizedBox(height: 24),
      TextField(
        controller: _phone,
        keyboardType: TextInputType.phone,
        decoration: InputDecoration(
          prefixText: '+234  ',
          hintText: '802 345 6789',
          errorText: _error,
        ),
      ),
      const SizedBox(height: 12),
      CheckboxListTile(
        contentPadding: EdgeInsets.zero,
        controlAffinity: ListTileControlAffinity.leading,
        value: _agreed,
        onChanged: (value) => setState(() => _agreed = value ?? false),
        title: const Text('I agree to the Terms of Service and Privacy Policy.'),
      ),
      const Meta('Standard SMS rates may apply.'),
    ],
  );
}

class OtpScreen extends ConsumerStatefulWidget {
  const OtpScreen({required this.phone, super.key});

  final String phone;

  @override
  ConsumerState<OtpScreen> createState() => _OtpScreenState();
}

class _OtpScreenState extends ConsumerState<OtpScreen> {
  final _code = TextEditingController();
  Timer? _timer;
  int _seconds = 60;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _startTimer();
  }

  void _startTimer() {
    _seconds = 60;
    _timer?.cancel();
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_seconds == 0) {
        timer.cancel();
      } else {
        setState(() => _seconds--);
      }
    });
  }

  Future<void> _verify() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(repositoryProvider).verifyCode(widget.phone, _code.text);
    } on AuthException {
      setState(() => _error = 'That code is not right. Try again.');
    } on Exception {
      setState(() => _error = 'Could not reach Dey Alert. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resend() async {
    await ref.read(repositoryProvider).sendCode(widget.phone);
    setState(_startTimer);
  }

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => DeyPage(
    bottom: PillButton(
      'Verify and continue',
      glow: true,
      busy: _busy,
      onPressed: _code.text.length == 6 ? _verify : null,
    ),
    children: [
      Heading(
        t('Verify phone number'),
        subtitle: 'Enter the 6-digit code sent to ${widget.phone}.',
      ),
      TextButton(
        style: TextButton.styleFrom(
          alignment: Alignment.centerLeft,
          padding: EdgeInsets.zero,
        ),
        onPressed: () => context.pop(),
        child: const Text('Change number'),
      ),
      const SizedBox(height: 12),
      TextField(
        controller: _code,
        autofocus: true,
        maxLength: 6,
        keyboardType: TextInputType.number,
        textAlign: TextAlign.center,
        inputFormatters: [FilteringTextInputFormatter.digitsOnly],
        onChanged: (_) => setState(() {}),
        style: const TextStyle(
          fontSize: 28,
          letterSpacing: 14,
          fontFeatures: [FontFeature.tabularFigures()],
        ),
        decoration: InputDecoration(counterText: '', errorText: _error),
      ),
      const SizedBox(height: 16),
      if (_seconds > 0)
        Meta('Resend code in 00:${_seconds.toString().padLeft(2, '0')}')
      else
        TextButton(onPressed: _resend, child: const Text('Resend code')),
      const SizedBox(height: 8),
      const Text(
        'Standard SMS rates may apply.',
        style: TextStyle(color: DeyTokens.darkSecondaryText, fontSize: 12),
      ),
    ],
  );
}
