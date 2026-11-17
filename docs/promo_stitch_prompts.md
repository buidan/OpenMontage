# Stitch prompts: promo end cards (and optional hook cards)

One end card per app. The promo assembler shows it for the last ~4 s of every reel,
under a voiceover ("…Find out in Kawaii Stories, coming soon."), with a slow zoom.

**Where to put the export:** `<app repo>/design/promo/end_card.png` (1080×1920 PNG, English),
plus one per audience language: `end_card_vi.png`, `end_card_zh.png`, `end_card_ja.png`.
The assembler picks `end_card_<locale>.png` when it exists, else `end_card.png`. Easiest in
Stitch: design the English card, then ask "Make a copy with all text in Vietnamese: …".
Optional hook card: `<app repo>/design/promo/hook_card.png`; without it the reel opens on
a still from the gameplay (Stories uses the story's cover art).
Then set `end_card:` / `hook_card:` for that app in `autopilot/promo/apps.yaml`
(Stories already points at `design/promo/end_card.png`).

## Rules that apply to every card

- 1080×1920 portrait, no device frame, no status bar.
- **Safe zone:** keep all text and the icon inside the middle area; leave the top 220 px and
  the bottom 420 px free of important content (TikTok / Reels / Shorts buttons and captions
  cover them), and keep 120 px clear on the right edge (like / share buttons).
- Leave the lower-middle band (roughly y = 1250–1450) calm: spoken-word captions appear there.
- No "Download now", no prices, no "free", no ratings, no award badges, no statistics.
- Store line: **"Coming soon to the App Store & Google Play"** as plain text (no Apple / Google
  logos or badges until the apps are live; then swap in the official badge artwork).

## End-card prompt template

Paste into Stitch inside the app's own project, so it uses that design system:

> Design a 1080×1920 portrait promo end card for **{APP NAME}**, a {ONE-LINE DESCRIPTION},
> using this project's design system (colors, clay 3D style, rounded shapes, typography).
> Centered composition: the app icon as a large soft clay rounded square near the top third,
> the app name "{APP NAME}" in big friendly display type below it, a one-line tagline
> "{TAGLINE}" in smaller type, and at the bottom of the safe area the text
> "Coming soon to the App Store & Google Play". {MASCOT LINE} Cozy, warm, uncluttered
> background in the design system's surface colors with a few soft decorative shapes at the
> edges. Keep the top 220 px, the bottom 420 px and the right 120 px free of text. No device
> mockup, no buttons, no badges, no prices, no statistics.

## Per-app values

| App | One-line description | Tagline (from approved claims) | Mascot line |
|---|---|---|---|
| Kawaii Stories | story-based language-learning app for kids | Stories that teach Chinese, English and Japanese | Bun the clay bunny waves next to the app name, holding a small open storybook. |
| Kawaii Math | Singapore-method math app for kids K–6 | Math lessons with Maestro Bun | Maestro Bun points at a floating number block. |
| Kawaii Code | block-coding puzzle app for kids | Coding puzzles, one block at a time | Maestro Bun stands beside a small robot holding a code block. |
| Kawaii IQ | non-verbal puzzle app for kids | Puzzles that grow with your child | Maestro Bun looks at a floating shape puzzle with a magnifying glass. |
| Kawaii Lab | cozy 3D clay science lab for kids | A cozy science lab for curious kids | Maestro Bun, Baker Mochi and Pip peek over a bubbling clay flask. |
| Kawaii Draw | step-by-step drawing app for kids | Draw cute friends step by step | A clay crayon draws a cute character outline. |
| Kawaii Keys | piano-learning app for kids | First piano songs made fun | A small clay piano with rainbow note bubbles floating up. |
| Kawaii Frets | guitar-learning app for kids | First guitar chords made fun | A small clay guitar with a chord diagram bubble. |

## Vietnamese versions (`end_card_vi.png`)

After the English card, ask Stitch in the same project:

> Make a copy of this end card with all text in Vietnamese. App name stays "{APP NAME}".
> Tagline: "{TAGLINE VI}". Bottom line: "Sắp ra mắt trên App Store & Google Play".
> Keep the same layout, safe zones and style; make sure Vietnamese diacritics render
> correctly and the text still fits on its lines.

| App | Tagline (vi, draft: review with the rest of locales/vi.yaml) |
|---|---|
| Kawaii Stories | Truyện giúp bé học tiếng Trung, tiếng Anh và tiếng Nhật |
| Kawaii Math | Học toán cùng Maestro Bun |
| Kawaii Code | Lập trình từng khối một |
| Kawaii IQ | Câu đố lớn lên cùng bé |
| Kawaii Lab | Phòng thí nghiệm ấm áp cho bé tò mò |
| Kawaii Draw | Vẽ những người bạn dễ thương từng bước |
| Kawaii Keys | Bài piano đầu tiên thật vui |
| Kawaii Frets | Hợp âm guitar đầu tiên thật vui |

## Optional hook-card prompt

> Design a 1080×1920 portrait background for the first 3 seconds of a promo video for
> **{APP NAME}**, using this project's design system. A bright, inviting clay scene that shows
> {WHAT THE APP DOES}, with a calm empty area across the middle (y = 700–1300) where large
> caption text will be overlaid. Same safe zones as the end card. No text in the image.
