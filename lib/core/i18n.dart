/// English is the source text. Translations are keyed by it, and anything
/// untranslated falls back to English. Pidgin is a draft for native review.
String currentLanguage = 'en';

String t(String english) =>
    currentLanguage == 'pcm' ? (_pidgin[english] ?? english) : english;

const launchLanguages = {'en', 'pcm'};

const _pidgin = <String, String>{
  'Choose your language': 'Pick your language',
  'Continue': 'Continue',
  'Skip': 'Skip am',
  'Next': 'Next',
  'Get Started': 'Make we start',
  'Report what you see': 'Report wetin you see',
  'Stay alert near you': 'Dey alert for your area',
  'Protect your community': 'Protect your community',
  'Enter your phone number': 'Put your phone number',
  'Enter your email': 'Put your email',
  'Watch zones': 'Places wey you dey watch',
  'All reports': 'All reports',
  'All': 'All',
  'Near you': 'Near you',
  'Add a follow-up': 'Add wetin you see',
  'Add a zone': 'Add place',
  'Contact support': 'Talk to us',
  'Send message': 'Send message',
  'Become a verifier': 'Become verifier',
  'Correct my data': 'Fix my information',
  'Check your email': 'Check your email',
  'Send code': 'Send code',
  'Verify phone number': 'Confirm your number',
  'Map': 'Map',
  'Feed': 'Feed',
  'Report': 'Report',
  'Profile': 'Profile',
  'Unverified': 'Dem never confirm am',
  'Corroborated': 'People confirm am',
  'Report Incident': 'Report wetin happen',
  'Submit Report': 'Send report',
  'Report anonymously': 'Hide my name',
  'Hidden from other users': 'Other people no go see',
  'Describe what happened and where. Do not name people or groups.':
      'Talk wetin happen and where. No call person or group name.',
  'Hold for 3 seconds': 'Hold am for 3 seconds',
  'Cancel': 'Cancel',
  'Call 112': 'Call 112',
  'Corroborate': 'I see am too',
  'Flag': 'Flag am',
  'All quiet in your area': 'Everywhere quiet for your area',
  "You're offline": 'Network no dey',
  'Retry now': 'Try again',
  'Report sent': 'Report don go',
  'Saved. Will send when you are online':
      'E don save. E go send when network come',
  'Settings': 'Settings',
  'Sign out': 'Comot',
  "I'm safe": 'I dey safe',
};
