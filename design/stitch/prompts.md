# Stitch prompts

Each prompt is the preamble, then the device line for its group, then the screen prompt.
`manifest.json` is built from these tables. IDs are permanent.

## Preamble

```
App: Dey Alert, a community security alert app for Nigerian neighbourhoods.
Style: modern, sophisticated, and restrained. Near-black background #0A0C0A, cards #141714 with a 1px #3A413A hairline border and 20px corners. Neon lime #A6EF1F is reserved for the one main action and the active tab, with near-black text on lime. Warm amber #F5A623 is used sparingly for attention. Inter font with a strong type hierarchy: large tight headlines, quiet metadata in #B4BCB4, tabular numerals. Generous spacing on an 8px grid. One consistent thin line-icon set. Flat surfaces, no decorative gradients, no emoji, no stacks of badges. Buttons, chips, and search fields are pill-shaped. A soft glow appears only around the one main action on a screen.
Bottom navigation is a floating pill bar with five tabs, each with an icon and a short text label: Map, Feed, Report, SOS, Profile. The active tab has a lime pill behind it. Each screen has one main action; never repeat SOS or Report as header or floating buttons.
Must stay readable in strong sunlight. Large tap targets (minimum 48dp). Icon-first with short plain-English copy that translates well to Pidgin, Hausa, Yoruba and Igbo.
Severity is shown by colour plus icon plus text label. Verification is shown by pin outline or badge (dashed = unverified, solid = corroborated, solid with check = confirmed by a verifier). Never rely on colour alone. Unverified reports are always labelled "Unverified" in calm, non-alarming wording.
Lime is for brand and action buttons only, never on an incident's severity or verification badge. The SOS button is always red, never lime. A confirmed incident says "Confirmed by [title], [ward]".
Show only what the app really does. SOS opens the phone's own SMS app with a pre-filled message after a 10 second cancel window. It needs airtime, does not send silently, and has no alarm sound or relay service. "Report anonymously" hides the reporter from other users; never say the identity is not recorded.
Severity labels are exactly Low, Medium, High, Critical; High and Critical chips are solid with dark text. Confirmed badges are white on a neutral surface. Unverified items look visibly quieter than confirmed ones.
Add no features beyond this brief: no comments, no WhatsApp or share buttons, no "Verify" button for ordinary members. Never show a toll-free claim.
Add no organisations, statistics, or legal claims. Say "verifier" or "community leader"; never mention CDA, vigilantes, mesh networks, patrols, cordons, dispatch, or NDPA or NDPR compliance. No automatic or offline SMS exists. Background location is used only during SOS or a check-in.
Overlays must not cover map pins or their labels, and map controls use one row at the top. Generate only this one screen. The title below is metadata; do not print it in the interface.
```

## Device lines

- **mobile:** `Device: Android phone, 360 dp wide, portrait.`
- **desktop:** `Device: desktop web, 1440 px wide, left sidebar navigation, dense tables, same design tokens.`
- **public-web:** `Device: lightweight mobile web page, no login, minimal assets, must work on slow 3G.`

## Mobile app (device: mobile)

