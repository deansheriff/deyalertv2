# Launch checklist

The code is complete for an Android pilot. These steps need your accounts, a real phone, or a professional.

## 1. Accounts and keys

- [x] **Supabase:** self-hosted on Coolify at `https://supabase.sherpackage.com`. It signs sessions with a shared secret, so set the backend's `SUPABASE_JWT_SECRET` to the Supabase service's `JWT_SECRET` and leave `SUPABASE_JWKS_URL` empty. Also copy the service role key.
- [ ] **Supabase phone auth:** phone sign-in is on. Set these on the Supabase auth service in Coolify:
  - `GOTRUE_SMS_AUTOCONFIRM=false`. It is currently on, which lets anyone sign up with any number and a password, skipping the code.
  - While testing without Termii: `GOTRUE_SMS_TEST_OTP=<your number without +>:<6 digits>`. Remove it before launch.
  - Once Termii is approved: `GOTRUE_HOOK_SEND_SMS_ENABLED=true`, `GOTRUE_HOOK_SEND_SMS_URI=https://<api-host>/v1/auth/sms-hook`, and `GOTRUE_HOOK_SEND_SMS_SECRETS=v1,whsec_<base64 secret>`. Put the same secret in the backend's `SUPABASE_SMS_HOOK_SECRET`.
- [ ] **Termii:** create the account, register sender ID `DeyAlert`, and fund it. Approval takes time, so start first.
- [ ] **Firebase:** create the project and Android app `ng.deyalert.dey_alert`. Put `google-services.json` in `android/app/` and add the Google Services Gradle plugin. Copy the service account JSON.
- [ ] **Google Maps:** create an Android key restricted to the package name and your SHA-1.
- [ ] **Sentry:** optional, one project each for backend and Flutter.

## 2. Deploy the backend

- [ ] In Coolify, create a Docker Compose app from `docker-compose.prod.yml` and set every variable in `.env.example`. Until Termii and Firebase are set up, use `ENVIRONMENT=staging`: sign-in codes and alerts are logged instead of sent. Switch to `production` once those keys are in; it then refuses to start if any are missing.
- [ ] Set `DATABASE_URL` to the Supabase Postgres connection, in `postgresql+asyncpg://postgres:<password>@<host>:5432/postgres` form. With both on the same Coolify server, use Postgres's internal Docker hostname rather than a public address.
- [ ] Generate `ADMIN_SESSION_SECRET`. The API refuses to start with a weak value or a missing key.
- [ ] Put Cloudflare Tunnel in front of `api`, and add WAF and rate rules for `/v1/auth/*` and `/admin/*`.
- [ ] Confirm `https://<api-host>/ready` returns 200.

## 3. Load your pilot area

- [ ] Choose the pilot LGA and partner.
- [ ] Get LGA and ward boundaries, checking the licence, then run `data.import_boundaries`.
- [ ] Without boundaries, reports still work, but wards are not set and anonymous reports fall back to coarse rounding.

## 4. Create staff

- [ ] In the api container, run `python -m data.seed_admin you@example.com`. It prints a one-time password. At first sign-in on `/admin/login` the panel makes you choose a new password. Admin sign-in is email and password only, so use a long, unique password.
- [ ] To give an app user a staff role, have them sign in on the app, then run `python -m data.make_staff +234... moderator`.
- [ ] Add verifiers at `/admin/verifiers`.

## 5. Build and test the app

```bash
flutter build appbundle --release --dart-define=SUPABASE_URL=... --dart-define=SUPABASE_ANON_KEY=... --dart-define=API_BASE_URL=https://... -PMAPS_API_KEY=...
```

- [ ] Create an upload keystore and `android/key.properties`. Release builds are unsigned without it.
- [ ] Run the manual matrix in plan §11.2 on a real 2 GB Tecno, Infinix, or itel phone over 3G: push with the screen off, offline report then reconnect, SOS composer with no data.
- [ ] Run SOS drills with pilot users.
- [ ] Load test fan-out against staging.

## 6. Legal and store

- [ ] Have counsel write the privacy policy and terms, then replace the placeholder in `LegalScreen`.
- [ ] Complete the DPIA and check NDPC registration.
- [ ] Confirm 112 works in the pilot state.
- [ ] Have a native speaker review the Pidgin strings in `lib/core/i18n.dart`.
- [ ] Submit the Play Console location declaration, using the wording in `BackgroundDisclosureScreen`. Review takes time.

## Not built yet

| Item | Why |
|------|-----|
| iOS release | Needs a Mac, APNs keys, and the Maps key in `AppDelegate`. Android is the pilot platform. |
| Hausa, Yoruba, Igbo | Need native translators. Shown as "Coming soon". |
| WhatsApp and voice OTP (DA-M37) | Planned for Phase 4; needs a provider account. |
| Map address search | Needs the paid Places API. Pin and landmark text are used instead. |
| Play Integrity at sign-up | Needs Play Console and a verification endpoint. |
| Automatic graphic-image detection | None exists. All photos are blurred until a moderator approves or the viewer taps. |
| MapLibre offline maps | Phase 4 evaluation. |
