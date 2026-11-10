# Operations runbook

## SMS provider outage

Sign-in codes and SOS texts sent by the server stop. SOS Lite keeps working because it uses the phone's own SMS app.

1. Check Termii status and account balance.
2. If alert SMS is burning spend, use the kill switch at `/admin/settings`.
3. Tell verifiers that sign-ups and server SOS texts are delayed.

## Push not arriving

1. Check Sentry for FCM errors and confirm `FCM_SERVICE_ACCOUNT_JSON` is valid.
2. Confirm the `worker` container is running and Redis is healthy.
3. Remember that unverified reports never push by design.

## A false report caused harm

1. Mark it false in the admin panel. It leaves every feed immediately.
2. Look up the reporter only if necessary. The lookup needs an admin and a written reason, and is logged.
3. Follow your legal counsel's process for any law-enforcement request.

## Restore drill

Run this on a schedule. A backup you have not restored is not a backup.

1. Restore the latest Supabase backup into a staging project.
2. Point a staging API at it and run `alembic current`.
3. Confirm `/ready` and that a recent incident is present.
