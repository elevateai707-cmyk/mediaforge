# Shared creative profile

The portable preferences live in `backend/app/edits/creative_profile.json`.
Creator Studio reads this file for its filming briefs. Named render presets live
in `backend/app/edits/style_pack.py`; the editor loads its choices from the same
backend catalog. Update these source files through Git so both installations
receive the same preferences. No private source recordings are needed to load
this profile.

## Caption choices

- **kinetic-db**: Build With DB gold #D4AF37, cream emphasis, condensed Anton,
  uppercase, speech-timed words, larger emphasis, soft dark shadow, no boxes.
- **kinetic-teal**: the general teal/white caption preference for other brands.
- The existing kinetic-word preset remains available for old projects.
- The shared style catalog also includes chapter cards, serif cards, gradient
  words, two-tone headlines, title bars and checklist text. Graphics recipes and
  timeline effects remain accessible through the editor API and project data;
  not every recipe has a visual wizard.

Select a preset in Studio. Changing it loads the preset's defaults; individual
fields can then be adjusted. Existing saved projects retain their own styles.
New Creator B-roll drafts use kinetic-db. Captions require a speech transcript
or manually entered cues; selecting a style does not invent spoken words.
Per-word timing uses supplied word timestamps; cues without timestamps use an
estimated split. Review timing and safe areas on the actual export.

Install fonts using `scripts/install_style_fonts.py` (Linux/fontconfig). It uses
openly licensed Google Fonts families and retains OFL license files. Missing
fonts are reported in Settings; otherwise a font fallback can change appearance.
The backdrop is currently a soft text shadow, not a full-frame gradient layer.

## Original reference-inspired work

Hooks are abstract planning components. Supply original wording and factual
claims for each campaign. Reference recordings are for analysis only: do not
copy their scripts, distinctive designs, clips, music, logos or watermarks.
Use neutral technique names. Keep original user narration unless a specific
synthetic voice has actually been created and authorized. Platform variants
should change their opening, visuals or story pacing meaningfully.

This repository stores reusable preferences and implementation. It does not
store the private reference library, voice samples, generated campaign media,
or the separate delivery/marketing worker's secrets and configuration.
