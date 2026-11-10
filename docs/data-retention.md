# Data retention

Enforced nightly by the `purge_retention` worker job. Have counsel confirm these periods.

| Data | Retention |
|------|-----------|
| Corroborations, including device location | 30 days |
| Incident record | 90 days, then deleted |
| SOS location points | 24 hours after the SOS ends |
| Audit log | 24 months |
| Incident media | Deleted with the incident record in the database. Storage files need a matching cleanup job before launch. |
| Device location | Overwritten; no history kept |

Deleting an account removes the profile, devices, and trusted contacts. Reports stay but are no longer linked to the person.
