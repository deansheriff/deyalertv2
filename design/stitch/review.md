# Design review: Dey Alert v3

Reviewed 2026-11-09 from Stitch screenshots against the §12.6 checklist and the plan. No screen is approved yet.

| Verdict | Meaning | Count |
|---------|---------|-------|
| Blocker | False or risky claim about safety, privacy, or law, or a checklist failure | 23 |
| Fix | Wrong detail, missing state, or layout defect | 23 |
| Pass | Ready for your approval; any note is optional | 7 |

**Not checkable from screenshots:** exact 48 dp tap targets and sunlight contrast. The Hausa, Yoruba, and Igbo strings on DA-M01 need a native speaker.

## Patterns across screens

1. **Invented SMS fallback.** Many screens promise offline or automatic SMS that does not exist. SOS Lite opens the SMS app and the person taps send. Server SMS needs a connection, and SMS alert fallback is opt-in and Phase 4.
2. **Invented organisations and infrastructure.** "CDA", "Lagos Mesh Network", ward patrol, "Command Center", cordons, and dispatch imply partners and a response service the plan does not have.
3. **Invented legal and security claims.** "NDPA Certified", "NDPR Compliant", "end-to-end encrypted", statute references, and automatic reports to the Ombudsman or police.
4. **Wrong numbers.** Corroboration radius, alert radius, description length, and incident lifetimes differ from the plan.
5. **Incident types outside the twelve.** Armed Intrusion, Transformer, and Noise Dispute appear on DA-M11, M12, M16, W02, and W10.
6. **Inconsistent components.** The admin sidebar changes on every screen, onboarding step counters conflict, High severity reads amber rather than `#F2711C` on mobile, and OTP boxes are square on M04 and round on M37.

Cause: the batch prompts used a shortened preamble that dropped most accuracy rules.

## Decisions

All four were accepted on 2026-11-09. Decision 1 removes the hourly background task from plan §5.4.

| # | Question | Decision |
|---|----------|----------------|
| 1 | §5.4 plans hourly background location for alert targeting, but DA-M28 must say background location is used only during SOS or a check-in. Both cannot be true, and this is what Google Play reviews. | Drop the hourly task. Update location when the app is open, else use the home point. |
| 2 | Should screens name "CDA" and vigilantes? No pilot partner is chosen. | Use "verifier" and "community leader". |
| 3 | Should moderators see true coordinates on DA-W04? The checklist says nothing shows them. | Show the public location; reveal the true point only through a logged action. |
| 4 | DA-M26 says verifier notes are public, but the public timeline excludes notes. | Keep notes private to verifiers and moderators. |

## Fix progress

Findings below describe the screens as first generated. Edits were paused on 2026-11-09 because downloaded files still held the old text.

| State | Screens |
|-------|---------|
| Stitch reported the edit as applied; not confirmed in the files | DA-M05, M08, M15, M16, M17, M19 no-contacts, M24, M28, M30, M31, P01, P02, P04 |
| Edit timed out; outcome unknown | DA-M33, P05, W01, W03, W04, W05 |
| Blockers not yet attempted | DA-W06, W07, W09, W10, W11 |
| Fix screens not yet attempted | All 23 |

## Findings by screen

