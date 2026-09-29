# Noust brand

In Orkney and Shetland a **noust** is the hollow on the shore where a boat is drawn up and
sheltered between voyages (Old Norse *naust*, the boathouse). Your server is the noust, your
apps are the boats. Pronounced "noost", always written lowercase in the wordmark.

## Files

The SVGs are the source and are written by hand. Everything raster is rendered from them by
`node docs/brand/render.mjs [reference.png]`, which uses the Chromium the console's Playwright
suite already installs.

| File (`panel/src/assets/brand/`) | Use |
|---|---|
| `noust-mark.svg` | The mark, two colours. Default. |
| `noust-mark-mono.svg` | The mark in `currentColor`: terminals, print, one-colour contexts. |
| `noust-icon.svg`, `noust-icon-mono.svg` | Square icon redrawn for 16-32 px: hollow, hull, one slab per side. |
| `noust-wordmark.svg`, `noust-wordmark-mono.svg` | Mark and "noust" set as strokes, no font. |
| `panel/public/favicon.svg` | The icon with a `prefers-color-scheme` query that lightens the ink. |
| `panel/public/*.png` | favicon 32, apple-touch-icon 180, app icons 192 and 512 (rendered). |

Ink is `currentColor` with `color="#18181b"` on the root, so a file on its own is off-black,
and the console (`<Logo>`, which references these files through `<use>`) paints it in the
text colour of the current theme. The hull keeps its violet in both themes. The console imports
them with `?url&no-inline`: `<use>` cannot reference the data: URL Vite would otherwise inline.

## Construction

- Mark: `viewBox 0 0 140 36`, a 1-unit grid with half and quarter units only where a curve
  needs them. Symmetric about x = 70.
- Weights: ground line 4, hull strakes 4, slabs 6, clinker gaps 1.5, gaps between slabs 2.
  Caps and joins are round everywhere.
- Ground at y = 26, dipping to y = 34 between x = 40 and 100 (cubic S, handles 7 and 8 long, no tighter than the stroke).
- Hull: one even-odd path, tips of radius 1.25 whose inner edge leaves at 3:4, keel bottom at
  y = 31, one unit above the hollow. The two cuts are lenses running parallel to the sheer.
- Slabs: 34, 27 and 20 wide from the bottom, offset by up to one unit, as dry stone is.
- Icon: `viewBox 0 0 32 32`, weights 4 on even coordinates so 16 and 32 px land on whole pixels.
- Wordmark: x-height 18, stroke 4.5, baseline on the ground's lower edge (y = 28); "noust"
  starts 14 units after the mark. The whole lockup is `viewBox 0 0 254 36`.

## Colour

| | Hex | Where |
|---|---|---|
| Ink | `#18181b` | Ground, slabs, wordmark on light grounds |
| Ink on dark | `#f4f4f5` (favicon) or the theme's text colour (console) | |
| Hull violet | `#7b61ff` | Sampled from the approved reference; 4.2:1 on white, 4.5:1 on `#111` |

The violet is the brand; the console's interactive accent is a separate token. Nothing else in
the mark takes a colour.

## Clear space and minimum size

- Clear space: the height of the bottom slab (6 units, one sixth of the mark's height) on every
  side.
- Mark: 16 px tall minimum (62 px wide). Wordmark: 16 px tall. Below 24 px wide use the icon,
  which holds at 16 px.

## Do

- Use the mono file when only one colour is available, rather than recolouring the violet.
- Keep the ink in the surrounding text colour on dark grounds.

## Don't

- Stretch, rotate, outline, add shadows or gradients, or set the hull in any colour but the violet.
- Add slabs, remove the hollow, or put the hull above the ground line: it rests in the noust.
- Retype "noust" in a font next to the mark; use the wordmark file.
- Use the mark as a status colour or a button: in the console, colour means state.