| ID | Screen | Phase | Prompt |
|----|--------|-------|--------|
| DA-M01 | Language picker | 1 | Full-screen list of five languages (English, Nigerian Pidgin, Hausa, Yoruba, Igbo) as large selectable cards showing native names, with a Continue button at the bottom. |
| DA-M02 | Onboarding | 1 | Three-slide carousel: "Report what you see", "Stay alert near you", "Protect your community". Large icon illustration per slide, pagination dots, Skip link, Get Started button on the last slide. |
| DA-M03 | Phone entry | 1 | Phone number field with +234 prefix, short note on why the number is needed, Send code button, terms and privacy consent checkbox. Include inline error for an invalid number and a "too many attempts, try later" state. |
| DA-M04 | OTP entry | 1 | Six separate digit boxes, resend countdown timer, Change number link, error state for wrong code, success state. |
| DA-M05 | Location permission rationale | 1 | Explains in two short lines why location is needed (report where it happened, alerts near you). Allow location primary button, Not now secondary. Include a denied state with an Open settings button. |
| DA-M06 | Notification permission rationale | 1 | Explains that alerts only work with notifications on. Allow notifications primary button, Not now secondary. Include a denied state. |
| DA-M07 | Battery optimisation guide | 1 | Step-by-step guide to stop the phone from killing the app, with tabs for Tecno, Infinix, itel, Samsung, Xiaomi. Numbered steps with small settings-screen illustrations and an "I've done this" button. |
| DA-M08 | Profile setup | 1 | Name field, home area chosen by map pin or search, alert radius slider from 1 to 20 km with a small map showing the radius circle live, Save button. |
| DA-M09 | Home shell | 1 | Bottom navigation: Map, Feed, Report, SOS, Profile. Show the Map tab active. |
| DA-M10 | Map view | 1 | Full-screen map as the hero, with lighter readable roads. One slim top row with the area name and a filter icon button. Incident pins (severity colour, verification outline), cluster bubbles, my-location button, and a small offline chip saying "Last updated 12 min ago". No bottom card until a pin is tapped. |
| DA-M11 | Map filter sheet | 1 | Bottom sheet with incident type multi-select grid, status options (Unverified, Corroborated, Confirmed), radius, and time window (1h, 6h, 24h, 7 days). Apply and Reset buttons. |
| DA-M12 | Incident feed | 1 | Compact scrolling card list with five or six cards visible. Each card: incident type icon in a ring (dashed when unverified), title, time ago and distance on one line, severity chip and verification label on one row, one-line description preview. No action buttons on cards. Filter and sort row at the top. |
| DA-M13 | Report incident | 1 | Grid of twelve incident types with icons (kidnapping, armed robbery, banditry, cult clash, gunshots, mob action, roadblock, fire, flooding, accident, suspicious activity, other). Below: description box with helper text "Describe what happened and where. Do not name people or groups.", location row, photo and video row, Report anonymously toggle, Submit button, small Call 112 link. |
| DA-M14 | Report location picker | 1 | Map with a draggable centre pin, search bar, Use my location button, landmark text field, Confirm location button. |
| DA-M15 | Report sent / queued | 1 | Two variants. Sent: check icon, "Report sent", what happens next. Queued offline: clock icon, "Saved. Will send when you are online", Retry now button, queue count. |
| DA-M16 | Incident detail | 1 | Hero map, status banner ("Unverified report" or "Confirmed by Community Chairman, Ward X"), type, severity, time, description, media carousel with blurred graphic images and tap to view, Corroborate and Flag buttons, vertical timeline of events. |
| DA-M17 | Corroborate sheet | 1 | Bottom sheet "Are you near this and can you confirm it?" with a distance check. Variants: allowed (Confirm button), too far away (explanation, button disabled), location off. |
| DA-M18 | Flag sheet | 1 | Bottom sheet with reason options (false report, duplicate, spam, inappropriate, names an individual), optional notes, Submit button, confirmation state. |
| DA-M19 | SOS Lite | 1 | Large flat red SOS button placed low within thumb reach, with a refined progress ring that fills while held for 3 seconds. Text "Hold for 3 seconds". One line: "Opens your SMS app after 10 seconds. Uses airtime. Works without internet." Call 112 button and one row "3 contacts" with Manage. No status pills and no full contact list. Separate state when no contacts exist: "Add a contact so SOS can reach someone" with an Add contact button, and Call 112 still available. |
| DA-M20 | SOS cancel window | 1 | Full-screen 10 second countdown "Sending to your contacts", very large Cancel button, text explaining the SMS composer opens next. |
| DA-M21 | Trusted contacts | 1 | List of contacts with name, phone, relationship and a consent status chip (Pending, Accepted). Add contact button, empty state, swipe to delete. |
| DA-M22 | Add or edit contact | 1 | Modal with name, phone, relationship, and a short explanation that the contact will get a one-time message asking permission. Save button. |
| DA-M23 | Profile tab | 1 | Name and home area header, alert radius, language, My reports row, Settings row, Sign out. |
| DA-M24 | Empty and error states | 1 | Four compact screens: no incidents nearby (calm message), no connection (offline with cached data note), something went wrong with Retry, loading skeleton for the feed. |
| DA-M25 | Notifications | 2 | List of past alerts with incident type icon, title, time, read and unread styling, tap to open the incident. Empty state. |
| DA-M26 | Verifier actions | 2 | Bottom sheet for community verifiers with Confirm, Resolve, Extend (1h, 3h, 6h), Mark as false, and a note field. Show a "Verified as Community Chairman, Ward X" header. |
| DA-M27 | My reports | 2 | List of the user's reports with outcome chips (Corroborated, Confirmed, Resolved, Expired, Marked false) and a short explanation of each outcome. No public score shown. |
| DA-M28 | Background location disclosure | 3 | Prominent full-screen disclosure explaining that location is used in the background only during SOS or a check-in, what is shared and with whom, with Agree and No thanks buttons. Written for Google Play review. |
| DA-M29 | SOS active | 3 | Live sharing screen: pulsing indicator, "Sharing your location with 3 contacts", last update time, I'm safe button, Cancel SOS button, Call 112 button. |
| DA-M30 | Safe check-in setup | 3 | "Tell me when I'm home safe": duration picker (30 min, 1h, 2h, custom), who gets alerted, Start button. |
| DA-M31 | Check-in overdue prompt | 3 | Full-screen "Are you OK?" with a 60 second countdown, large I'm safe button, and "Alert my contacts now" button. |
| DA-M32 | SOS resolved | 3 | Confirmation that contacts have been told you are safe, button to return home, option to share feedback. |
| DA-M33 | Settings | 4 | Grouped settings: Notifications, Alert radius, Quiet hours, Language, Data saver, Privacy, About. |
| DA-M34 | Language and data saver | 4 | Language list and a Data saver toggle with a short explanation of what it changes (thumbnails only, less map refresh). |
| DA-M35 | Quiet hours | 4 | Start and end time pickers, note that confirmed critical alerts still come through, Save button. |
| DA-M36 | Export and delete account | 4 | Two sections: Download my data, and Delete my account with a clear explanation of what is removed and a confirmation step. |
| DA-M37 | OTP fallback options | 4 | Variant of the OTP screen offering "Send code by WhatsApp" and "Call me with the code" after the resend timer ends. |

