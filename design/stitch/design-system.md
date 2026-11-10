# Dey Alert design system (v3)

Mirrored in `lib/core/theme/tokens.dart`. Mirror in the admin panel's Tailwind config when it is built in Phase 2.

## Tokens

| Token | Value | Use |
|-------|-------|-----|
| primary | `#A6EF1F` | Brand and primary actions, with near-black text |
| primary on light | `#4C7A00` | Lime is unreadable on white, so the light variant uses this |
| accent | `#F5A623` | Attention, secondary accent |
| danger | `#FF3B30` | SOS and destructive actions |
| dark background / surface / border | `#0A0C0A` / `#141714` / `#3A413A` | Default theme |
| dark text / secondary text | `#F2F5F3` / `#B4BCB4` | Lighter secondary text for outdoor reading |
| light background / surface / text | `#F7F9F8` / `#FFFFFF` / `#111614` | Light variant |
| card radius | 20 px | Cards and sheets |
| pill radius | full | Buttons, chips, search fields, navigation |
| font | Inter | All text |
| minimum tap target | 48 dp | All controls |

## Style

Modern, sophisticated, and restrained: near-black surfaces with hairline borders, a strong type hierarchy, generous spacing on an 8 px grid, one thin line-icon set, and no decorative gradients.

Each screen has one main action. Lime is reserved for that action and the active tab, and a soft glow appears only around it. Bottom navigation is a floating pill bar with five labelled tabs, with a lime pill behind the active one. Labels stay because icons alone fail lower-literacy users.

## Meaning

Never carry meaning with colour alone.

**Colour means severity,** always paired with an icon and a text label. Lime and green are never severity or status colours, because a confirmed kidnapping must not look safe. The SOS button is always red.

| Severity | Colour |
|----------|--------|
| Low | `#5B9BD5` |
| Medium | `#F5A623` |
| High | `#F2711C` |
| Critical | `#FF3B30` |

**Pin outline or badge means verification.**

| Status | Treatment |
|--------|-----------|
| Unverified | Dashed ring, labelled "Unverified" in calm wording |
| Corroborated | Solid ring |
| Confirmed | Solid ring with check badge and "Confirmed by [title], [ward]" |

## Theme order

Dark is the default. Light-variant screens are generated after the dark direction is approved, and both must pass the sunlight contrast check in the review checklist.