| Screen | Verdict | Findings |
|--------|---------|----------|
| DA-M01 | Fix | Remove "Emergency SMS fallbacks work... without data". Step counter conflicts with M03, M04, M08. |
| DA-M02 | Fix | Remove "Verified community reports". Only slide 1 is shown; Get Started state missing. Desktop canvas. |
| DA-M03 | Fix | Remove "contact your CDA secretary". Consent says the number is only for broadcasts, but it is used to sign in. |
| DA-M04 | Fix | "Without airtime charge" contradicts M03. Phase 4 WhatsApp and call options shown. Wrong-code and success states missing. |
| DA-M05 | Blocker | Says 1 km (default 5). "Vigilantes see the exact street" conflicts with fuzzing. False "Works offline via SMS fallback. Zero constant background tracking". Debug label left in. Desktop canvas. |
| DA-M06 | Fix | Says 1 km. Invented curfew and CDA notices. |
| DA-M07 | Pass | Check the vendor settings paths on real phones. |
| DA-M08 | Blocker | False "stored on-device and never shared"; the home point is stored on the server. Invented claim about who sees the name. Name marked required but is optional. |
| DA-M09 | Pass | Desktop canvas; crop on export. |
| DA-M10 | Fix | Legend covers the Akerele Junction label. Area name truncated. Desktop canvas. |
| DA-M11 | Fix | Incident types are not the twelve. Radius stops at 10 km (max 20). Backdrop is not a dimmed map. |
| DA-M12 | Fix | Types outside the twelve. Time and distance still wrap on some cards. Two lime elements. |
| DA-M13 | Fix | Limit shows 240 characters (plan 1,000). Invented "Fast CDA" and two-item media limit. |
| DA-M14 | Fix | "House numbers are blurred" applies only to anonymous or sensitive reports. "Responder" implies a dispatch service. Invented "Verified Road". |
| DA-M15 | Blocker | Says the report is sent to residents within 1.5 km, but unverified reports are never pushed. Invented "secure mesh node" and SMS fallback. |
| DA-M16 | Blocker | False "End-to-end encrypted dispatch". "Cordon Active" and patrol events imply an official response. Confirmed banner truncated. |
| DA-M17 | Blocker | Range says 500 m (plan 2 km). False "responders act immediately on 3 live confirmations". Too-far and location-off states not visible. Extra unrequested button. |
| DA-M18 | Fix | Confirmation state missing. |
| DA-M19 | Fix | Button is not low in thumb reach. Red reads pink, not `#FF3B30`. **No-contacts state is a Blocker:** false "Works without data or airtime bundle". |
| DA-M20 | Pass | Optional: "Live GPS pin" should be "GPS pin". |
| DA-M21 | Fix | "Live coordinates" and "automated" do not describe SOS Lite. Invented five-contact limit. Empty state missing. |
| DA-M22 | Fix | Remove the WhatsApp line. |
| DA-M23 | Fix | Remove "Connected to Ward 2 Network" and "CDA Verified". Radius applies from the device location first. Desktop canvas. |
| DA-M24 | Blocker | False "Urgent alerts will queue and deliver via SMS fallback" while offline. "Monitored live" overclaims. Radius shows 2.5 km. |
| DA-M25 | Pass | |
| DA-M26 | Fix | Note visibility depends on decision 4. |
| DA-M27 | Fix | Invented dispatch to EKEDC. Flooding lifetime shown as 24 h (plan 12 h). |
| DA-M28 | Blocker | "Zero passive tracking" depends on decision 1. Pings are kept 24 h, so "zero movement logs" is false. Buttons should be Agree and No thanks. |
| DA-M29 | Fix | Remove "GSM SMS Fallback Active" and "Live Beacon". |
| DA-M30 | Blocker | False "Works offline via SMS fallback" and "SMS will dispatch if network data drops". Invented "Lagos traffic factored". |
| DA-M31 | Blocker | Same false offline claim. Check-in alerts go to trusted contacts only. |
| DA-M32 | Pass | Desktop canvas; crop on export. |
| DA-M33 | Blocker | "Critical sirens bypass silent mode" is unconfirmed on either platform. Background claim depends on decision 1. Quiet hours and radius wording are wrong. |
| DA-M34 | Pass | |
| DA-M35 | Fix | Only confirmed critical alerts bypass quiet hours. Invented "community chat" and day picker. Toggle renders incorrectly. |
| DA-M36 | Pass | Deletion wording matches the plan. |
| DA-M37 | Fix | False "No airtime charge" and "end-to-end encrypted". Blue focus ring is off-token. |
| DA-P01 | Blocker | "Public Beacon" is wrong and alarming on a private link. "Target subscriber". Battery level is not collected. Invented compliance and purge claims. |
| DA-P02 | Blocker | Brief requires no sensitive details, but it shows session, ward, and reason. Toll-free claim. Invented NDPA and encryption claims. |
| DA-P03 | Fix | Remove "NDPA Compliant". Do not reveal the requester's ward. Desktop canvas. |
| DA-P04 | Blocker | Invented toll-free shortcode 32001, REVOKE command, and "Zero-data" delivery. Desktop canvas. |
| DA-P05 | Blocker | Brief asked for placeholder text. Contains invented legal terms, contact details, and a 30-day purge. Do not reuse any of this text. |
| DA-W01 | Blocker | "Official Government & CDA Personnel Only" and a `gov.ng` address imply a government link. Debug link left in. MFA and locked states not visible. "Remember device" weakens MFA. |
| DA-W02 | Fix | "Real-time surveillance", response-time metrics, and Command Center language. Inconsistent sidebar. Header clipped. |
| DA-W03 | Blocker | Unrequested "Batch Verify & Broadcast" bypasses case-by-case review. Wrong lifecycle copy. Action column clipped. |
| DA-W04 | Blocker | True coordinates shown; see decision 3. "Cordon Active". "Redacted" and "Critical High" are not valid states. |
| DA-W05 | Blocker | Invented legal section, 7-year retention (plan 24 months), and live transmission to an Ombudsman. Reveals more than the reporter's identity. |
| DA-W06 | Blocker | Requests NIN documents, which the plan does not collect. Implies police and council approvers. Duplicate plus in the Add button. |
| DA-W07 | Blocker | Exact "Trust Score 98.4%"; other screens use bands. Invented background-check attestation and patrol data. |
| DA-W08 | Fix | Roles must be member, verifier, moderator, admin. Remove the police title. |
| DA-W09 | Blocker | Invented automatic reports to the Ombudsman, NDPC, and police, plus Merkle and certification claims. IP column missing and table clipped. |
| DA-W10 | Blocker | Lifetimes and categories contradict the plan. Fallback text conflicts with opt-in SMS. Two Save buttons. |
| DA-W11 | Blocker | "9mobile DND Bypass" implies evading do-not-disturb rules. Invented WhatsApp throttle. Top bar wraps and chart labels collide. |
