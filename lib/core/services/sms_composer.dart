import 'package:url_launcher/url_launcher.dart';

String sosMessage({double? lat, double? lng}) {
  if (lat == null || lng == null) {
    return 'SOS. I need help. I could not get my location. Please call me.';
  }
  final latText = lat.toStringAsFixed(5);
  final lngText = lng.toStringAsFixed(5);
  return 'SOS. I need help. My location: $latText, $lngText '
      'https://maps.google.com/?q=$latText,$lngText';
}

Uri smsComposerUri(List<String> phones, String message) => Uri(
  scheme: 'sms',
  path: phones.join(','),
  query: 'body=${Uri.encodeComponent(message)}',
);

Future<bool> openSmsComposer(List<String> phones, String message) =>
    launchUrl(smsComposerUri(phones, message));