## Admin web panel (device: desktop)

| ID | Screen | Phase | Prompt |
|----|--------|-------|--------|
| DA-W01 | Sign-in and MFA | 2 | Centered sign-in card with email and password, then a second step for a six-digit authenticator code. Error states and locked-account message. |
| DA-W02 | Dashboard | 2 | Stat cards (incidents today, active, confirmed today, queue size), chart of incidents by type, chart of incidents over 30 days, table of top LGAs, small list of newest flagged reports. |
| DA-W03 | Moderation queue | 2 | Filterable data table of reports under review with columns for type, location, reporter reputation band, flag weight, age, and status. Filters for LGA, ward, type and age. Row checkboxes and bulk Restore and Mark false actions. |
| DA-W04 | Incident review drawer | 2 | Right-side drawer over the queue: map, description, media grid with moderation status, event timeline, list of flags with reasons, and action buttons Restore, Mark false, Hide, Extend. Reporter identity shows as hidden with a "Request access" link. |
| DA-W05 | Reporter lookup dialog | 2 | Warning dialog for break-glass access: states that the lookup is logged, requires a written reason, and shows the audit notice. Reason text area and Confirm and Cancel buttons. |
| DA-W06 | Verifier management | 2 | Table of verifiers with name, title, LGA and ward badges, status, approved by, and actions to suspend or revoke. Add verifier button and search. |
| DA-W07 | Add or approve verifier | 2 | Form with user phone search, title, scope picker for LGA and ward with a small map of the area, and Approve button. Review summary step. |
| DA-W08 | Users and roles | 2 | Table of users with phone (partly masked), role, area, status, and a role change control. Search and filters. Confirmation modal for role changes. |
| DA-W09 | Audit log | 2 | Chronological table with actor, action, entity, reason, time, and IP. Filters by actor and action type. Row expand for metadata. Export button. |
| DA-W10 | Platform settings | 2 | Sections for corroboration radius and threshold, incident type lifetimes, SMS provider and credentials (masked), and an SMS fallback kill switch. Save with confirmation. |
| DA-W11 | SMS and OTP spend | 4 | Charts and table of daily OTP and SMS volume, delivery rate, cost against daily cap, and alert thresholds with edit controls. |

