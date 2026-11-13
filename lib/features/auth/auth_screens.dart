import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../core/i18n.dart';
import '../../core/theme/tokens.dart';
import '../../core/ui.dart';
import '../../models/models.dart';
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

String? normalizeEmail(String input) {
  final email = input.trim().toLowerCase();
  return RegExp(r'^[^@\s]+@[^@\s]+\.[a-z]{2,}$').hasMatch(email) ? email : null;
}

class SignInScreen extends ConsumerStatefulWidget {
  const SignInScreen({super.key});

  @override
  ConsumerState<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends ConsumerState<SignInScreen> {
  final _input = TextEditingController();
  bool _usePhone = false;
  bool _agreed = false;
  bool _busy = false;
  String? _error;

  SignInTarget? _target() {
    if (_usePhone) {
      final phone = normalizeNigerianPhone(_input.text);
      return phone == null ? null : SignInTarget.phone(phone);
    }
    final email = normalizeEmail(_input.text);
    return email == null ? null : SignInTarget.email(email);
  }

  Future<void> _send() async {
    final target = _target();
    if (target == null) {
      setState(
        () => _error = _usePhone
            ? 'Enter a valid Nigerian mobile number.'
            : 'Enter a valid email address.',
      );
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref.read(repositoryProvider).sendCode(target);
      if (mounted) unawaited(context.push('/verify', extra: target));
    } on AuthException catch (error) {
      setState(
        () => _error = error.statusCode == '429'
            ? 'Too many attempts. Try again later.'
            : 'We could not send a code. Try again.',
      );
    } on TimeoutException {
      setState(() => _error = 'This is taking too long. Check your connection and try again.');
    } on Exception {
      setState(() => _error = 'Could not reach Dey Alert. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _switchMethod() => setState(() {
    _usePhone = !_usePhone;
    _input.clear();
    _error = null;
  });

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // Email always works; the server says whether admins have switched phone on.
    final phoneAllowed = ref.watch(signInMethodsProvider).value?.contains('phone') ?? false;
    final usePhone = _usePhone && phoneAllowed;
    return DeyPage(
      bottom: PillButton(
        t('Send code'),
        glow: true,
        busy: _busy,
        onPressed: _agreed ? _send : null,
      ),
      children: [
        Heading(
          usePhone ? t('Enter your phone number') : t('Enter your email'),
          subtitle: usePhone
              ? 'We send a code by SMS to confirm this is your number.'
              : 'We send a 6-digit code to confirm this is your email.',
        ),
        const SizedBox(height: 24),
        TextField(
          key: ValueKey(usePhone),
          controller: _input,
          keyboardType: usePhone ? TextInputType.phone : TextInputType.emailAddress,
          autocorrect: false,
          autofillHints: [if (usePhone) AutofillHints.telephoneNumber else AutofillHints.email],
          decoration: InputDecoration(
            prefixText: usePhone ? '+234  ' : null,
            hintText: usePhone ? '802 345 6789' : 'you@example.com',
            errorText: _error,
          ),
        ),
        if (phoneAllowed)
          TextButton(
            style: TextButton.styleFrom(alignment: Alignment.centerLeft, padding: EdgeInsets.zero),
            onPressed: _switchMethod,
            child: Text(usePhone ? 'Use email instead' : 'Use phone number instead'),
          ),
        const SizedBox(height: 12),
        CheckboxListTile(
          contentPadding: EdgeInsets.zero,
          controlAffinity: ListTileControlAffinity.leading,
          value: _agreed,
          onChanged: (value) => setState(() => _agreed = value ?? false),
          title: const Text('I agree to the Terms of Service and Privacy Policy.'),
        ),
        if (usePhone) const Meta('Standard SMS rates may apply.'),
      ],
    );
  }
}

class VerifyCodeScreen extends ConsumerStatefulWidget {
  const VerifyCodeScreen({required this.target, super.key});

  final SignInTarget target;

  @override
  ConsumerState<VerifyCodeScreen> createState() => _VerifyCodeScreenState();
}

class _VerifyCodeScreenState extends ConsumerState<VerifyCodeScreen> {
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
      await ref.read(repositoryProvider).verifyCode(widget.target, _code.text);
    } on AuthException {
      setState(() => _error = 'That code is not right. Try again.');
    } on Exception {
      setState(() => _error = 'Could not reach Dey Alert. Check your connection.');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resend() async {
    await ref.read(repositoryProvider).sendCode(widget.target);
    setState(_startTimer);
  }

  @override
  void dispose() {
    _timer?.cancel();
    _code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final phone = widget.target.isPhone;
    return DeyPage(
      bottom: PillButton(
        'Verify and continue',
        glow: true,
        busy: _busy,
        onPressed: _code.text.length == 6 ? _verify : null,
      ),
      children: [
        Heading(
          phone ? t('Verify phone number') : t('Check your email'),
          subtitle: 'Enter the 6-digit code sent to ${widget.target.value}.',
        ),
        TextButton(
          style: TextButton.styleFrom(alignment: Alignment.centerLeft, padding: EdgeInsets.zero),
          onPressed: () => context.pop(),
          child: Text(phone ? 'Change number' : 'Change email'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _code,
          autofocus: true,
          maxLength: 6,
          keyboardType: TextInputType.number,
          textAlign: TextAlign.center,
          autofillHints: const [AutofillHints.oneTimeCode],
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
        Text(
          phone
              ? 'Standard SMS rates may apply.'
              : 'No email? Check your spam or promotions folder.',
          style: const TextStyle(color: DeyTokens.darkSecondaryText, fontSize: 12),
        ),
      ],
    );
  }
}