## Public web pages (device: public-web)

| ID | Screen | Phase | Prompt |
|----|--------|-------|--------|
| DA-P01 | SOS tracking page | 3 | Simple page opened from an SMS link: map with the person's last location and trail, "Name is sharing their location", last updated time, link expiry time, Call button, Open in Google Maps button. No login, minimal assets. |
| DA-P02 | Tracking ended or expired | 3 | Calm page saying the link has ended or expired, with a "Person is safe" variant. No sensitive details. |
| DA-P03 | Contact opt-in | 3 | Page opened from an SMS: "Name wants to add you as a trusted contact for emergencies", what you would receive, Accept and Decline buttons, link to privacy information. |
| DA-P04 | Opt-in result | 3 | Confirmation for accepted and declined, with how to withdraw consent later. |
| DA-P05 | Privacy and terms layout | 1 | Readable long-form legal page template with a table of contents, section headings, a last-updated date, and a language switcher. |

## Phase 5: platform expansion (2026-11-10)

All on design system `assets/14479210342777017401`. Each brief asked for the dark near-black base, neon lime only for the main action and active nav item, Inter, 20 px cards, pill controls, and severity colours that always come with an icon and a label.

| ID | Brief |
|----|-------|
| DA-W12 | Admin sign-in, split layout: brand panel with logo, "Community safety, coordinated.", soft severity dots and three live stat chips; sign-in card with email, password with show toggle, lime "Sign in", "Ask a super admin to reset it", audit and rate-limit footer. |
| DA-W13 | Panel shell and dashboard: grouped sidebar (Overview, Moderation, Communication, Network, Admin) with counts and a staff card with role pill; top search and "New broadcast"; KPI row, needs-attention list, incidents by type, system status. |
| DA-W14 | Staff: Active, Invited and Disabled tabs; role, area scope, last sign-in, two-step; invite drawer with role, areas and "Require two-step sign-in". |
| DA-W15 | Roles and permissions matrix grouped by area, super admin column locked on, sensitive permissions marked, sticky save bar. |
| DA-W16 | Audit log for super admins: chain-verified chip, CSV export, filters, expandable rows with details and entry hash. |
| DA-W17 | Moderation queue v2: filters, bulk action bar, claim, waiting time in amber and red, word-filter holds. |
| DA-W18 | Incident detail v2: timeline, photos, internal notes, public updates, action card, possible duplicates with merge, sensitive actions needing a reason. |
| DA-W19 | Broadcasts: composer with area, channels and reach estimate; two-person approval list with delivery stats. |
| DA-W20 | Analytics: range pills, KPI deltas, incidents per day by severity, by type, by LGA, outcomes, delivery, weekly report. |
| DA-W21 | System health: service status grid, scheduled jobs, kill switches, editable limits. |
| DA-W22 | User detail: reason gate, reports, flags, devices, SOS history, sanctions with appeals, reputation history. |
| DA-W23 | Partners and webhooks: organisations with scope, signed webhooks, rotate secret, recent deliveries with retry. |
| DA-W24 | Support inbox (two-pane) and NDPA privacy requests with due dates. |
| DA-W25 | Live map: full-height dark map, floating filters, heat toggle, selected incident panel, ring legend. |
| DA-W26 | Settings sub-pages: incident types, word filter, boundary upload. |
| DA-M38 | Watch zones list with toggles, lime "Add a zone", limit of five. |
| DA-M39 | Contact support: category chips, message, device info checkbox, your conversations. |
| DA-M40 | Feed with an announcement card and inline verifier updates. |
| DA-M41 | Become a verifier: what verifiers do, form, pending state. |
