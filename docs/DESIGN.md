# Noust design system

The console's design system: the rules every page of `panel/` follows, the tokens and
components that implement them, and how they are enforced. It is normative. "MUST" and "MUST
NOT" are requirements (a change that breaks one is a bug, and most are caught by a test);
"SHOULD" is the default, departed from only for a stated reason; "MAY" is a free choice.

Where the system lives:

| What | Where |
|---|---|
| Tokens: every colour, size, radius, height, layer, width, duration | `panel/src/styles/tokens.css` (the only place a value is written) |
| Tailwind utilities for the tokens, the three heights and the sliding indicator | `panel/src/styles/app.css` (`@theme inline`, `@utility`) |
| Typefaces (packaged, never fetched) | `panel/src/styles/fonts.css`; the files and their licences in `panel/src/assets/fonts/` |
| Logos of real applications (packaged, with their licence and notice) | `panel/src/components/ui/logos/` |
| Primitives: buttons, fields, notices, tables... | `panel/src/components/ui/` |
| Page kit and the seven templates | `panel/src/components/page/` |
| Page header and URL tabs | `panel/src/app/PageHeader.tsx`, `panel/src/app/LinkTabs.tsx` |
| The frame: the main bar, the second column, the phone's bar and section selector, the breadcrumbs | `panel/src/app/frame/`, with the layout rules in `panel/src/app/navLayout.ts` and the saved choices in `panel/src/app/preferences.ts` |
| The executable specification | the gallery, `/__design` (development builds only; `panel/src/dev/`) |
| Enforcement | `styles/tokens.test.ts`, `styles/design-rules.test.ts`, `styles/design-structure.test.ts`, `styles/setting-copy.test.ts`, `styles/concept-doors.test.ts`, `styles/dead-class.test.ts`, `i18n/verbs.test.ts`, `i18n/glossary.test.ts`, `eslint.config.js`, `e2e/design-contract.spec.ts`, `e2e/indicator.spec.ts`, `e2e/field-edge.spec.ts`, `e2e/tab-switch-layout.spec.ts`, `e2e/leave-guard.spec.ts`, `e2e/settings-length.spec.ts` |

The brand (the mark, its violet, its construction) is `docs/brand/BRAND.md`. Copy rules and the
English/Spanish glossary are `panel/src/i18n/README.md`. This document says how the console
looks and behaves; the gallery shows it in both themes; the tests hold it.

**The identity is dry stone.** A *noust* is the hollow dug in the shore, often lined with dry
stone, where boats are hauled out and kept safe, and that is what Noust does with a fleet of
servers: it keeps what runs, sheltered. The console is built the way a dry-stone wall is: surfaces
are courses of stone of three heights under one light from above, each piece has the corner it
earns by its place in the hierarchy, and nothing is held together by decoration. Everything below
(the tones, the typefaces, the heights, the motion, the list of what is never drawn) follows from
that, and each rule says which test holds it.

---

## 1. Principles

1. **A precision instrument, calm, made of stone.** Dense where the operator works (tables,
   logs, key-value lists), generous where they decide (headers, states, confirmations). The only
   decoration is depth, and depth is information: a thing is carved in, seated or raised
   because of what it does (2.6). More to see and less to read: a picture, a shape or a number before a
   paragraph, and nothing on screen competes for attention it has not earned.
2. **Colour means something or it is not there.** Surfaces, text and navigation are stone: grey
   with a trace of green. Hue is spent on exactly three things: the **state** of something (green
   running or succeeded, amber in progress or warning, red failed, grey stopped), what the operator
   **can act on** (the hull's violet), and the **identity of a series inside a chart** (the `viz`
   family). The colour of a real logo, and of the hull in an illustration, is the brand's own. Every
   state is also told by a shape (a glyph) and a word. **Only what is not well tints its surface:**
   a healthy thing stays stone, so a screen of twenty applications that run is calm and the one that
   fails is the only thing with colour behind it. A state is never a stripe down the side of a card
   (2.12). Whether something is on, shown as a
   label (in a card, a row, a list, a fact), is a state too, and is a state badge: the running
   green for on, the stopped grey for off, never bare text (owner item 56, 3.2.1). A control
   that changes it (a `Switch`, a `Checkbox`) stays a control. The state of a feature is
   told by a `StatusPill` of 20px (`size="sm"`) in the header of its card or its row (green
   on, grey off, amber on but not working), because operators could not tell on from off at a
   glance (owner item 56, 3.2): colour, shape and word, small. It is never a block: a state is
   a fact, not news, and **no tinted block is taller than the control it explains** (the
   `FeatureState` block of 3.2 was 74 to 142px tall for a switch of 18px, and is retired).
3. **State first, action beside it.** The top of every view answers "how is it?" and "what can
   I do?": the state next to the title, the one primary action at the right.
4. **One implementation of each pattern.** When two pages do the same thing they use the same
   component. A new page is composed of a template and existing components; when something is
   missing, the system (and its gallery) grows first, then the page uses it.
5. **The system's output is sacred.** What nginx, systemd, certbot, git or a database prints is
   shown verbatim in mono, never paraphrased, translated or cut, with Noust's explanation and
   the fix above it.
6. **No jargon.** A term of Noust's own (release, in place, unit, drain, health gate) appears
   the first time in each view with its benefit in plain words; the technical name is a value
   in mono or a tooltip, not the label.
7. **Always know which server you are on.** On a fleet, the server is named in words, in mono,
   in fixed places: never by a tint.

Three corollaries the rest of the document applies everywhere:

- **Friction is proportional to what can be lost**: nothing for what is reversible, one
  question for one resource, typing the name for what cannot be undone.
- **Nothing moves when data arrives**: space is reserved from the first frame (layout shift
  0.05 or less on every page), and switching a tab never slides the page sideways.
- **The terminal is a way in, not a repeated hint**: the console does what the command does, so a page
  shows a command only when it has a reason (6.16, CL-1): the terminal alone can do it, it is for another
  server, or the system's own output names it.

---

## 2. Foundations

### 2.1 Colour

Every colour is written once in `tokens.css` as `light-dark(light, dark)`; `data-theme` on any
element pins a subtree to one theme. Tailwind's palette, type scale, shadows and radii are
switched off in `app.css`, so `bg-red-500` or `text-sm` do not compile: only token utilities
exist. The utility name is the token's with `color-` dropped (`--text-muted` is `text-fg-muted`,
`--accent-text` is `text-accent-fg`).

**Stone** (surfaces, edges and text: chroma under 0.02 in OKLCH, hue between yellow-green and
blue-green, tested). From the darkest stone to the lightest in light, and the other way up in dark,
where height is luminance:

| Token | Light / dark | Role |
|---|---|---|
| `--bg-rail` | `#c9cec6` / `#121615` | The main navigation bar |
| `--bg-column` | `#d6dad3` / `#181c1a` | The second column of the navigation |
| `--bg` | `#e5e8e3` / `#1d2220` | The page |
| `--bg-sunken` | `#d9ddd5` / `#232927` | Carved: fields, tracks, meters, code, the footer of a dialog or a card |
| `--bg-sunken-deep` | `#d0d4cc` / `#1b201e` | A carved field under the pointer |
| `--surface` | `#f4f6f2` / `#2a302d` | Seated: cards, tables, dialogs |
| `--surface-raised` | `#fbfcfa` / `#323a36` | Raised: buttons, option cards; also menus, popovers and toasts |
| `--surface-hover` | `#fbfcfa` / `#323a36` | A row that opens something, under the pointer |
| `--surface-active` | `#d9ddd5` / `#232927` | Pressed; a skeleton's ground |
| `--rail-hover` | `#d5d9d1` / `#1e2421` | An item of the navigation bar under the pointer |
| `--edge`, `--joint` | `#ffffff`, `#b0b7ad` / `#444c47`, `#0e1110` | The light edge on top of a height and the dark joint under it (2.6) |
| `--carved-shade`, `--carved-lip` | `#767f77`, `#ffffff` / `#0e1110`, `#7e8880` | The two edges of a field's hollow: the shadow along its top inside and the lit lip below. One of them is 3:1 against the page, a panel and a raised surface in each theme (C-12) |
| `--border` | `#c8cdc5` / `#3a423d` | Hairlines that separate content |
| `--border-strong` | `#767f77` / `#7e8880` | The boundary of a dialog, a menu or a drawer; the line of a control that has one (3:1) |
| `--text` | `#2e3530` / `#e8ece7` | Content |
| `--text-muted` | `#434c45` / `#bec6bf` | Secondary text |
| `--text-faint` | `#515b55` / `#9ea79f` | Meta: timestamps, help, placeholders. Still 4.5:1 on every surface, the carved ones included; never decorative |

**Interactive** (the hull's violet, only for what can be acted on or is selected):

| Token | Light / dark | Role |
|---|---|---|
| `--accent` | `#6a4df5` / `#9b86ff` | Primary button fill, a switch that is on, a chosen option |
| `--accent-hover` | `#5a3df0` / `#a898ff` | Its hover |
| `--accent-soft` | `#e3defb` / `#38305f` | A selected ground |
| `--accent-text` | `#5236d6` / `#b4a5ff` | Links, the icon of the current item |
| `--accent-edge`, `--accent-joint` | `#8f7bff`, `#3f2aa6` / `#c0b3ff`, `#5a47b8` | The light edge and the joint of the primary button |
| `--on-accent` | `#ffffff` / `#15102f` | Text on the accent |
| `--focus` | `#5236d6` / `#b4a5ff` | The focus ring |
| `--selection`, `--match` | | Selected text; a search match in a log |

**State** (each with a soft ground and a border for tinted blocks; only what is not well uses its
ground):

| Token | Light / dark | Role |
|---|---|---|
| `--ok`, `--ok-soft`, `--ok-border` | `#176a40` / `#5ad48e` | Running, serving, succeeded (its ground only in a message that stays open) |
| `--warn`, `--warn-soft`, `--warn-border` | `#7f5000` / `#ecb24c` | In progress, queued, warning, expiring |
| `--fail`, `--fail-soft`, `--fail-border` | `#b3261e` / `#ff8179` | Failed, down, invalid |
| `--fail-strong`, `--on-fail`, `--fail-edge`, `--fail-joint` | `#b3261e` / `#cf3a30`, white | The danger button's fill, its text and its two edges |
| `--idle`, `--idle-soft`, `--idle-border` | `#525c54` / `#a4ada5` | Stopped on purpose, unknown, cancelled (stone) |

**Chart series** (`--viz-1` `#006fb8`/`#3496ef` blue, `--viz-2` `#9b2673`/`#d462a8` magenta):
inside a plot and nowhere else. **ANSI** (`--ansi-blue`, `--ansi-magenta`, `--ansi-cyan`): a
program's own colours reproduced in `LogViewer`, nothing else. **Illustration** (`--il-stroke`,
`--il-stroke-2`): the grey stroke of a drawing and the fill of a meter that is not a problem.
**Veil** (`--backdrop`): behind a modal, over the main bar and the column as over the page; the ground under it is `--bg-veiled` while it is up, so the room of the scrollbar, which a fixed veil does not reach, is not a light strip beside it.

Rules:

- **C-1** Nothing but these tokens carries a colour. A component MUST NOT write a hex, `rgb()`,
  `hsl()`, `oklch()` or `color-mix()`; those belong in `tokens.css`. The one other colour on screen
  is a real logo's, the brand's own, set by `LogoTile` from its packaged data.
- **C-2** A tinted block (a notice, a failed job, an error) uses `--x-soft` for its ground,
  `--x-border` for its border and `--x` for its glyph and state word. It MUST NOT use an alpha on
  a colour token (`border-warn/40`, `bg-fail-soft/50`): a token's contrast is tested, a colour
  mixed with whatever is underneath is not. The one approved alpha is the veil, and it is a token
  of its own.
- **C-3** Information is not a state and has no colour: an info notice is `--surface`,
  `--border` and an `Info` icon in `--text-muted`.
- **C-4** Violet is for what can be acted on or is selected: the primary button, a link, the
  chosen option and the chosen tab, a switch that is on, the current step of a stepper, focus and
  selection. It MUST NOT decorate, MUST NOT colour data (a series, a count) and MUST NOT mark
  something that is not pressed: a badge that says "Default" is stone (2.12). The hull in an
  illustration is the one drawn violet that is not interactive, because it is the brand's.
- **C-5** Text has three grades, each 4.5:1 or more on every surface: `text` for content,
  `text-muted` for secondary text, `text-faint` for meta. Opacity MUST NOT be used to fade text,
  not even a section that is switched off (`SwitchedRegion` steps its content text down to
  `text-muted` instead): axe measures an opacity, and exempts only what is disabled or inert.
- **C-6** Surfaces go from `bg-rail` (the darkest stone) to `surface-raised` (the lightest) in
  light, and the other way in dark: `bg-rail` < `bg-column` < `bg` < `bg-sunken` < `surface` <
  `surface-raised`, plus `bg-sunken-deep` and the interaction steps `surface-hover` and
  `surface-active`. A tone is the height of what is drawn on it (2.6).
- **C-7** `border` separates content; `border-strong` bounds a dialog, a menu or a drawer, and a
  control that has a line. Never the other way.
- **C-8** On a tinted block, the content is in `text` (and `text-muted`); only the glyph and the
  state word take the state colour.
- **C-9** Work in progress is amber: the spinner, the deploying and queued glyphs, a progress bar.
- **C-10** The hull violet of the logo is the brand. In an illustration and in the logo itself it is
  drawn with `--accent`, which is why the two never go beside a violet that means "press".
- **C-11** The current navigation item is the raised piece under it, and nothing else is: the
  sliding indicator of 2.7 draws it (a tone, a light edge on top and a darker joint beneath, 2.6) in
  the main bar, in the second column and at the foot of a phone, each with its own piece,
  and `aria-current="page"` is the item's own state, so a screen reader and a test agree with the eye.
  Its label is `--text` and its icon `--accent-text`, against `--text-muted` and `--text-faint` for
  the others, and it does not get heavier (Y-2): a shape, a tone and a colour, never a colour alone.
  There is no bar of colour at its side (F-3, 2.12): the stripe of 4.0 (`data-nav-indicator`) is
  retired with the sidebar it was drawn in. *Enforced by:* `app/frame/frame.test.tsx` (one item is
  current in each bar, by `aria-current`, with no stripe and one aria-hidden piece in each list) and
  `styles/tokens.test.ts` (the label and the icon of the current item on the stone of the bar).
- **C-12** A field has no line round it, and is found all the same (WCAG 1.4.11, A-10). It is carved
  (2.6): a hollow in the stone with its shadow along the top inside, because the light comes from
  above, and its lit lip below. A hollow has those two edges and a theme can make only one of them
  strong, so each theme makes the one it can: in light the **shadow** (`--carved-shade`, the stone of
  `--border-strong`, 3.4:1 against the page, 3.8 against a panel, 4.0 against a raised surface); in
  dark the **lip** (`--carved-lip`, 4.4, 3.7 and 3.2), because nothing is darker than the dark page by
  3:1 (black on it is 1.3, on a raised surface 1.8) and the only way to tell a hollow there is the stone
  it is cut from catching the light. The other edge stays as soft as every other height's, so a field is
  a hollow and not a box with a border. This holds for the fields, a checkbox, the track of a switch
  and the rails of tabs and segments; a track or a meter that nobody presses has the fine edge.
  *Enforced by:* `styles/tokens.test.ts` (each theme's strong edge against the three grounds; the
  carved height is made of both edges; why dark cannot do it with the shadow) and
  `e2e/field-edge.spec.ts`, which reads the pixels a browser paints on a field, a checkbox and a
  switch in both themes and fails under 3:1.

### 2.2 Data and charts

- **V-1** A chart answers one question: its title is the quantity ("Memory"), its description
  the window ("Last 30 minutes"). A page's charts share one range control (`SegmentedControl`),
  and the range is in the URL.
- **V-2** At most three series. From the fourth, use small multiples (a chart per series) or an
  "Other" series. Colours are never generated, and series are not stacked by default.
- **V-3** Series 1 is `--viz-1`, solid; series 2 `--viz-2`, dashed (4-3); series 3
  `--text-muted`, dotted (1.5-3). Every series also has its own stroke and a label in the legend:
  colour is never the only channel. The pair is tested in both themes: 3:1 or more on every
  surface, a distance of 15 or more for normal vision and 8 or more for protanopia and
  deuteranopia (OKLab, Machado 2009), 35 degrees of hue or more from the accent and every state.
- **V-4** A series that is a state ("failures per hour") uses the state's token and glyph,
  never `viz`; state series and identity series are not mixed in one chart.
- **V-5** Deploys and other events on a chart are markers in the state language: colour, shape
  and word.
- **V-6** Gaps are gaps: the line breaks, and the data table says "no reading". Percentages
  have a fixed 0-100 axis. Only series 1 has a fill (8%).
- **V-7** Every chart has a written summary (the canvas's accessible name), a data table on
  request, and an enlarge action. The canvas's own text is mono 11px (the one size outside the
  type scale, because it is drawn, not laid out).
- **V-8** A meter is neutral up to 75%, amber to 90%, red beyond, and when it turns amber or red
  it adds the state's glyph and a word ("High", "Critical"; screen-reader only in the small
  size). Its value is always printed.
- **V-9** Every figure has its unit and goes through `lib/format.ts`, in tabular figures.

### 2.3 Typography

Three typefaces, each with one job, packaged with the console and licensed under the SIL Open
Font License (`panel/src/assets/fonts/`, listed in `third-party-licenses.txt`; nothing is fetched).
**Atkinson Hyperlegible Next** carries the interface: it was drawn so that characters cannot be
mistaken for one another, and the console is read on dense screens by people who are tired.
**Bricolage Grotesque** carries titles, in its narrow widths: the more a title is a title, the
narrower it is (a section 90%, a page 78%). **JetBrains Mono** carries every value that comes from
the system that is code: logs, commands, code and a value a person types or pastes. Never a
label, never a heading, never a metadata line, and never a name. Name text by **role**, not by size:

| Role | Where | Size / line | Utility |
|---|---|---|---|
| Page title | The `h1` of `PageHeader` and `AuthLayout` | 32/40 | `display text-32` (a system identifier is the same, with `translate="no"`) |
| Dialog and drawer title | The `h2` of a `Dialog`, a `ConfirmDialog` and a `Drawer` | 24/32 | `display text-24` |
| Section title | Every `h2`: `Section`, a wizard step | 16/24 | `title text-16` |
| Subsection title | Every `h3`: `Subsection`, `Card`, a `DangerAction` | 14/20 | `title text-14` |
| Reading | A `StatTile`'s value, a diagnosis headline, an empty state's title | 18/24 | `title text-18` |
| Body | Prose: a page's or dialog's description, an empty state | 14/20 | `text-14` |
| Dense UI (the working size) | Buttons, tables, fields, lists, key-value rows, tooltips | 13/20 | `text-13` |
| Label | A field's label; emphasis in dense UI | 13/20 | `text-13 font-medium` |
| Help and meta | Field help, timestamps, table headers, badges, keys, a tile's context | 12/16 | `text-12` |
| System value | Paths, ports, commits, IDs, unit names, commands | 0.92em of its line | `<Mono>` |
| System output | `SystemOutput`, `LogViewer` | 12/16 mono | verbatim |

- **Y-1** Only the seven sizes exist: 12, 13, 14, 16, 18, 24, 32 (and a system value's 0.92em
  of one of them). No `text-[...]`.
- **Y-2** Weights are 400 for running text and 500 for labels and emphasis. 600 is for what is
  pressed or names a column: the kit writes it in buttons, pills, badges, table headers and a field's
  label (`font-semibold`), and a feature MUST NOT write it. 650 and 700 come only from the `title`
  and `display` utilities, which set the display face; `font-bold` MUST NOT be used anywhere. A chosen
  tab or item does not get heavier: its width would change under the indicator that is sliding to it.
- **Y-3** Every figure that changes or is compared is in mono or `tabular-nums`; numeric columns
  are right-aligned.
- **Y-4** Mono is for logs, commands and code, and for a value that is typed or pasted somewhere else
  (a path, a port, a commit, an ID, a unit name): `<Mono>` (mono, no ligatures, `translate="no"`). A
  **name** (an application, a server, a domain, a database) is not code: it is set in the interface
  typeface, and as a title in the display face, with `translate="no"` so that it is never translated.
- **Y-5** Sentence case everywhere. No all caps, no wide tracking, no italics, no bold inside a
  sentence (emphasis is 500). A small label in capitals above a title is the commonest tic of a
  templated interface and is never drawn (2.12).
- **Y-6** Prose is capped at `--measure` (68ch, `max-w-measure`); help under a field at
  `--measure-help` (52ch). Tables have no measure.
- **Y-7** A system identifier used as a page title (a domain, a unit, a database, a site) is a title
  like any other, in the display face, and is not translated (`PageHeader mono` says so; the prop
  keeps its name for the pages that pass it).
- **Y-8** The text drawn before a font arrives takes the room it will keep: each font is followed by
  fallback faces scaled to its measured metrics (`fonts.css`, measured with fontTools over the
  console's own English copy), so the swap changes the glyphs and moves nothing. *Enforced by:*
  `styles/tokens.test.ts` (every fallback declared and scaled; the stacks in order; no CDN, no `data:`).

### 2.4 Spacing

The scale is Tailwind's quarter-rem step: **half steps (2px) up to 16px** inside components,
then **20, 24, 32, 40, 48, 64** for layout.

| Utility step | 0.5 | 1 | 1.5 | 2 | 2.5 | 3 | 3.5 | 4 | 5 | 6 | 8 | 10 | 12 | 16 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Pixels | 2 | 4 | 6 | 8 | 10 | 12 | 14 | 16 | 20 | 24 | 32 | 40 | 48 | 64 |

- **S-1** Nothing else: steps 7, 9, 11, 13, 14, 15, 6.5 and every arbitrary value
  (`mt-[1.625rem]`) are off the scale. To line a button up with a field, use `Field action`; to
  size a skeleton like a control, use `h-control-md`.
- **S-2** The rhythm of a page belongs to its template, not to the feature: header to content
  32; between sections 32 (`Sections`); a section's heading to its content 16; siblings inside a
  section 16; fields of a form 20; groups of a form 24; page padding 16 / 24 / 32 (phone,
  tablet, desktop), top 24 / 32, bottom 64.
- **S-3** Surfaces: a card's body 20 (`Card padding="md"`), a compact card 16 (`sm`); a banner
  notice 16 x 12, an inline notice 12 x 10; a dialog 20 wide, its body 16 tall; a drawer 20 x
  16; a table cell 12, the first 16.

### 2.5 Shape

- **F-1** Five radii, by hierarchy: the bigger the container, the rounder. `rounded-chip` 6px
  (a pebble: badges, pills' squares, keys, a fleet slab, the highlight of a menu row),
  `rounded-control` 9px (a piece: buttons, fields, menu rows, the raised piece of a tab,
  inline notices), `rounded-card` 14px (a slab: cards, tables, popovers, banners, the tracks that
  tabs and segments sit in), `rounded-wall` 22px (a wall: dialogs, the first-use panel) and
  `rounded-pill` (switches, meters, counts). A piece sits inside a slab with the slab's padding, so
  the corners stay concentric. Nothing else.
- **F-2** Borders are 1px. The focus ring is 2px `--focus`, offset 2, defined once in `app.css`
  (`:focus-visible`); a component adds only `-outline-offset-2` on a scroll box whose overflow
  would clip it.
- **F-3** There is no stripe of colour down one side of anything: no left border on a row, no inset
  shadow on a log line, no rail. The state is a glyph, a word and, when it is a problem, a tinted
  surface (2.12). *Enforced by:* the ratchet rule `side-stripe` and the sheet scan of
  `styles/design-rules.test.ts`.

### 2.6 Elevation

Height is made of **three kinds of edge and one light that falls from above**: a tone, a light
edge on the top of a piece and a darker joint beneath it. Never a blurred grey shadow repeated
under everything, which is what a stock interface does and what the identity is not. A
`box-shadow` here has at most 2px of blur and is never translucent black (`tokens.test.ts` reads
every `--elev-*` and fails on one).

| Height | Is | Made of | Who |
|---|---|---|---|
| **Carved** (`elev-carved`, `elev-carved-fine`) | Sunk in: where something is typed or chosen inside | `--bg-sunken`, shadow on top, light below; deeper (`--bg-sunken-deep`) under the pointer | `Input`, `Textarea`, `Select`'s trigger, `Combobox`, `Checkbox`, a `Switch`'s track, the tracks of `SegmentedControl`, `Tabs` and `LinkTabs`, the search box (these have the strong edge of C-12); `Progress` and `Meter`, `LogoTile`, a command in an empty state (the fine one) |
| **Seated** (`elev-seated`) | A low slab: what is read | `--surface`, a light edge on top and a joint of 1px beneath. It does not react to the pointer; a row of a table is flat | `Card`, `DataTable`, `Dialog`, `Drawer`, `Menu`, `Popover`, the lists of `Select` and `Combobox`, `Toast`, `Disclosure`, `StatTile`, `JobProgress`, `FleetCourses`, a `Kbd`, a state pill at `md` |
| **Raised** (`elev-raised`, `-accent`, `-fail`, `-chosen`, `elev-lift`, `elev-rest`) | What is pressed | `--surface-raised` and a joint of 2px. It rises 1px under the pointer (joint 3px) and sinks 1px when pressed (joint 1px) | `Button` (the primary with the accent's own edge, the danger button with the failure's), `IconButton`, the options of `ChoiceCards` (the chosen one with the accent as a ring), the thumb of a `Switch`, the chosen item of a row of tabs or segments (the sliding indicator), a fleet slab that opens a page |

- **E-1** A component takes the height of its role and no other. *Enforced by:*
  `styles/design-rules.test.ts` reads `KIT_HEIGHTS` (`styles/designRules.ts`), the role of every kit
  file that draws a height, fails on a piece that draws another or that draws one and has no role, and
  has cases that must be caught (a raised panel, a carved button).
- **E-2** A feature does not draw heights: a panel is a `Card`, a pressed piece a `Button`, a field a
  `Field`. *Enforced by:* the ratchet rule `elev-outside-kit`.
- **E-3** Only what is pressed moves under the pointer. An item that is flat until the pointer is on
  it (`elev-lift`) rises the same 1px; what is read does not react, so that a panel never suggests
  it can be pressed. A row that opens something changes its tone, flat.
- **E-4** Stacking is named, and nothing else sets a `z-index`:

| Utility | Value | What |
|---|---|---|
| `z-sticky` | 30 | Top bar, save bar, wizard bar |
| `z-backdrop` | 40 | The veil behind a dialog or drawer |
| `z-overlay` | 50 | Dialog, drawer, menu, popover, tooltip, select |
| `z-toast` | 60 | Toasts |
| `z-skip` | 70 | The skip link |

### 2.7 Motion

Motion answers an action and shows what changed. It is never decoration, and nothing enters in a
staggered cascade. **Each curve has a job**, and no curve is the default of everything:

| Job | Duration and curve | Where |
|---|---|---|
| What **enters** decelerates | `--duration-enter` 240ms, `--ease-enter` `cubic-bezier(0, 0, 0.2, 1)` | A dialog, a drawer, a toast, the veil; a small popup (menu, popover, tooltip, select list) 200ms, from 0.97 in scale |
| What **leaves** accelerates and takes less | `--duration-fast` 140ms, `--ease-exit` `cubic-bezier(0.4, 0, 1, 1)` | Every one of those on its way out; a removed slab lifts and fades |
| What **moves from one place to another** | `--duration-move` 200ms, `--ease-standard` `cubic-bezier(0.4, 0, 0.2, 1)` | The sliding indicator of tabs, segments and navigation lists, the highlight of a menu, the thumb of a switch, the fill of a meter |
| **Over and pressed** | `--duration-press` 100ms, `--ease-standard` | The rise and sink of a raised piece, the tone of a hovered row, a ghost button |
| Big and rare | `--duration-large` 360ms (400 at most) | A first-use illustration; nothing else |
| A state change of a pill | one opacity pulse (0.9s), never movement | `StatusPill` |
| Skeleton | `breathe` 1.6s (a loop) | `Skeleton` |
| Spinner, indeterminate bar | 0.8s linear, 1.4s | `Spinner`, `Progress` |
| Traffic along a `FlowDiagram` connection | `flow`: dashes advance 1.2s linear (`animate-flow`), 0.6s over TLS (`animate-flow-fast`) | `FlowDiagram` |

- **M-1** With `prefers-reduced-motion`, every duration token is 0, every animation stops and every
  transition is instant, except the spinner (`animate-spin`), which keeps turning slower: frozen, it
  would say nothing is happening. What is in progress also stays readable by its shape and word.
- **M-2** Nothing animates layout; lists do not reorder with animation; a state never slides.
- **M-3** An animation is a CSS animation declared in `app.css`, never SMIL (`<animate>` in an
  SVG): the reduced-motion rule stops CSS animations and does not reach SMIL. A component that
  marks something as moving also reads `REDUCED_MOTION` and leaves the class off.
- **M-4** Only `opacity` and `transform` (and `translate`, `scale`) change, with two stated
  exceptions, each for a reason. **The sliding indicator** moves by `translate` and changes size by
  `width` and `height`, because scaling a piece stretches its rounded corners, which would draw the
  raised piece as an ellipse mid-slide; the element is absolute and has no children, so no layout
  depends on it. **The fill of a meter or a progress bar** changes its `width` because it is the
  quantity itself, measured. Everything else that moves is one of the two properties.
- **M-5** The **sliding indicator** (`SlidingIndicator`) is the raised piece of the chosen item of a
  row of tabs, a group of segments, a navigation list (horizontal or vertical, folded to icons or
  not) and, flat, the highlight of the option being passed over in a menu or a select. It slides to
  the item the person picks in `--duration-move`, whatever way the choice was made, the browser's
  back and forward included. It does **not** slide on the first frame, when the item changes size by
  itself (a font arrives, the bar folds, the window resizes), when there was no chosen item (it is
  hidden, and appears in place), or with reduced motion. It queues nothing: holding an arrow key
  restarts a transition that goes from where the piece is to the item of the moment. It is
  `aria-hidden`; the items carry the state. *Enforced by:* `components/ui/SlidingIndicator.test.tsx`
  (the piece goes where the chosen item is, slides only between two items, never on the first frame,
  on a resize, with reduced motion or from nothing, and does not queue) and `e2e/indicator.spec.ts`
  (the real transition: its duration, its end at the target's box, none with reduced motion).

### 2.8 Icons

- **I-1** One set, lucide-react, plus Noust's own `StatusGlyph`, and three places where the name
  of an icon is written. The icons that carry a **meaning** come from `components/ui/icons.ts`
  (`ICONS.success`, `ICONS.warning`, `ICONS.error`, `ICONS.info`, `ICONS.locked`, `ICONS.verified`,
  `ICONS.more`, `ICONS.close`, `ICONS.dismiss`, `ICONS.delete`, `ICONS.external`, `ICONS.copy`,
  `ICONS.copied`, `ICONS.add`, `ICONS.search`). The icons of **objects** (an application, a
  database, a certificate, a server, a token, a log) come from one map of the kit,
  `components/ui/objectIcons.ts` (`OBJECT_ICONS.app`, `.database`, `.certificate`...), and the
  verbs of a button that `ICONS` does not have (turn on, edit, rebuild, refresh...) from
  `VERB_ICONS`, beside it. An object has one icon in the whole console: the sidebar, the lists on
  the side, the palette (all read the register, which reads the map), the head of a card, a menu
  item and a button that acts on a database draw the same one. A feature never imports an object's
  icon from lucide; when the object is missing the map grows, never the feature. *Enforced by:*
  lint rule `object-icon-import` (`no-restricted-imports`, whose list is read from
  `objectIcons.ts` so it cannot drift from it; the maps and the tests are exempt) and
  `semantic-icon-import` for the severities.
- **I-2** Five sizes, from `--icon-*` (`ICON_SIZE` in `icons.ts`): 12 (`size-icon-xs`, a status
  glyph in a dense row), 14 (`size-icon-sm`, a small button, a 13px line), 16 (`size-icon-md`,
  the default), 20 (`size-icon-lg`, an empty state), 24 (`size-icon-xl`, illustration only).
- **I-3** Lucide's stroke (2) is not changed, except the check of `Checkbox` and `Stepper`.
- **I-4** A decorative icon is `aria-hidden`. An icon replaces a word only in an `IconButton`,
  whose `label` is its accessible name and its tooltip.
- **I-5** One icon, one meaning. `CircleCheck` is "succeeded", `ShieldCheck` only "verified
  security" (a valid certificate). Two vocabularies, each in its place: `StatusGlyph` says the
  **state of an entity** (an app, a service, a job, a node); the `ICONS` severities say the
  **severity of a message** (`Notice`, `Field`, `Toast`).
- **I-6** An icon takes the colour of its text, or of its state; the accent only when it is an
  active interactive icon.
- **I-7** Where the icon of an object goes. The head of a `Card`, of a `SettingsGroup` and of a
  `Section` that is about an object carries its icon, before the title (`icon={OBJECT_ICONS.log}`:
  a component, never an element, so the kit sets its size and its colour); a button or a menu item
  that acts on an object draws the verb's icon when `VERB_ICONS` has one and the object's
  otherwise; so does a first-use empty state, at 20px. Without an icon, on purpose: a tab (its
  width is the budget), running text, an inline empty state, and a head that names no object of
  the map (Columns, Over time, About). Two cards of one view that are about the same object (the
  jails and the banned addresses of fail2ban) draw the same icon. *Enforced by:* review (G-7);
  `components/page/Section.test.tsx` and `Card.test.tsx` (the icon is drawn, decorative, in the
  text colour).

### 2.9 Density and control sizes

- **D-1** Every control reads its height from a token: `--control-sm` 32px, `--control-md` 36px
  (the default), `--control-lg` 44px (sign in, unlock, the wizard's Deploy). With a coarse
  pointer they grow to 40, 44 and 48px in one place (`tokens.css`). Utilities: `h-control-*`,
  `size-control-*`.
- **D-2** Table rows are compact (36px) for histories that are scanned (deployments, sessions,
  deliveries) and comfortable (44px) for inventories that are opened (applications, services,
  databases).
- **D-3** Two zones: dense (13/12px, rows of 36-44) in tables, logs, key-value lists; generous
  (14-16px, gaps of 24-32) in headers, confirmations and empty states.

### 2.10 Settings rows

A setting is a row, and the state of a feature is the header of its card. The rules below hold
every place that turns something on or off, and are the answer to the block of colour that used
to say it.

- **SW-1** A `Switch` is a setting that takes effect the moment it is moved and can be undone in
  one step. It never shares a surface with a Save button, and it is never a filter or a view mode
  (that is a `Checkbox` in a toolbar, or an `IconButton` with `aria-pressed`) nor a parameter of
  an action (that is a `Checkbox` in the action's form). *Enforced by:* ratchet `switch-in-form`;
  G-7.
- **SW-2** A setting is a `Setting` row: a label, one sentence of help, the control at the end.
  Rows of one subject live in a `SettingsGroup` (a `Card padding="none"` with `divide-y`). The
  state of a feature is the header of its card: the title, a `StatusPill size="sm"` after it
  (`Card status`) and the control in `actions`. A tinted block is never used to say that something
  is on or off. *Enforced by:* G-7 question 7 (`FeatureState` no longer exists, and
  `feature-state-notice` counts the neutral notice that replaces it).
- **SW-3** The label names the thing and never changes with the state ("Send notifications", not
  "Notifications are on"). The help describes what the setting does, in one sentence of 18 words
  or fewer, and never starts with the state ("Off: ..."). A row is 44px without help and 60px
  with one line of help; nothing in a row is taller than its text. *Enforced by:*
  `styles/setting-copy.test.ts` (resolves the keys passed as `label`, `help` and `risk` and counts
  words); ratchet `help-starts-with-state`; `e2e/design-contract.spec.ts` (row height).
- **SW-4** What changes when it is on is said in the help. A risk that exists only while it is on
  is one line under the help (`risk`: a 12px severity glyph in the state colour, the text in
  `--text`, at most 14 words) with a "What changes" `Popover` (`riskDetail`) for the detail. A risk
  that exists only while it is off (a safeguard) shows its line while it is off. *Enforced by:*
  `styles/setting-copy.test.ts`; review.
- **SW-5** Turning off a safeguard, or turning on something that exposes data or weakens a
  boundary, asks one question (`ConfirmDialog friction="simple"`, the `confirmOff` of a `Setting`)
  that says the consequence. Turning on what is safe and reversible asks nothing. Something that
  needs a plan (a dry run, a rewrite of units, a restart) is a button with an ellipsis ("Turn
  on..."), not a switch. *Enforced by:* `Setting.test.tsx` (`confirmOff` opens the question);
  review.
- **SW-6** A disabled switch always says why, in its help, with a link to what is missing. Locked
  by policy shows the lock and who set it. A switch in progress keeps its value and shows its own
  busy state; it fails back to the previous value with an error toast or an `ErrorBlock compact`.
  *Enforced by:* the type (`disabled` requires `reason`); `Setting.test.tsx`.
- **SW-7** A switch that asks for sudo mode says so before the press (a glyph with an accessible
  name) while the window is closed. *Enforced by:* `Setting.test.tsx` (`elevated` renders the
  glyph with a name).
- **SW-8** A number that means "off" when it is 0 or empty is a `Setting` with a switch that
  reveals the number in its `children` (the form of an optional limit): "No limit" and "Default"
  are visible states, not magic values. *Enforced by:*
  ratchet `empty-means-off` (the catalog strings "Empty keeps...", "0 turns it off", "Blank means
  no limit").

### 2.11 Illustration, logos and help

- **IL-1** An illustration is drawn only the first time of a main function (the first application, the
  first server, the first backup): `EmptyState variant="firstUse"` with `illustration`. An empty
  list, a filter that matched nothing and an error never get one. There are two drawings
  (`Illustration`): the noust with a hull in it, for the first thing a person does, and an empty slab,
  for a list with nothing yet. Both are one grey stroke (`--il-stroke`) and the hull in violet, follow the
  theme through tokens, carry no gradient and are decoration unless given a `label`.
- **IL-2** A tile is only for a real logo. `LogoTile` is a carved square with the logo of an
  application or an engine in its brand's colour; its marks are packaged (29 today, from Simple
  Icons, CC0, with the brands' notice in `components/ui/logos/`), so no request leaves the console to
  draw one and `img-src` stays `'self'`. An icon of the console's own stands bare: a tile behind
  it says nothing and is a tic (2.12). *Enforced by:* the ratchet rule `icon-tile`.
- **IL-3** An explanation is a `HelpPopover`: a "?" button that opens a popover. A `Tooltip` only
  names an icon (an `IconButton` does it for its label), because a touch screen has no hover and
  what is said in a tooltip is lost to anyone who cannot rest a pointer on it. *Enforced by:* the
  ratchet rule `tooltip-by-hand`.

### 2.12 What is never drawn

The marks of a templated interface, which the owner asked never to see again, and why.
Each is a rule of `styles/design-rules.test.ts`, counted in every file (the kit included) and held to
a baseline that only goes down, with samples that must be caught and samples that must not; the
stylesheets are scanned for the same.

| Never | Why | Rule |
|---|---|---|
| A stripe of colour down one side of a container (a card, a row, an alert, a log line) | It is the commonest mark of a templated interface, and it says the state by colour alone. The state is a glyph, a word and a tinted surface | `side-stripe` |
| A gradient, outside an illustration (and an illustration has none) | A surface is a tone of stone and an edge is a hard line | `gradient` |
| A blur: a frosted bar, a soft halo | The same, and it costs paint. A bar over the content is solid | `blur` |
| The same diffuse shadow under everything | The height is the identity; a blur is what every other interface has | `diffuse-shadow` and the `--elev-*` test |
| Capitals with a wide tracking above a label or a title | Sentence case is how the console speaks | `caps-label` |
| A middle dot between facts ("Next.js · :3001 · shop.example.com") | Each fact has its own place: a column, a line, a chip | `middle-dot` |
| An arrow in a label or at the end of a button | A button says its verb; a way from here to there is the breadcrumbs | `arrow-glyph`, `arrow-button` |
| A tile behind an icon of the console's own | A tile is for a real logo | `icon-tile` |
| Violet on something that is not pressed (a "Default" badge) | Colour means something | review (G-7) and C-4 |
| The same card, with the same shadow, ten times | A list of like things is a table, a list of courses or a `RowList` | review (G-2) |

---

## 3. Layout and widths

- **L-1** The shell is the navigation, a sticky 56px top bar and the page, and the navigation has two
  forms that only the window's width decides (`app/navLayout.ts`, NV-20): from 1024px, a **main bar**
  of areas in titled groups (`--width-rail`, 208px, or `--width-rail-folded`, 68px, folded to icons)
  and, always, a **second column** (`--width-column`, 256px) with the context of the area or the
  resource the page is under; and, on a phone, a bar at the foot with four areas and "More", and the
  column as a section selector at the top of the page. There is no other presentation and nothing to
  choose between them: no "one bar", and no strip of tabs that lists sections. The page is in a column
  capped at `--width-page` (1600px) with 16 / 24 / 32px of padding, or as wide as the window when the
  person chose the whole width.
- **L-2** Every template sets its own inner grid from the width tokens. A content column MUST
  NOT have a `max-w` narrower than its own header (the header and the content start and end on
  the same edges):

| Token | Value | Use |
|---|---|---|
| `--width-page` | 1600px | The shell's cap: T1, T2, T4, T6 |
| `--width-settings-nav` | 200px | T3's side navigation, T5's step rail |
| `--width-settings` | 896px | T3's content: the page's own column at 1440 (L-2), so a section ends where every other page does |
| `--width-wizard` | 736px | T5's step |
| `--width-auth` | 992px | T7's slab |
| `--width-dialog-sm / md / lg / xl` | 440 / 560 / 720 / 1280px | A question / a form of up to six fields / two columns or steps / looking closely |
| `--width-drawer-md / lg` | 480 / 720px | Forms and a row's detail / logs |
| `--width-search` | 288px | Every `FilterBar` search box |
| `--width-rail`, `--width-rail-folded` | 208 / 68px | The main bar, unfolded and folded to icons |
| `--width-column` | 256px | The second column of every area and every resource |
| `--measure`, `--measure-help` | 68ch, 52ch | Prose, help |

- **L-3** Layout breakpoints: `sm` (640px), `lg` (1024px), and the navigation's own, which are the
  frame's and not a page's: 1024 and 1440px (`navLayout.ts`). Below 640 is a phone (one column, tables as
  card rows); below 1024 the areas are at the foot of the screen, "More" opens a sheet with all of them in
  their groups, and the column is a section selector at the top of the page that opens a sheet with the
  same list; from 1024 the main bar and the column are always both there, because two open bars need
  about 1410px: from 1024 to 1439 the main bar is folded to icons and unfolds over the page without
  pushing it, and from 1440 it is unfolded and the saved fold decides. `md` and `xl` only hide columns or
  widen grids.
- **L-4** A page never scrolls sideways at 390px or at 200% zoom; only a table, a log or a tab
  strip scrolls inside itself.
- **L-5** The root keeps the scrollbar's room (`html { scrollbar-gutter: stable }`), and so do
  dialog bodies and drawers (`scroll-stable`): a short tab after a long one, or a dialog that
  grows, never slides the page by a scrollbar's width.
- **L-6** Reference viewports for screenshots and tests: 390, 1440 and 1920.

---

## 4. Page templates

Every page is one of seven templates, each a layout component in `components/page/`. A page
passes its header as props (the template renders `PageHeader` itself, with the right spacing),
and fills the template's slots. A page that needs something no template has extends a
template first (and the gallery with it).

Rules common to all:

- **One primary action per view**, the last of the header's actions (or the save bar's Save,
  while there are changes). Never in a section, never `size="sm"` for a page's primary. It holds
  while the `SaveBar` has changes: the header's primary is then drawn as a secondary (same label,
  same size, so nothing moves) until the changes are saved or discarded, and an empty state or a
  card never repeats a primary the header already has (FM-2).
- **The daily verbs are on the surface.** What an operator presses every day on a resource is
  a secondary action of its header, never behind `More actions` (a verb used every day and kept
  in a dropdown costs a click every time): an application's Restart, Stop or Start (by its state) and
  Roll back beside its primary, Update. `More actions` holds what is rare (Diagnose, Delete).
  Below 640px those verbs stay on screen as icon buttons that keep their names; the header
  wraps rather than hides them. A verb the resource cannot do is not drawn (a static site has no
  service to restart).
- **The header order**: breadcrumbs, the title with its state, one line of facts, one sentence
  of description; at the right the secondary actions, the primary, then `More actions` (an
  `IconButton`, never a text button "Actions").
- **Above the fold** at 1440: the state, the primary action and the start of the main content.
  At 390: the state and the primary action.
- **An `h2` never repeats the `h1` or the tab's name** ("Backups" under "Backups").
- **The CLI hint** (`CommandHint`) is shown only with its `reason` (CL-1) and then once per view, in the
  template's `footer`; in T3 once per subsection, at its end. A command that repeats a button is not shown.
- **What is destroyed** lives in T3's last subsection ("Delete") or behind `More actions` with a
  confirmation, and every destructive option in it starts **unchecked**.

### T1 List: `ListPage`

For: Applications, Databases, Services, Cron, Domains, Backups, Activity, API tokens, fleet
servers.

```
+---------+-----------------------------------------------------------------------+
| sidebar | Applications                             [Secondary] [+ Primary] [...]|
|         | Everything this server runs.                                          |
|         | [Subset A 12] [Subset B 3]                     (only if it has subsets)|
|         |                                                                       |
|         | [Search...          /] [State v] [Type v]              3 of 17 apps  |
|         | +-------------------------------------------------------------------+ |
|         | | Application        State       Type     Deployed      Port    ... | |
|         | | shop.example.net   * Running   Next.js  2 h ago       :3004   ... | |
|         | +-------------------------------------------------------------------+ |
|         | $ noust list                                                          |
+---------+-----------------------------------------------------------------------+
390px:  Applications / [+ Primary] [...] / [Search] [Filters] / card rows:
        +------------------------------+
        | * Running                ... |   <- the row's actions, always in view
        | shop.example.net             |
        | Next.js . 2 h ago . :3004    |
        +------------------------------+
```

```tsx
<ListPage
  header={{ title, description, primaryAction, overflow }}
  tabs={<LinkTabs .../>}          // optional: named subsets, as URLs
  notice={<Notice .../>}          // optional: at most one
  filters={<FilterBar .../>}
  footer={<CommandHint command="noust list" />}
>
  <DataTable ... />   // or, empty: <EmptyState variant="firstUse" .../>
</ListPage>
```

- Nothing sits between the header and the table but filters and at most one notice (a page has
  one banner at most: NT-3, held by the browser contract).
- What configures the list (schedules, destinations, engines, users) goes to a subset tab or to
  settings, never below the table.
- A row's light detail (a cron job's runs, an app's backups) opens in a `Drawer`; a row with a
  page of its own opens it.
- A column that says "-" or the same thing in nearly every row is removed.
- Empty: one `EmptyState variant="firstUse"` in the table's place, without filters or column
  headers, offering an action that is possible right now. A filter that matches nothing shows an
  inline empty state with "Clear filters".

### T2 Detail: `DetailPage`

For: an application, a database, a service, a site, a fleet node, a deployment. The sections of a resource
are not tabs under its header: they are the second column, which slides to them when the resource is
entered (NV-22, NV-23). The `tabs` slot is for the views of one thing (a site's Text, Structure and
Diagram; a zone's Records, DNSSEC and Delegation), never for pages.

```
Applications >
shop.example.net  * Running  Next.js . :3001 . shop.example.net ->  [Restart] [Stop] [Roll back] [Update] [...]
+--------------------------------------------------------------------------------------------+
| x The service keeps failing to start. systemd gave up after 5 restarts.       [Diagnose]    |  <- banner, every tab
+--------------------------------------------------------------------------------------------+
[o Updating shop.example.net  npm ci]                                                            <- the job in hand
--------------------------------------------------------------------------------------------
  the open section (the column says which): a list (T1 body), a log, charts, or settings (T3). No h2 repeating it.
```

```tsx
<DetailPage
  header={{ title: domain, mono: true, breadcrumbs, status, meta, secondaryActions, primaryAction, overflow }}
  banner={broken ? <Notice variant="banner" tone="error" .../> : undefined}
  job={<JobProgress .../>}
  tabs={<LinkTabs label=... tabs=... />}
>
  <Outlet />
</DetailPage>
```

- The header and the tabs belong to the layout route: the same height and place on every tab.
- A section of the resource that repeats another area's view (Server > Logs, a fleet face that
  mirrors the sidebar) is a filter of that view, not a section (NV-3, 6.14). A view reached from a
  state rather than browsed (an application's Diagnose) is in the column's "Fix" group, and the status
  banner, the header's `More actions` and the Overview's "Needs attention" link to it too.
- Settings that are saved but do not run yet are a chip in the header's status line ("3 not
  applied") that opens the review, not a full-width banner on every tab (FM-5).
- A strip of view tabs, where a page has one (NV-23), is made of URLs (`LinkTabs`) or of one state
  (`Tabs`), and a count on it (`count`) is `null` while it loads: its room is kept, so the strip does not
  change width when it arrives. A count on a section is the column's.
- When the resource is broken, a status banner between the header and the page says the cause
  and the fix, on every section, not only on the first.
- At 390 the tab strip scrolls with faded edges and the active tab centred; the header keeps
  the primary action visible beside `More actions`.

### T3 Settings with side navigation: `SettingsLayout` and `SaveBar`

For: global settings (and a database's or a service's when they
grow).

```
(T2 header, or the title "Settings")
+--------------------+------------------------------------------------------------+
| General            | Deploys                                                    |
| Deploys          < | How new versions reach production.                         |
| Deploy on push     | +- Instant rollback ------------------------- Off --------+ |
| Resources          | | Each deploy is kept apart; going back takes seconds.   | |
| Export             | +--------------------------------------------------------+ |
| ------------       | +- Startup check ----------------------------------------+ |
| Delete             | | fields                                                 | |
|                    | +--------------------------------------------------------+ |
|                    | ## 2 unsaved changes                    [Discard] [Save] | |  <- sticky bar
+--------------------+------------------------------------------------------------+
 200px navigation + content of 896px at most, starting on the header's left edge.
 390px: the navigation is an index page of its own; each subsection has "< All sections".
```

```tsx
<SettingsLayout label="Application settings" items={[
    { to: "/apps/$domain/settings", params, label: "General", exact: true },
    { to: "/apps/$domain/settings/deploys", params, label: "Deploys" },
    { to: "/apps/$domain/settings/delete", params, label: "Delete", danger: true },
  ]} index={isIndexRoute} backTo="/apps/$domain/settings" backParams={params}>
  <Card title=... description=...> fields </Card>
  <SaveBar changes={dirtyCount} onDiscard={reset} onSave={save} saving={pending} />
</SettingsLayout>
```

- An application's settings are not a T3 (4.2, item 116): its column lists seventeen sections in five groups and there is no
  list of its own inside a "Settings". What were eighteen subsections are four pages of titled cards (Code and build, Deploys
  and triggers, Resources and running, Access and protection, `features/app/pages/ConfigPage`) beside Export and declaration,
  Several servers and Delete, each a page of its own (6.7, FM-9).
- Each subsection is its own URL and fits in one and a half screens at 1440; one that does not is
  split (FM-8, NV-9).
- The side list is titled by what each section is for (NV-11), holds 8 items per group at most, 7 groups and 26 in all (NV-3), and
  grows a filter box from 12 entries (`SETTINGS_FILTER_FROM`; an item's `keywords` are the old
  names that still find it). The list is the second column and the box is at its head, above the
  groups; the page draws the section alone. On a phone the column is the section selector at the top of
  the page and its sheet has the same list and the same box; `/settings` itself is the hub of doors.
- A subsection's one primary action that is not a Save ("Invite a person", "Create token")
  goes in the page's header through `SettingsPrimaryAction` (`features/settings/SettingsShell`),
  never in a section.
- **One form pattern**: a subsection is one form, saved from its `SaveBar`, which is there from
  the first frame ("No unsaved changes") so its arrival never moves the page; Save is the
  view's primary only while there is something to save (and the header's primary steps aside
  meanwhile). No row of "Save changes" buttons.
- **A subsection saves in one way** (FM-1): a form with a `SaveBar`, or a list of immediate
  settings (`Setting` rows). Never both. It also warns before it is left with changes (FM-3):
  the `SaveBar` owns that question.
- A `Switch` applies at once and MUST NOT share a form with a Save button (SW-1).
- Read-only facts (type, directory, unit) go in the General subsection as a compact
  `KeyValueList`, not among the fields.
- What destroys goes in the last subsection, `danger: true`, set apart.

### T4 Dashboard: `DashboardPage`

For: Home (one server's and the fleet's), a server's summary. `fleet` is the subject of the page, two thirds
wide under the attention cards, and `side` what accompanies it a third wide; what stands under or beside the
subject is not drawn before it, so its arriving moves nothing.

```
Overview                                                   [1h 24h 7d 30d] [+ New application]
+ Apps 14/17 -+ Failed 2 ---+ Deploys today -+ Certificates -+ Backups 24 h -+ Disk ------+
| running     | worker ...  | 8 . 1 failed   | 1 in 12 days  | 5 of 17 apps  | 61%        |
+-------------+-------------+----------------+---------------+---------------+------------+
+ Needs attention (5 at most, then "All") -----+ + Recent activity ------------------------+
| x worker.example.dev  The service failed [View]| | 12:03 shop deployed #13  * Succeeded   |
| ! example.com  Expires in 12 days      [Renew]| | ...                                    |
+-----------------------------------------------+ +----------------------------------------+
+ CPU ------+ + Memory ---+ + Network --+ + Disk -----+   <- 4 in a row from 1280, 2 x 2 below
```

```tsx
<DashboardPage
  header={{ title: "Overview", secondaryActions: <SegmentedControl .../>, primaryAction }}
  figures={<>{tiles}</>}              // StatTile x up to 6
  attention={<Card title="Needs attention" padding="none">...</Card>}
  activity={<Card title="Recent activity" padding="none">...</Card>}
  charts={<>{charts}</>}
  firstSteps={empty ? <FirstSteps /> : undefined}
  tabs={<LinkTabs .../>}              // optional: the Overview's Map is a tab of it
/>
```

- It summarises and links; it never repeats a whole list (the Overview does not list every
  application).
- It takes `tabs` when the dashboard has a sibling view of the same subject (the Overview and its
  Map); the figures, attention and charts belong to the first tab.
- "Needs attention" comes first and only when something does, worst first.
- On an empty server, `firstSteps` replaces everything below the header: never four empty charts.

### T5 Wizard: `Wizard`, `Stepper`, `WizardActions`

For: New application (a page), Add a server, Deploy on push, Turn on instant rollback, Restore
a backup (dialogs).

```
Page (from 1024px):                          Dialog (lg, 720px):
+ steps --+ step ---------------+ so far -+  + Add a server ------------------ x +
| v Source| Address             | Source  |  | (1)Authorize --(2)Join -- (3)Done |
| * Addr. | The domain it       | ...     |  | step content                      |
| o Conf. | answers on.         |         |  +-----------------------------------+
| o Vars  | fields              |         |  | [Back]                 [Continue] |
| o Deploy|                     |         |  +-----------------------------------+
|         | [Back]   [Continue] |         |
+---------+---------------------+---------+
```

```tsx
<Wizard header={{ title: "New application" }} steps={STEPS} current="address" onSelectStep={goTo}
  title="Address" description="..." summary={<Card ...>}
  actions={{ back: { onClick }, next: { onClick }, missing: domain ? undefined : t("...missing"), onMissing: focusDomain }}>
  fields
</Wizard>

// In a dialog:
<Dialog size="lg" title=... footer={<WizardActions back=... next=... missing=... />}>
  <Stepper orientation="horizontal" steps=... current=... />
  ...
</Dialog>
```

- **One stepper**: numbered, each step done, current or not started, said in words for screen
  readers; vertical beside a page's step (from 1024px), horizontal on top of a dialog's (and on
  smaller screens). It never adds "Step 2 of 5" in text.
- **Continue is never disabled.** When something is missing, pressing it says what (a warning
  notice, announced) and moves focus to it (`onMissing`) instead of moving on. A disabled button
  does not say why.
- A step fits in about one screen; its title is an `h2`.
- **A step with nothing to ask is passed over** (New application, checklist M-12): Continue and
  Back skip it, the stepper still lists it, done, and opens it on request, and the last step says
  what was passed over. What decides is in `stepAsks` (`features/new-app/wizard.ts`), pure and
  tested: configuration asks when the type was not detected (or more than one was), when
  another platform proposes settings, when this server cannot deploy it as it is, or when
  something needs a fix; variables ask when one is required and has no value; a database asks
  when the project names one. Nothing is passed over on a guess: before the inspection arrives
  every step asks. The server step on a fleet is passed over when the one server that can take
  it is the one on screen. The budget, counted in clicks from the Applications list and
  measured by `e2e/click-budget.spec.ts`: a public repository to its first build in 4 (New
  application, Inspect, Continue, Deploy), a recipe in 3 (From a recipe, its card, Deploy).
- **A recipe is one step with its own Deploy** (`setup`: where it answers and the variables it
  cannot run without), then the job followed on the last step. What the recipe cannot run
  without is in front and blocks Deploy, said in a sentence that leads to the field; everything
  it brings with it is folded. A button that only opens a page is never called Deploy: it says
  where it starts ("From a recipe").

### T6 File editor: `FileEditorPage`

For: a site's web server configuration, a unit file.

```
Domains > example.net  * Serving                                        [Disable] [...]
(i) Tested with nginx -t before it is saved        Serves: example.net, www . /etc/nginx/...
+ editor, the screen's height (24rem at least) ------------------------------------------+
|                                                                                        |
+----------------------------------------------------------------------------------------+
## No unsaved changes                                  [Discard] [Test] [Test and save]
```

```tsx
<FileEditorPage header={{ title: site, mono: true, status, secondaryActions, overflow }}
  notice={<Notice title="Tested with nginx -t before it is saved" />} meta={<>...</>}
  bar={{ changes, onDiscard, onTest, onTestAndSave, testing, saving }}
  footer={<CommandHint command="noust site show example.net" />}>
  <ConfigEditor className="h-full" ... />
</FileEditorPage>
```

- The editor takes the screen's height (`h-editor`); the bar at its foot is a `SaveBar` with
  Test and Test and save.
- The `meta` line keeps its place while the file is read: a skeleton as tall as the facts, with
  `metaLoading` so the line is `aria-busy` (a skeleton with no busy region around it is silent to
  a screen reader). The editor under it says what is loading, once, in its own `LoadingRegion`.
- Never on the same page as logs or a danger zone.
- The check's own output (nginx -t, systemd-analyze) is shown verbatim when it fails.

### T7 Access: `AuthLayout`

For: sign in and the checks around it (second factor, notice), the first account, an invitation, a
new password, a sealed central. All of them are the same slab.

```
        +------------------------------------------------------------+
        | +-------------------------+                                |
        | | [noust]                 |   Sign in                      |
        | |                         |   With your Noust account.     |
        | |   (a noust, from the    |   [Username or email.........] |
        | |    side, a boat in it)  |   [Password..................] |
        | |                         |   [         Sign in          ]  |   <- control-lg
        | | Your apps and servers,  |   -- other ways to sign in --  |
        | | kept safe.              |   [Passkey][Your company][Token]|   <- AuthDoors
        | +-------------------------+                                |
        +------------------------------------------------------------+
         (c) Yago Lopez Prado. Free software under the AGPL-3.0-or-later.
                                  one slab of 992px
```

- One slab of 992px in the middle of the screen. On the left, carved in as a hollow (`elev-carved-fine`),
  the identity: the logo, `NoustSide` (a noust drawn from the side, in the greys of the stone with the
  boat in the accent) and one sentence. On the right, the form: the `h1` (`display text-32`, it takes
  focus on arrival), its sentence, the form and the terminal's way. Below 1024px the hollow is a band
  across the top of the slab (the logo and the drawing at the slab's width, no sentence) and the form
  follows it.
- The picture is in a slot of its own (`art`, `data-slot="art"`), filled by `SignInArt` (the noust) unless
  something else is given, and the slot gives what it holds the width of the hollow. The logo, the sentence,
  the operator's line and the licence notice are outside the slot, so a picture put in its place never takes
  them away. The one thing an operator puts there is their own picture (`SignInPicture`, Settings > Sign-in
  image): PNG, JPEG or WebP, in the drawing's own box (400 by 230, so the slab never changes size), filling it
  (`cover`, a photograph) or whole on the hollow's surface (`contain`, a logo), and it is the drawing again
  when the picture does not load. The answer is asked before the slab opens, and while it is on its way the
  slot holds the drawing unseen, so a company's screen never flashes Noust's drawing first.
- The licence notice is the page's `footer`, under the slab and outside it.
- Before sign-in nothing of the server is shown: not its name, not its version. The one exception is the
  line its operator chose (`auth.login_label`), under the sentence of the hollow.
- The other ways to sign in are `AuthDoors`: the password form first and, under it, two or three equal
  doors (passkey, your company when one is configured, access token). A door opens its way in the form's
  place, with a link back to the password.

### Patterns shared by the templates

| Pattern | Rule |
|---|---|
| First-use empty state | One per page, in the content's place: an icon, what this is and what is missing, one sentence and the one action possible now: no command beside it, which would repeat the button (CL-1). `EmptyState variant="firstUse"` |
| Inline empty state | Inside a section or a table filtered to nothing: one line with a link or a small button, no icon, no frame, 56px at most. `EmptyState variant="inline"` |
| Drawer | A row's detail with the page behind it: 480px for forms and detail, 720px for logs |
| Dialog | sm 440 a question, md 560 a form of up to six fields, lg 720 two columns or steps, xl 1280 only to look closely (an enlarged chart) |
| Danger | In T3's last subsection or behind `More actions` with a confirmation; destructive options unchecked by default |
| CLI hint | Only with a reason (`solo-terminal`, `otro-servidor`, `salida-del-sistema`), once, at the foot of the view (T3: at the end of each subsection) |
| State colours | Green only for running, serving, succeeded, and something that is on: a feature in the header of its card (`Card status`), or an on/off label in a row, a list or a fact (`StatusPill`, 6.1); never a block |

---

## 5. Components

Import primitives from `components/ui` and the page kit and templates from `components/page`
(both have an `index.ts`). Each entry: use it for, do not use it for, and its rules.

**`Button`** (`variant` `primary | secondary | ghost | danger`, `size` `sm | md | lg`, `loading`,
`icon`, `trailingIcon`). For an action with a verb. It is raised: it rises 1px under the pointer and
sinks 1px when pressed; the primary has the accent's own edge, the danger button the failure's, and
the ghost is text on the page and flat. It never ends in an arrow (2.12). Not for navigation (a link styled with
`buttonClassName`). One `primary` per view; `sm` inside cards, rows and toolbars; `lg` only for
sign in, unlock and the wizard's Deploy; `danger` only for the last step of a destructive flow;
`ghost` for toolbars and Discard. Labels are verbs: "Deploy", "Delete application", never
"Confirm" or "OK". Every button renders `data-variant`, which the design contract reads.

**`IconButton`** (`label` required). A known, repeated action (more, close, copy, refresh). The
`label` is its accessible name and the tooltip that names the icon. Raised in a header (`secondary`),
flat in a row (`ghost`). Not for the page's main action.

**`Field`** with `Input`, `Select`, `Textarea`, `Checkbox`. Every form control is in a `Field`:
label, help and error wired together. A `<label>` by hand MUST NOT be written. Labels have three
words or fewer; a field is required by default and `optional` marks the others; help stays
visible under the control; an error is an icon and a sentence under it. A button that acts on
the control (Inspect, Browse, Generate) goes in `action`, beside the control.

**`Combobox`** (`items`, `value`, `onValueChange`, `groupBy`, `filter`, `renderItem`,
`virtualized`, `mono`). One value from a long list, found by typing: time zones, services,
users. Every item has a `value` and a `label`; any other text it carries (a city, an
abbreviation, an offset) is searched by the default filter, which matches every word anywhere
ignoring case and accents and reads `+2`, `utc+1` or `gmt-03:30` as a UTC offset
(`matchesComboboxQuery`). Arrows move, Enter chooses, Escape closes; focusing it selects the
chosen label so typing starts a new search. Groups are headed in the order they first appear.
Beyond a hundred items only the rows in view are rendered (`@tanstack/react-virtual`); the
headings are then drawn but not announced, so an item's label names its group
(`Europe/Madrid`). A short, known list is a `Select`; a value the operator types freely is an
`Input`.

**`CopyValue`** (`value`, `label`, `name`). A value the operator pastes into another place (a
deploy key's public half, a webhook address, a command): in a sunken box, in full and wrapped
at any character, never cut, with its `CopyButton`. Not for a fact among facts (a
`KeyValueList` row copies its own) and not for output the system printed (`SystemOutput`).

**`SectionSwitch`** and **`SwitchedRegion`** (owner item 106). The master switch of a whole section
("Alerts", "Backups"): one `Switch` with its name beside it in the header of the page, on the line of the
title (`PageHeader master`, `SettingsMasterSwitch` in Settings: it belongs to what it governs and is never at the
far end of the header, HD-1), and the list it governs wrapped in a `SwitchedRegion`, which draws its content text in the
muted tone when the section is off so that a page of rules that do nothing does not look as if it did
(a change of tone, never an opacity: the page must stay 4.5:1 and stay reachable). The name never changes with
the state (SW-3); a switch that cannot be moved says why beside it (`disabled` requires `reason`).

**`Switch`** applies at once. Its track is carved and its thumb is a raised piece that slides across it
in `--duration-move`. **`Checkbox`** is a choice inside a form saved with a button, or a
selection. They never share a form (SW-1). A switch is drawn by a `Setting` row, which owns its
label, help, risk and `confirmOff`; on its own it is a bare control with an `aria-label` (its own
`label` and `description` props are retired and go from the type when the last page has moved). While
a change is applied it takes `busy`: it keeps its value and draws its arc, and says it with
`aria-busy`.
A yes/no choice is one of the two, never a `Select` of two options: the rule `boolean-select`
fails a `value: "true"`, `"false"`, `"yes"` or `"no"` outside the kit (the one exception is the
database row editor, whose options are a column's own two values and say so in a
`design-exception`). A few fixed exclusive choices that are not yes/no (TCP or UDP, allow or deny)
are still a `Select` in a form: a form-height `SegmentedControl` is not in the kit yet, and where it
goes with the rest of the form density is decided with the final UX pass, not here.

**`ChoiceCards`** (`legend`, `options` with `label`, `description`, `detail`, `icon`, `badge`,
`untranslated`, `columns`, `error`). One of two to six exclusive choices that each need a sentence to be
understood (how deploys work, a domain's role, a role of a person, a site's template): a radio group of
raised cards, a title and **one line** each, two to a row from 640px or one (`columns={1}`). The chosen
one takes the accent as a ring and a check. What does not fit in a line is the option's `detail`,
folded under the card behind a "Details" button that is beside the radio and not part of the choice, and
not read as its description. A choice that needs no sentence is a `SegmentedControl` or a `Select`.

**`FieldGroup`** (`legend`, `description`, `error`, `optional`). Related controls under one name and
one message: a set of checkboxes, a port and its protocol. A `fieldset` with a `legend` whose error
is the one `Field` shows (a glyph and a sentence in the failure colour, tied to the group with
`aria-describedby`). A paragraph in `text-fail` and a hand-written `role="alert"` have neither
(ratchet `field-error-by-hand`).

**`Disclosure`** (`label`, `defaultOpen`, `variant` `plain | card`, `summary`). The options of a
form most operators never change, folded under one ghost button ("More options") that says whether
it is open. Open from the start when what it holds already differs from the defaults. `card` is the
same in a bordered surface of its own for a part of a page that most operators skip, with a
one-line `summary` of what is inside; a native `<details>` is never written (ratchet
`native-details`). Not for a section most operators need, and never for help, which stays visible.

**`Dialog`** (`size` `sm | md | lg | xl`). A short task that needs focus. Not for long reading
or long forms (a drawer). A wall: 22px corners, seated, its title in the display face (24px) and its
footer a carved band; it opens decelerating and leaves accelerating (2.7). Footer buttons at the right,
the primary last.

**`ConfirmDialog`** (`friction` `"none" | "simple" | "type"`, `server`, `children`, `ready`, `size`
`sm | md | lg`, `explainError`, `failureAction`). Anything that destroys
or interrupts, with the friction its reach deserves (see 6.2); the friction is always declared,
there is no default (FM-6). It is the only confirmation of the console: a plan with a before and an
after (an sshd change, the ports a firewall protects) is its `children` in a wider `size`, not a
second dialog, and a destructive last step is never a
`variant="danger"` button written by hand outside it and `DangerZone`. `explainError` says a
refusal in its own words before it is shown, and `failureAction` puts what to run on the node under
the failure. `simple` opens on Cancel; `type`
asks for `confirmText`; `none` runs at once and reports a failure as an error toast. A failure
is shown in the dialog, verbatim, and the dialog stays open. On a fleet, pass `server`. Options
that change what the action does ("Also delete its files") are its `children`, `Checkbox`es
between the question and the name to type, never inside the description; each one that destroys
more starts unchecked, and the description says what the current choice destroys; `ready`
false keeps the action disabled while an option still lacks what it needs (a restore's
target). An action the operator held is not a failure (`lib/held.ts`, used here, by
`reportActionError` and by `ErrorBlock`): a cancelled "Confirm it's you" leaves the question
open and says nothing, a request left waiting for approval closes it with a neutral "Waiting
for approval" toast that leads to Approvals.

**`RestoreDialog`** (`existing`, `defaultTarget`, `replaceOnly`, `suggestion`, `isValidName`, `words`,
`options`, `onRestore`). The one confirmation of a restore, for whatever is backed up (an application, a
database): a new place by default where that touches nothing, with one question; over what exists, a
`ConfirmDialog friction="type"` that says what is replaced and what is kept first and asks for the name. It
knows neither word: the caller's `words` name the choices, and its own options are the children (a function of
where the restore goes). Not for anything that is not a restore.

**`Drawer`** (`size` `md | lg`). Detail with the page still behind it: a log, a unit file, a
certificate, a channel's form.

**`Menu`**, **`MenuItem`** (`description`), **`MenuSeparator`**. A row's actions and a header's
"More actions": seven items at most, the destructive ones last after a separator. The highlight of the
item the keys or the pointer are on is a flat sliding indicator that glides from item to item (M-5);
`Select` does the same in its list, and `Combobox` does not, because its list is filtered and
virtualised. An item's
`description` is one sentence under its label, wired as its accessible description, for what
the operator would otherwise stop to ask ("Nothing in the application changes"); most items have
none.

**`RowMenu`** (`name`). The menu at the end of a row: three dots, named "Actions for {name}" in
both languages (`common.rowActions`) so that a list of twenty rows says which one a menu belongs
to, with its `MenuItem`s as children. A feature does not write a `RowActions` of its own (ratchet
`row-actions-local`).

**`SplitButton`** (`children`, `name`, `onClick`, `menu`, `variant`, `loading`). One action with
its variations: the main part does the usual thing and the arrow beside it opens a menu of the same
action done another way ("Update" and, in the menu, "Update without cache"). One control of one
height with one focus ring, two buttons with their own names. A menu of different subjects is a
`Menu` on its own button, not this.

**`Tooltip`**. Names an icon, and nothing else (IL-3); never the only place something is said, never
on a button that already has text. **`Popover`**: small panels (the session menu), never a
confirmation. **`HelpPopover`** (`label`, `title`, `children`): the "?" beside a name that needs
explaining, a button that opens a popover, which a touch screen can use.

**`SlidingIndicator`** (`variant` `raised | flat`, `activeSelector`, `className`). The piece of 2.7 (M-5), drawn
first inside the positioned container that holds the items, which are `relative` so they paint above it.
It finds the chosen item by any of `data-active`, `aria-current`, `aria-pressed`, `aria-checked`,
`aria-selected`, `data-checked` and `data-status="active"`, measures it, and writes where it is as
`--indicator-x/-y/-width/-height` through the CSSOM (never a `style` attribute: the policy forbids it).
One piece serves a horizontal row and a vertical list; to mount it on a navigation list: give the list
`relative isolate`, put `<SlidingIndicator className="rounded-control bg-surface" />` first in it, make
each item `relative` with no ground of its own on the current one, and mark the current item with
`aria-current="page"`. Two lists on one page (the main bar and the second column) each have their own.
It is hidden when the current route has no entry in the list.

**`LinkTabs`** (`count` per tab) and **`Tabs`**. Sibling views of one subject: a carved rail with the
current one as the raised piece that slides (M-5). `LinkTabs` (the state is the URL) by default; `Tabs`
only where a URL makes no sense. Eight tabs at most. A row wider than its screen scrolls inside the rail.

**`DataTable`** (`mobile` `"cards" | "scroll"`, column `card` `"title" | "status" | "meta" |
"control" | "hidden"`, `density`, `layout` `"auto" | "fixed"`, `rowActions`, `onRowActivate`,
`skeletonRows`, `pagination`). Rows of like things. Under 640px a table is cards without being told;
`mobile="scroll"` is for the one whose columns are the point (a parameter list, a diff). A selection
checkbox is `card: "control"` (its own slot, never on the meta line), and an empty meta value
is left out of the card rather than drawn as a dash. `layout="fixed"` for values with no
natural length (an audit event): the columns keep their widths and cells truncate.
`pagination="apps"` names the list and turns on paging for it: see **`TablePagination`** and 6.8.
Rows of like things that are not columns of facts (a variable, a mount, a person in a dialog) are a
**`RowList`**, not a `ul` with `divide-y` and a border written by hand (ratchet `bordered-list`).

**`RowList`** (`aria-label` required) and `RowList.Item` (`leading`, `actions`). A bordered list on
a surface whose rows are told apart by a hairline, 44px at least, with the card's one radius and
one way to hold a glyph at the start and a `RowMenu` or one small button at the end. Its three
states are not its own: loading is `SkeletonRows` in a `LoadingRegion`, empty an `EmptyState
variant="inline"`, failed a `QueryState`.

**`TablePagination`** (`total`, `page`, `pageSize`, `onPageChange`, `onPageSizeChange`), with
`usePageSize(list)` and `pageWindow`. Under a list of more than 20 rows: "Showing 21-40 of
57", how many to a page (20, 50, 100 or all) in a compact `Select`, and previous and next.
`DataTable` draws it itself when it is given a `pagination` id; the size is the operator's,
remembered per list in `localStorage` (`noust.pageSize.<list>`), so applications and
databases are chosen apart and every table of one list agrees. Not for a list the server pages
(Activity's "Load more", the audit trail's keyset): those say how many are loaded instead.

**`EmptyCell`** (`reason`). A table cell with nothing in it: an en dash on screen and the reason
for screen readers. Never a bare "-".

**`StatusPill`**, **`StatusGlyph`**, `stateTextClass`, `STATUS`. The state of an entity, in
eight states with eight shapes (a pebble, never a tinted pill for what is well: running and static are
glyph and word on the page, and only in progress, a warning, a failure and a stopped thing take their
ground, 2.1): running (dot), deploying (spinning arc), queued (still dashed
ring), warning (triangle), failed (cross), stopped (ring), static (square), unknown (question
mark). `AppStatePill` and `DeployStatePill` translate the backend's words. Never a `Badge` or a
coloured dot for a state. It is also the badge of an on/off: `running` with the feature's word
for on ("On", "Enabled", "Connected", "Starts"), `stopped` for off, read-only (a span, never a
button; its accessible name is the word, the glyph is hidden). The state of a feature is one of
these at `size="sm"` (20px) in the header of its card, as the `Card`'s `status` after the title,
or after the label of a `Setting` row; "on but not working" is `warning` with its word ("On, no
channel") and a sentence with one link to the fix, never a block. Stated in words for Spanish
agreement: `StatusPill` has one word per state (`Fallido`) and a feminine entity passes a `label`
("Tarea fallida").

**The state of a feature** is the header of its `Card` or its `Setting` row: the card's `StatusPill
size="sm"` as its `status` and the action in `actions`, or a `Setting` row (2.10). The tinted block
of 3.2 (`FeatureState`) is deleted: at 74 to 142px for a switch of 18px it weighed the same for good
news as for bad and repeated what the card's title and the control already said. The signals (colour,
shape and word) are the `StatusPill`'s, in 20px. **The state of a feature is never a
neutral `Notice` either**: a notice in which only a word changes between on and off has to be read
to be understood (ratchet `feature-state-notice`).

**`Setting`** (`label`, `help`, `status`, `risk`, `riskDetail`, `elevated`, `lockedBy`, `children`;
a switch: `checked`, `onCheckedChange`, `busy`, `confirmOff`, `disabled` with its `reason`; or an
`action` button). One setting as a row: its name, one sentence of help and the control at the end,
44px tall without help and 60px with one line; nothing in a row is taller than its text. A
switch applies at once and can be undone in one step (SW-1); what needs a plan is an `action`
button ("Turn on...") and not a switch (SW-5). The rules of its text are SW-3 and SW-4 and are held
by `styles/setting-copy.test.ts`. Not for a filter or a view mode, a parameter of an action, or
anything saved with a Save button.

**`SettingsGroup`** (`title`, `icon`, `description`, `status`, `actions`, `level`). The rows of
one subject on one surface: a `Card padding="none"` whose children are separated by a hairline. A
subsection that saves with a Save button is a form of `Field`s, not a group of rows (FM-1).

**`InlineMessage`** (`tone`, `trailing`, `live`). One line with no frame and no ground: the risk of
an option, the status of a field or a row, a one-off result, in 14 words or fewer, with the severity
glyph in the state colour and the text in `--text`. A `Setting`'s `risk` is one. A field's own
validation stays the `Field` error; a framed condition with a fix is a `Notice` (NT-9).

**`Badge`**. A short attribute: a type, a version, a branch, a count, a "Default": a stone pebble that is
not pressed, so it is neutral. `tone="accent"` is only for something that is selected (it is violet, and
violet means pressed). Never a state: a state, including "this feature is on", is a `StatusPill`.

- **BD-1** A `Badge` is an attribute (a type, a version, a branch, a count) or something selected
  or staged (`accent`). It is never a state: a state, including "this feature is on", is a
  `StatusPill`. *Enforced by:* the type (`BadgeTone = "neutral" | "accent"`); ratchet `badge-state`
  (a tone the code computes).
- **BD-2** A state is never coloured text without its glyph and never a tone map of a feature's
  own: `StatusGlyph`, `stateTextClass` and `StatusPill` say it. *Enforced by:* ratchet
  `tone-map`; `styles/dead-class.test.ts` (a class that names no colour of the system, such as
  `text-danger`, fails: Tailwind drops it silently and the text is painted without its colour).

**`Card`** (`padding` `md | sm | none`, `as`, `interactive`, `loading`, `title`, `icon`,
`description`, `status`, `actions`, `footer`, `level`, `tone`, `leading`). Every panel of the console: a seated slab (14px
corners, a light edge on top and a joint beneath), which does not react to the pointer unless it opens
something, and then only changes its tone. A feature MUST
NOT write `rounded-card border bg-surface` by hand. `icon` is the object the card is about, a
component of `OBJECT_ICONS` (I-7). `status` is the `StatusPill size="sm"` of a feature with a
state, right after the title (not in `actions`). `leading` is a real logo (`LogoTile`) at the start of the header,
outside the text so that it spans the title and the line under it; `tone` (`warning` or `failed`) takes the
ground and edge of the state of what the card holds, only when it is not well (a database server whose
support is ending or ended, a thing that needs attention on Home), so that only what is wrong has colour
behind it, and the state is also said in words and a glyph inside, never by the ground alone. The
header keeps 12rem for its text and the `actions` go under it when they would take them, so a phone
never reads a description one word to a line beside a wide button.

**`Notice`** (`tone` `info | success | warning | error`, `variant` `inline | banner`, `title`,
`action`, `onDismiss`, `live`). A persistent message beside what it concerns (inline) or the
state of a page or server (banner). Not the result of an action that is visible anyway (say
nothing), not one that is not on screen (a toast), not a field's validation (`Field`), not a
failure with the system's output (`ErrorBlock`). `live` only for the outcome of something the
operator just did. It renders `data-variant`, which the browser contract counts. **Notice
rules**:

- **NT-1** A notice states a condition that is true now and that the operator can change or must
  know before acting. If a help line, a risk line or nothing says it, there is no notice. *Enforced
  by:* ratchets `notice-info-bare` and `notice-stack`; review.
- **NT-2** Size: an inline notice has a title of 8 words or fewer and a body of 30 words or fewer
  (three lines at 896px); a banner has a title of 8 words or fewer and a body of 25 or fewer. More
  than that goes behind "What changes" (`Popover`), a `Disclosure`, a `Drawer` or the documentation.
  One action. *Enforced by:* ratchet `notice-long` (the test resolves the keys of every `<Notice>`
  and holds the hard ceiling of 45 words; the limits above are held by review).
- **NT-3** At most one notice per section or form, two per dialog, and one banner per page. A
  banner states the condition of the page or the server and is never inside a card, a dialog or a
  settings subsection (there it is inline). With several conditions, show the worst or aggregate
  ("3 things need attention"). *Enforced by:* ratchet `notice-stack`; `e2e/design-contract.spec.ts`
  (at most one `[data-variant="banner"]` on every route).
- **NT-4** A notice never says whether a feature is on or off, never repeats the section's
  description or the control's label, and never describes a risk that is only hypothetical: that is
  a risk line (SW-4). A risk becomes a notice when it is in force (for example, previews are being
  given every variable). *Enforced by:* ratchet `feature-state-notice`; review.
- **NT-5** `info` is allowed only with an action or a title of its own, or for two or more
  sentences that must persist. A static explanation is the description of its section. *Enforced
  by:* ratchet `notice-info-bare`.
- **NT-6** `success` is not the result of saving. It is allowed only inside a dialog or a wizard
  that stays open and offers the next step. *Enforced by:* ratchet `notice-success`.
- **NT-7** A notice sits beside its cause, on the side the operator is looking at. It never appears
  above a control that is below it: the control would move under the pointer. *Enforced by:*
  `e2e/layout-shift.spec.ts` (the query editor's write mode); review.
- **NT-8** A link in a notice names its destination, not "Learn more". *Enforced by:*
  `i18n/glossary.test.ts`.
- **NT-9** `InlineMessage` (one line, no frame, no ground) is the form for a risk, a field's
  status or a one-off result; `Notice` is for a framed condition with a fix; the banner is for the
  page. A field's own validation stays `Field` error. *Enforced by:* the components' types and
  tests; G-7.

**`JobProgress`** (`state` `queued | running | succeeded | failed | cancelled`, `title`, `step`,
`error`, `hint`, `action`, `onDismiss`, `announce`). The job the operator just started, where
they started it. Its history is Activity's.

**`FilterBar`** (`label`, `search`, `filters`, `count`, `actions`). Search and filters above a
list: a 288px box that `/` focuses, the filters, the count at the right. Filters live in the URL.

**`EmptyState`** (`variant` `firstUse | inline`, required; `illustration`, `illustrationLabel`). See the
shared patterns above. The 3.0 framed block without a variant is gone. A bare muted paragraph is not an
empty state (ST-2). The first time of a main function gets an `Illustration` (IL-1); anything else gets
at most a bare icon, with no tile.

**`LogoTile`** (`logo`, `size` `sm | md | lg`, `label`). The logo of a real application or engine in a carved
tile, in its brand's colour, from packaged marks (IL-2). Used by the catalog, the recipes and the database
engines, and by nothing that is not a logo. **`LogoMark`** is the same mark with no tile, for a line of a list that is
about an engine or an application (the engines of the side column of Databases, a row of its table), where a tile
would be taller than its row.

**`FleetCourses`** (`label`, `courses`, `columns`, `linkFor`; `planCourse`, `isWell`). The fleet as courses of
stone: one course (a row) per server, with its name, its place and its disk on the left and its
applications as slabs of one size in a regular grid (two across on a phone, `columns` from 640px). The
ones with a problem come first, tinted with their state, its glyph and its word; the well ones are neutral
stone and, when a server has more than two rows of them, fill what is left of the two rows by name and the
last slab says how many more there are ("+3 running"), because a problem is never hidden, twenty that are
well say less than the one that is not, and a server drawn with none of its applications named, beside
neighbours that show theirs, reads as a server that has none. A server with nothing
on it shows an empty slot. A slab that opens a page is raised (it takes `linkFor`, since the kit does not
know the routes); without it the slabs are seated. A server is named as the operator named it (`web-01`),
never with a name that has to be decoded. A course takes a `problem` (`state`, `label`): a server that does not
answer says so on a slab at the head of its course, with the applications it last ran drawn after it. `ungrouped`
draws every application of a fleet of one, where "+3 running" would hide what the page is about.

**`Section`** (`loading`, `icon`), **`Sections`**, **`Subsection`**. A page's subjects (`h2`, 32px
apart) and their parts (`h3`, or `h4` inside a card). Headings are never written by hand in a
feature. A section about an object (a deployment, the health of an app, its domains) carries the
object's icon before its title (`icon`, a component of `OBJECT_ICONS`, decorative, in the text
colour: I-4, I-6 and I-7).

**`PageHeader`** (`title`, `mono`, `status`, `statusExtras`, `meta`, `description`, `breadcrumbs`,
`place`, `secondaryActions`, `primaryAction`, `overflow`, `server`, `flush`). The start of every page;
templates render it from their `header` prop. `description` is one sentence of prose; facts go
in `meta`; a `mark` is drawn before the title (the logo of an application's type, in a `LogoTile`) and holds
its room with a skeleton while the page is read; `actions` is the 3.0 form, kept for pages not yet migrated. `statusExtras` are chips
after the state that open what they count ("3 not applied", "Deploys paused"), instead of a banner
on every tab, and keep their room while they load; `statusBelow` draws the state and its chips on a line of
their own under the title, from the first frame, for a resource whose actions arrive after the page and would
otherwise take the room beside the title (an application's, a site's): a state that wrapped under the title when
the actions came moved the page; `place` is the line above the title that says
where a resource lives (team, project, environment), drawn only where there is something to tell
apart. The `primaryAction` is wrapped so that it steps aside while a `SaveBar` holds changes
(`DemotePrimary`): a `Dialog`, a `Drawer` and a `ConfirmDialog` reset it, since a dialog has a
primary of its own.

**`KeyValueList`** (`pasted` per item). Facts about one thing, one per row; a value that is pasted into another place
(`pasted`: a webhook address) wears its copy button always and wraps, so what is copied is what is seen; system values in mono and copyable, cut at the
end of their line with the whole as their title. A value that is a sentence (`mono: false`) is never
cut: it takes the lines it needs, and `wrap` gives a card too narrow for a host name or a kernel the
same for its system values. Not tabular data.

**`StatTile`** (`loading`). One key figure with a line of context, in a dashboard's figures band.
On a phone, two to a row, its label, value and context wrap instead of being cut. While the
reading is on its way the tile keeps its label, shows skeletons for the value and the context,
and is `aria-busy`.

**`Meter`**, **`ResourceMeter`**, **`Progress`**. A level in a range (with a glyph and a word
when it is a problem); use against a limit; progress of a task with a known end (amber). The track is
carved and the fill is stone until the level is a problem.

**`toast`**. The outcome of an action whose effect is not visible on screen, or a job queued
with a link. Never a success that is visible anyway, never an error that has to be read and
acted on in place.

**`Skeleton`**, **`Spinner`**. Loading with the content's shape (the region `aria-busy`); a
spinner only inside a control or a `JobProgress`. The region is whatever holds the skeleton: a
`LoadingRegion`, a `DataTable` with `loading`, a `Card`, `Section`, `StatTile` or `TabToolbar`
with `loading`, a `LogViewer` with `loading`. A skeleton with none of them around it is silent to
a screen reader, and the route tests (section 9) refuse it. The skeleton has the shape of the
content: `SkeletonRows` (the rows of a `RowList` or a `DataTable`), `SkeletonCard`, `SkeletonSection`
and `SkeletonText`, with heights from the control tokens, never a rectangle of a height guessed by
eye (ST-1).

**`TextLink`** (`size` `inline | ui`, `variant` `link | row`, and the router's `to`, `params`,
`search`) and `textLinkClassName`. A link to another page of the console inside a sentence, a card
or a notice: the accent, underlined on hover. `inline` takes the sentence's size; `ui` (13px) stands
on its own. `variant="row"` is the name of a row in a list or a table: medium weight in the text
colour, underlined on hover (ratchet `row-link-class` counts the classes copied from file to
file). Not for an action (`Button`), a page outside the console (`ExternalLink`), or a link
drawn as a button (`buttonClassName` on a `Link`). A link the router does not build (a
`ServerLink`, a download) takes `textLinkClassName`.

**`FlowDiagram`** (`layers`, `edges`, `label`, `highlight`, `onNodeFocus`, `summary`, `table`,
`animated`; types in `flowDiagram.types.ts`, layout in `flowDiagram.layout.ts`). How requests
travel through a web server, read only: fixed layers from left to right (ports, names,
locations, destinations, backends), never a free graph. Rules:

- The layout is columns and deterministic (`layoutFlow`): the tallest column is stacked, what
  fans out (a server) sits level with the first thing it opens, what fans in (an upstream)
  level with the middle of what leads to it, settled so nothing overlaps and the caller's order
  is kept; `group` keeps elements together under a caption (locations per server block,
  destinations per upstream). The same data is always drawn at the same coordinates. Columns
  fill the width they are given, 160 to 240px per node; a tall column scrolls inside the box
  (a production site's 25 locations).
- Every element has an outline and an icon of its kind (listener, server, location, upstream,
  upstream-server, static, redirect, other) and its value in mono; colour only says whether it
  answers, with the glyph and word of `StatusGlyph` (`ok` responds, `fail` does not, `unknown`
  grey, `none` nothing drawn).
- Connections are curves drawn by React in SVG; traffic is dashes moving along them through
  the `flow` animation (faster over TLS), a WebSocket is a double line. With `highlight` (a
  route from "Try a URL") only that route moves and the summary names it. Pointing at or
  focusing an element lights its whole way; pressing it keeps it lit (`aria-pressed`) until it
  is pressed again or Escape.
- Every element is a button with an accessible name ("Location /api/: Responds"); under the
  diagram a written summary (counted from the data unless `summary` is given, naming what does
  not respond) and the same connections as a table behind a disclosure (A-7). Below 640px it is
  a list per column, each element saying where it leads.

**`FlowDiagram`** draws fixed columns from left to right and what connects their elements, never a
force layout and never a stored position: a site's traffic (ports to backends) and, with `relation`
on its edges and `legend` and `nodeLink` of its own, how a server fits together (the Map: names,
applications, databases, scheduled work, data). **`Chart`**. A time series (see 2.2). **`LogViewer`** (`loading`: busy, with the shape of lines
under what it waits for, until the first arrive; chips to keep only some levels, each with its word,
its glyph and its count, offered only when the output has more than one level; a time toggle; full
screen where the browser has it; `onOpenLine` makes the lines something to act on, as in the
journal's "view in context": the region becomes a listbox with one selected line, a click or Enter
opens it, a click that ends a text selection opens nothing, and `highlightId` marks the line a
context is about), **`SystemOutput`**: the system's output,
verbatim, copyable. **`Mono`**: a system value. **`ServerText`** (`coded`, `text`, `part`): a sentence the server says as a code, in the console's language, with a command set in `Mono` and the server's English when the console has no entry; it draws no element of its own, so it takes the type of the paragraph it is in (a notice, a field's error, a row's help), never a box of its own, and a system error is still shown verbatim beside it (Principle 5). **`Kbd`**: a key. **`CommandHint`**: the CLI
equivalent. **`RelativeTime`**: "3 min ago" with the exact moment in a tooltip.
**`SegmentedControl`**: one of a few exclusive views (a chart range). An option may carry a `count` (a number
after its name, in its own text so the name a screen reader reads has a space before it): the servers of a
fleet's list, with how many applications each has.

**`TerminalScreen`**. A shell drawn on a canvas, from an emulator that is the console's own
(`lib/terminal`): a library that injects a `<style>` element cannot run under the content security
policy, and nothing here writes HTML. Its colours are tokens, read where it is drawn (the chart's
reader) and again when the theme changes: a program's red is the failure colour. The keyboard
lands on a text area, so an input method and a virtual keyboard work; Ctrl+C is an interrupt,
copy and paste are Ctrl+Shift+C and V, and Escape then Tab leaves it, because Tab belongs to the
shell and a terminal that keeps the keyboard for good fails WCAG 2.1.2. `textView` puts the same
screen where a screen reader can read it and `announce` reads new lines aloud (off by default: a
program that redraws the screen would be read again each time). What a shell prints is the
system's own and is never translated.

**`ErrorBlock`** (`onRetry`, `action`), **`QueryState`**. A failure the way the console always
shows one, and the four states of anything loaded (see 6.5 and 6.6). `action` is one follow-up
beside Try again, after the system's words ("View output"); `JobProgress` passes its own
`action` to it when the job failed. `QueryState` takes `label` (a noun that completes "Loading ...",
"Could not load ...", right in English and wrong where the article or the order changes) or, for a new
screen, `sentences`: the three said whole in the screen's own catalog.

**`DangerZone`**, **`DangerAction`**. Irreversible actions, last, each saying what it destroys.

**`SaveBar`** (`changes`, `onDiscard`, `onSave` or `form`, `saving`, `busy`, `saveLabel`,
`saveMenu`, `secondary`, `guard`, `yieldPrimary`). The one way a form is saved. With `saveMenu` (the
same save done another way, such as "Save only" beside "Save and restart") Save is a `SplitButton`
whose main part is `onSave`. It owns two things so that no
subsection writes its own: the question asked once before the page is left with changes (FM-3: a
`LeaveGuard`, a `ConfirmDialog friction="simple"` "Discard unsaved changes?" with "Keep editing"
focused, over `useLeaveGuard`, which blocks a navigation and warns the browser before the tab is
closed) and the header's primary stepping aside while there are changes (FM-2). A wizard or a file
editor with no bar places a `LeaveGuard` itself; a dialog or a drawer asks when it is closed.

**`Stepper`**, **`WizardActions`**, and the templates `ListPage`, `DetailPage`,
`SettingsLayout`, `DashboardPage`, `Wizard`, `FileEditorPage`, `AuthLayout`: section 4. The
`Wizard`'s `summary` is a landmark named "Chosen so far" (`summaryLabel` to rename it);
`FileEditorPage`'s `footer` holds its `CommandHint`, under the bar. The `Wizard`'s summary sits
beside the step from 1536px (16rem at least) and follows it below that. `SettingsLayout` renders only
the navigation the screen shows (the side list from 640px, the index on a phone's index route),
so the page has one navigation landmark in a browser and in a unit test alike.

**The content kit of 4.2** (items 109, 121, 122, 123), each with its specimen in `/__design`, "Components (4.2)", and its rules in 6.16:
**`SurfaceSection`** (`title`, `icon`, `description`, `help`, `status`, `actions`, `flush`, `level`, `loading`): a section that is a slab, its head
row saying what it is. **`FirstUse`** (`action`, `secondary`, `drawing`): the first time inside a section. **`ResourceCard`** (`name`, `level`, `icon`
or `logo`, `status`, `facts`, `actions`): a thing that exists. **`NameWithTags`** (`tags`, `shown`, `renderTag`): a name and its tags on one
line, "+N" for the rest. **`SaidLines`** (`label`, `lines`): what a system answered about what failed, under the table. **`GuardedButton`**,
**`Can`**, **`ReadOnlyLine`**, `useCan` (`components/page/permissions.tsx`): who can do what. `DataTable embedded` draws a table inside a
slab with no slab of its own.

**`ICONS`**, **`ICON_SIZE`** (`components/ui/icons.ts`) and **`useMediaQuery`**, `SM_UP`,
`LG_UP`: section 2.8 and the `sm`/`lg` breakpoints in code.

---

## 6. Patterns

### 6.1 Showing state

| Context | Form |
|---|---|
| A resource's header | `StatusPill`, pill, `md` |
| A table column (the second) | `StatusPill appearance="inline" size="sm"`, `w-32` |
| Counters in the sidebar and top bar | `StatusGlyph` of 10px and the number; the accessible name is a sentence ("2 services failed") |
| Running text, a tooltip | The word in `stateTextClass`, with its glyph when it is a warning or a failure |
| An aggregate | The colour and glyph of the worst state in it |
| Whether a feature is on | `StatusPill size="sm"` in the header of its `Card` (`status`), never a block and never a neutral `Notice` |
| Whether an option is on | The control itself: the switch of its `Setting` row says it |
| An on/off as a label: a channel's card, a table cell, a fact | `StatusPill state="running"` (on) or `"stopped"` (off) with the word, `size="sm"`: on its soft ground in a card or a fact, `appearance="inline"` in a table. **A feature's on/off as a label is a state badge, never bare text.** A setup left half done is `queued` |
| Never | Colour alone; a `Badge`; a dot of your own |

Queued is a still glyph: waiting is not work. A job running on an application is the
application's state while it runs.

### 6.2 Actions and confirmation

- One primary per view (the design contract counts them). In a row, actions live in a menu named
  "Actions for {name}".
- Friction is proportional to what can be lost:

| Friction | When | Examples |
|---|---|---|
| `none` | Reversible, or its effect is visible at once | Enable or disable a cron job, change a saved value |
| `simple` | One resource that can be made again, or an interruption that loses no data | Stop an app, remove a domain from an app, remove a schedule or a destination, revoke a token, remove a server from the fleet, delete a database user |
| `type` | Data lost for good, or a wide reach | Delete an application, drop a database, delete a service or a site, delete a backup, restore over existing data |

- The friction is declared at every call: there is no default, and a call that passes the name to
  type has said `type` (FM-6). Removing a domain from an application and removing a server from
  the fleet are `simple`, as the table says; turning off a safeguard is `simple` too (SW-5).
- There is one confirmation component, `ConfirmDialog`: a plan with a before and an after is its
  `children` in a wider `size`, and the last step of a destructive flow is never a danger `Button`
  written by hand outside it and `DangerZone` (ratchets `danger-button-outside-confirm` and
  `confirm-hand-rolled`).
- **DL-1** A deletion shows one box for each thing of the resource that holds data (an application's files, the volumes
  of its containers, its own databases, its certificate), each read from what the server says deleting would touch
  (`GET /api/apps/{domain}/deletion`) and none ticked. A folder that is not Noust's (an adopted stack's) goes only when its
  box is ticked, and ticking it names it, because that is the one way the server removes it; a database another resource
  also uses is said to stay and has no box. The question that follows says, from the boxes, what goes and what stays.
  *Enforced by:* `features/app/deletion.test.ts`; `features/app/pages/DeletePage.test.tsx`; `e2e/app-settings.spec.ts`.
- After acting, the interface itself changes (the row goes, the state changes), the change is
  announced to screen readers, and focus returns to something stable. A toast only when the
  result is not on screen.

### 6.3 Sudo mode

An action the backend guards with `require_elevated` answers `403 elevation_required`; the
console opens "Confirm it's you" once and retries the action by itself when confirmed. That
answer is not an error for the operator. The dialog says what is about to happen and, on a
fleet, on which server. On a fleet the node decides what needs sudo mode
(`x-noust-requires-elevation`); the central asks its own operator and vouches for the call.

The dialog asks for exactly what the session says confirming takes (`elevation_factors` and
`elevation_requires_password` of `GET /api/auth/session`): one code field and, when the account has
a passkey, the passkey button; the password field only where the server asks for the password
again. It states how long the confirmation lasts with the server's own numbers, and the console
never decides on its own that the window closed: a cached `elevated_until` that has passed is
checked against the server before asking, because the window moves while the operator works.

### 6.4 Jobs and progress

A job has four states (queued, running with a step, succeeded, failed or cancelled) and shows
in three places: `JobProgress` where it was started (an app's header, a domain's tab, the
wizard's last step), its row in Activity, and a toast when it ends if the operator left the
page. The live log opens in a drawer. Start and success are announced politely, a failure
assertively, with the error verbatim; an event is announced once, by one channel.

**The task centre** (4.2, item 114) follows every job and every long operation of every server from the
top bar, whichever page started it. One store (`features/tasks/store.ts`) holds them, fed by the reads of the
server (`/api/jobs/active`, the history, `/api/operations/active`, and a central's `/api/fleet/jobs/active`) and
by the `job` and `operation` events; nothing else writes it and no screen keeps a state of its own for the
centre. The icon counts what runs in the shape and colour of work in progress; its panel puts yours first
(running, then queued), then "Now on the server" (what you did not start), then what ended lately with its
result (a failure says where it stopped and what the tool printed, verbatim), and on "All servers" it is
grouped by server, a server not yet updated saying it will count its tasks when it is.

- **Pinning is a choice.** A running task can be pinned as a chip at the foot of every page, with its progress
  and step; it keeps its result until it is let go, and a reload keeps the pin. Nothing is pinned by itself.
- **The end is told once, to the one it concerns.** A task of yours that ends while you are on another page
  is one toast with "See"; on its own page (or the page of the application it acts on) it says nothing,
  because you are looking at it; and the server's own notice for it is not said a second time. What is not
  yours keeps the server's notice.
- **The browser's notification is offered, never started.** The first time you leave a long task of yours, a
  toast offers it; only your yes asks the browser for its permission, and it is shown only while the tab is hidden.
- **A task has a page** (Activity > Tasks > one): its steps (a deploy's five phases), who and where, its log as
  it is written, Pin, Cancel where the queue allows it and Try again for a failed deploy.

### 6.5 Errors and system output

An error is: what failed (four words: "Could not load applications"), the fix above (the
backend's `hint` wins over the catalog's), what the system said below (mono, verbatim, uncut,
copyable), and "Try again" when it can be retried. A field's error is in its `Field`; a load's
in its place (`QueryState`); an action's where it was started (the dialog, the `JobProgress`,
or a persistent error toast with the `detail`). `role="alert"` only for the outcome of something
the operator just did. Never "Oops" or "Sorry"; an unknown cause is not invented.

**A fault is reported from where it is met** (4.2, item 127). The screen of a page that broke says that the rest
of the console works, shows the error verbatim and offers two buttons, Try again and Report a problem; the help
menu of the top bar (and "More" on a phone) opens the same report from any page. It is one dialog of three steps
(what happened, what is sent, what happens on GitHub) that ends in one .zip the person downloads and in GitHub's
form opened with its fields filled in: Noust publishes nothing, the person does.

- **What is sent is what is shown.** Each part of the file (the server's support report, the browser's block, the
  person's own text when the address of the form cut it, each image) opens and shows exactly what is written in it,
  and what looks like a secret is marked as hidden. The server masks its report; the browser masks its own text with
  the server's shapes (`api/secretShapes.gen.ts`, written by `scripts/generate_secret_shapes.py` from the tuple
  `mask_secret_shapes` walks), never with a list of its own, because the report is also made when the server is down.
- **The browser's last errors are kept in memory only** (`lib/errorBuffer.ts`: twenty entries from uncaught errors,
  unhandled rejections, `console.error` and failed API calls), and masked when a report is made, not before.

### 6.6 Empty, loading and partial

Everything loaded has four states: a skeleton shaped like the content (the region
`aria-busy`, a `sr-only` "Loading …"), the error, the empty state, the content. `QueryState`
does all four. Under a second, nothing blinks; 1 to 10 seconds, a skeleton; beyond, progress
if the end is known, otherwise a background job with its `JobProgress`. A skeleton lives in a
`LoadingRegion` (`QueryState` uses one): after two seconds it also says, visibly and with a
spinner, what it is reading, so a view that reads the machine never looks stuck. A page does not
draw its header over a blank area while its first read is out: its body holds the skeleton from
the first frame (a list keeps its filters and its table, the rows being placeholders), and an
empty state is drawn only once the answer is known, never because the answer has not come. A
page that waits on something it needs before it can show anything else (the rows behind a notice
that must not push them down) shows the skeleton for that wait too, and when the wait ends it
draws what came as new elements, never as the placeholders pushed down: `ListPage`'s `arrival`
changes at that moment, and a page whose body depends on a second read (a deployment on whether
its app is a static site) holds the skeleton until both are in. Something that will sit above
or beside content (a button, a note) has its place held while it is read. A route that keeps the router
waiting past a second (the session, a page's code) is drawn as `RoutePending`, a skeleton in the
page's place; before that, the previous page stays and nothing blinks. A refresh that fails
keeps the last answer on screen with a compact error above it. When an optional dependency
fails, only the affected block says so; controls are not disabled for it and "undefined" is
never printed. An empty cell is `EmptyCell`.

- **ST-1** Everything that loads draws its four states (skeleton, error, empty, content) through
  `QueryState`, or `LoadingRegion` when it is not a query. No `aria-busy="true"` and no
  `isError && data === undefined` ternary is written in a feature. A skeleton has the shape of the
  content (`SkeletonRows`, `SkeletonCard`, `SkeletonSection`), never a rectangle of a height guessed
  by eye, and nothing moves when the data arrives. *Enforced by:* ratchets `hand-aria-busy` and
  `hand-error-ternary`; `routes/loading.test.tsx`; `e2e/layout-shift.spec.ts`. The codemod
  `panel/scripts/codemods/loading-region.mjs` rewrites the common hand-written region.
- **ST-2** An empty list is an `EmptyState` with its `variant`: `firstUse` in the place of the
  content (one per page), `inline` inside a section or a filtered table. A bare paragraph is not an
  empty state. *Enforced by:* the type (`variant` is required once the last page has moved); ratchet
  `inline-empty-by-hand`.
- **ST-3** A refresh that fails keeps the last answer on screen with a compact error above it; an
  optional dependency that fails says so in its own block and disables nothing. *Enforced by:*
  `QueryState.test.tsx`; `e2e/late-answers.spec.ts`.

### 6.7 Forms and the save bar

Fields stacked vertically, 20px apart, each in a `Field`. Validation on submit, then on leaving
a field once a submit failed; the server decides (the 422's `fields`). A button is disabled
only when there is nothing to do (no changes) or while it works: an invalid value does not
disable it, the error says what is wrong where it is wrong. Settings are saved from the
`SaveBar` (a settings subsection) or the form's own footer (a dialog). System values are typed
in mono, without autocomplete or spellcheck; a unit is a `suffix`. A secret is shown once, with
a copy button and "Done". Related fields are a `fieldset` with a `legend` (`FieldGroup`); long
forms go in a drawer.

- **FM-1** A settings subsection saves in one way: a form with a `SaveBar`, or a list of immediate
  settings. Never both, and never a "Save" in a card (a dialog's or a drawer's footer is the only
  other place). *Enforced by:* ratchets `card-save` and `switch-in-form`.
- **FM-2** One primary per view, including while the `SaveBar` has changes: the header's primary
  becomes secondary (`DemotePrimary`) until the changes are saved or discarded. An empty state or a
  card never repeats a primary the header already has. *Enforced by:* `e2e/design-contract.spec.ts`
  (one `[data-variant="primary"]` on every route of `e2e/routes.ts`, clean and dirty).
- **FM-3** Leaving a form with unsaved changes (navigation, tab close) asks once: "Discard unsaved
  changes?". `SaveBar` owns the guard (`LeaveGuard`); no page writes its own. *Enforced by:*
  `e2e/leave-guard.spec.ts`; ratchet `own-leave-dialog`.
- **FM-4** A submit button is never disabled for an invalid or incomplete value, in a page, a
  dialog or a drawer. Pressing it focuses the first field with an error and says what is missing. A
  button is disabled only when there is nothing to do or while it works. *Enforced by:* ratchet
  `submit-disabled-invalid` (a heuristic: it reads the `disabled` of the button itself for an empty
  or invalid value, and misses a flag computed elsewhere);
  `e2e/design-contract.spec.ts` on a sample of dialogs.
- **FM-5** A setting that was saved but does not run yet is a chip in the page header's status line
  ("3 not applied", `statusExtras`) that opens the review; it is not a banner on every tab.
  *Enforced by:* `e2e/app-overview.spec.ts` (the chip shows with another banner present).
- **FM-6** One confirmation component: `ConfirmDialog`, with `friction` always declared (`none`,
  `simple`, `type`; there is no default). A confirmation that needs a plan or a host name uses its
  `children`, `size` and `confirmText`; there is no second dialog and no hand-written
  `variant="danger"` outside `ConfirmDialog` and `DangerZone`. *Enforced by:* the type; ratchets
  `confirm-hand-rolled` and `danger-button-outside-confirm`.
- **FM-7** A success toast is only for what is not visible on screen. A row that appears, a row
  that disappears or a dialog that closes says nothing. *Enforced by:* ratchet `toast-success`
  (only goes down); G-7.
- **FM-8** A subsection of settings fits in one and a half screens at 1440px; when it does not, it
  is split (account, sign-in methods, compliance controls and the ENS report are four subsections,
  not one). *Enforced by:* `e2e/settings-length.spec.ts` (heights per route, a baseline that only
  goes down; over 1400px needs a reason).

- **FM-9** A page that gathers several forms is one form (4.2). The four pages of an application that gather what were
  subsections draw each part's cards under its heading and save them from one `SaveBar` (`SaveHost`): a part registers its
  changes where it used to draw its own bar (`PartSaveBar`), and Save checks every part that changed before any is sent, asks
  "Confirm it's you" once and saves them in order, a part that fails keeping its changes beside its fields. The parts a person
  seldom opens are folded under a button each (`FOLDED`, `PartFold`), still mounted, so a change in a folded part counts on
  the bar. *Enforced by:* `features/app/settings/AppSettings.test.tsx` (one bar per page);
  `features/app/settings/sections.test.ts`; `e2e/app-settings.spec.ts`.

### 6.8 Tables

- Column order: **identity first** (the name; it names the row for screen readers and opens
  it), **state second** (`StatusPill inline sm`, `w-32`), then attributes, time, **numbers
  right-aligned in mono**, and the row's actions last in a menu.
- Initial order: newest first in histories, alphabetical in inventories; severity is one click
  on the state column (`STATE_RANK`).
- Density per D-2; truncation is a last resort, with the full value in a tooltip, and system
  values are truncated in mono.
- A cell has no vertical padding: its height is a minimum, a row is as tall as its tallest content,
  and a 32px control and the row's border already fill 44px. A cell that stacks lines (a name with its
  tags under it) says so with `Column.stacked` and keeps 6px above and below, so its content never touches
  the row's border; a row without such a cell is as tall as it always was. On a phone a card holds its
  name and its facts in blocks, never in a paragraph, because what a cell holds may be a list. *Enforced
  by:* `e2e/table-cells.spec.ts`.
- **One table of applications** (4.2, item 122). The list, a project's and the fleet's are `AppsTable`: the domain with its
  tags on its line (`NameWithTags`; a pull request preview wears the tag "preview" and says whose it is to a screen reader, so
  no row has a second line), its state, its type and its last deploy, and where there is more than one place to tell apart its
  project, its environment and its server. The views of the Applications column (by project, by state) are this table under a
  heading each (`embedded` in a `SurfaceSection`), never another table. *Enforced by:* `features/apps/AppsViews.test.tsx`;
  `features/apps/groups.test.ts`.
- **One table per group** (4.2). Rows that belong to named groups (the Software page's pieces by what they are for, the
  Applications column's views by project or by state) are the same table under a heading each: a `Section` or
  `SurfaceSection` titled by the group, a table named by it (`caption`: "Software: The machine"), and the group left out when it has no row
  to show. Never one long table with a column that names the group, header rows inside it, or a second kind of table for the
  grouped view: sorting, paging, the phone's cards and the keyboard are the table's, once, and a group is a place to read, not
  a thing to learn. A group of a single row is still a table. *Enforced by:* `features/server/software/SoftwarePage.test.tsx`
  (a table for each group, none for an empty one); `features/apps/AppsViews.test.tsx`; G-2.
- Headers are 12px, 500, `text-muted`, sortable with `aria-sort`.
- Paging is the operator's: 20 rows to a page to start with, 50, 100 or all on request, kept
  per list, with "Showing X-Y of Z" and a page-size control that is always where the rows are
  (`DataTable pagination`, `TablePagination`). A list of 20 or fewer draws none. A list the
  server pages uses "Load more" and says how many are loaded; virtualise only beyond 100. The
  last page is never padded: a grid with an incomplete last row makes people think there is no
  more.
- On a phone, card rows (the table's default) with the actions in view.

### 6.9 Filters and search

`FilterBar`: the search box (`type="search"`, `/` focuses it, its value in the URL's `q`),
filters as `Select` or `SegmentedControl`, the count ("3 of 17 applications") at the right. A
filter that empties the table shows "No matches" with "Clear filters". A list of resources that live in a
project (applications, databases, sites) adds a Project and an Environment filter and the same
two columns, **only where there is more than one place to tell apart**: one team with one project
and one environment draws none of it, so a server nobody organised looks as it always did
(`features/teams/ownership.tsx`). The team the operator works in narrows the list before any
filter does; the switcher in the top bar says which.

### 6.10 Charts on a page

One range control per page; two columns from 1024px, one below; each chart with its `h3`, its
summary and its enlarge action; the rules of 2.2.

### 6.11 Which server am I on

On a fleet: the server's name is always on screen, in the places that are about a server and in no
other: in the scope selector of the top bar; in the second column of an application, under its name,
where it runs; in the machine strip; and in every confirmation, sudo dialog and mutation toast
(`ConfirmDialog server`: "Delete shop.example.com on web-2"). It is not said above the title of a
page: the owner asked that a header never repeat the server (4.1, item 92). No tint of frame or
background tells servers apart: colour already means state. A hub offers no local pages, and the
selector is always visible when there are nodes. A lone server with no nodes draws none of it. The
two scopes are one server and "All servers" (the fleet's pages): the central is one more server of the
list, marked "central", and there is no third place. The main bar is the same areas on both and each
area opens at its fleet face where it has one (NV-10, section 6.14), so the destinations are never the
last server's under another's name. The pages that belong to the instance and to no server (the
accounts, the tokens, the compliance, the audit) leave the selector where it was and say "All the
fleet" beside their name, which is why the server a person was on is still the one they come back to.
### 6.12 Notifications: which channel says it

An event is said in one channel, never two:

| Channel | When | Component | Stays | ARIA |
|---|---|---|---|---|
| Field error | A control's value is wrong | `Field error` | Until fixed | `aria-invalid`, `aria-describedby` |
| Risk or status line | What could go wrong with an option, the status of a field or a row, a one-off result | `InlineMessage` (a `Setting`'s `risk`) | While it is true | none, or `status` if `live` |
| Inline notice | Something persistent about a form, section or dialog | `Notice` | Until resolved or dismissed | none, or `status` if `live` |
| Banner | The state of the page or the server | `Notice variant="banner"` | Until resolved | none |
| Error block | A load or an action failed, with the system's words | `ErrorBlock`, `QueryState` | Until retried | `alert` only if `live` |
| Toast | The outcome of an action not visible on screen; a job queued | `toast.*` | Success and info 5s; error and warning until closed; 3 at most | the announcer's polite or assertive region |
| Inbox | What failed on its own or needs a decision, while the operator was elsewhere; the bell in the top bar | the inbox (`Needs you`, `Recent`) | Until resolved | the announcer's polite region, never a counter that changes every few seconds |
| Dialog | A decision that blocks | `Dialog`, `ConfirmDialog` | Until decided | `dialog`, `alertdialog` |

A toast's title reuses the action's verb in the past tense ("Deployed shop.example.com"), with
at most one action. Success is said sparingly: only when it is not evident from the interface
(FM-7). A toast acknowledges what the operator just did; what failed on its own or needs a
decision stays in the inbox until it is resolved; Activity is the history (NV-17, 6.14).

### 6.13 Keyboard and real time

`Ctrl/Cmd K` opens the command palette (NV-14, section 6.14), which finds anything the console
lists, by any name it answers to (checklist M-23): every screen of the register, by its name, by its
keywords and by the names it had before (the place it lives in is said at the right of the result,
dimmed: "Server › System updates"); applications (domain, name, type as a person says it); databases;
sites; certificates; services; servers; the newest deployments (by application, commit or message);
teams and projects; and the create commands (New application, from a recipe, import, adopt, New
database, New project, New team, Add a server) and the verbs on an application (checklist M-25:
Restart, Stop or Start, Roll back, Update, group "Operations", found only once the verb is typed, and
listed first on the application's own page). A verb never has an implementation of its own: it is the
application's button run by the same hook, with the same dialogs, so its friction (6.2), its sudo mode
(6.3) and its permission are the button's; what the principal may not do in the application's team
is not offered. Each result is in its group, with what tells two of one name apart beside it (a type,
a time left). A pill at the left of the field says the scope the palette is working in (the server on
a fleet, the application on screen); `>` limits it to actions, `@` to changing scope; with nothing
typed it offers the three most recent. Its lists are the console's own, read when it is first opened
and only those the account may read, and a finished job marks them stale (`applyServerEvent`);
nothing is read until it is opened.

`g` then a letter goes to an area of the bar, at its fleet face on the fleet's pages; every area has
one (NV-6) and the shortcuts dialog (`?`) lists them. A shortcut matches the character the key
produces when it is a Latin letter or a symbol, and the letter of the physical key (`event.code`)
when it is not, so it works on AZERTY, QWERTZ and non-Latin layouts. `/` focuses the page's search.
No single-key shortcut acts unless the focus is in a list or table. Every shortcut is in the
shortcuts dialog and in its control's tooltip. A live stream's state ("Live", "Reconnecting") uses a
`StatusGlyph` and a word, at the end of the machine strip so the readings that arrive after it never
push it across the bar, and the readings give way whole, from the right, when the words of a language
need more room than the strip has (the badge never does: "En vivo" is longer than "Live", and so are the
service counts); numbers that change every few seconds are never live regions.

### 6.14 Navigation and where things live

Four questions, answered in four places: **where am I** (the scope selector, and team > project > environment above the title), **what is here** (the main bar's thirteen areas and, inside one, the second column), **how is it set up** (Settings, grouped by whose it is) and **how do I get anywhere** (the palette). Nothing answers two of them.

- **NV-1** Every screen is declared once in `app/nav.ts`: path, parent, kind (`area`, `tab`, `section`, `hidden`), scope, label, command, keywords (the old names included), icon, shortcut, permission, and for an area where its fleet face is (`fleetTo`), for a section what it is for (`intent`) and the group of its column it is drawn in (`column`). The main bar, the second column and its sheet on a phone, the settings navigation, the breadcrumbs, the `g` shortcuts and the palette read it; no component lists destinations of its own. The bar and the column read the same entries (`app/columns.ts`, and `features/nav/views.ts` for the fixed views of an area that is one page), so they cannot differ. *Enforced by:* `app/nav.register.test.ts` (a route with no entry and an entry with no route fail); `app/columns.test.ts`; ratchet `destination-outside-register` (a `LinkTab[]` or `NavItem[]` literal outside `app/nav.ts`; 7 files to 0).
- **NV-2** Four levels; the first three are routes: L1 area (the main bar), L2 section (the second column), L3 subsection (T3's side list), L4 detail (drawer, dialog, anchor or search parameter). Never more than two bars: the main bar and the second column, and the sections of a resource take the place of the area's in the same column (NV-22). A URL has four segments at most after `/n/<server>/`. A fifth level is never a route. *Enforced by:* `nav.register.test.ts` (the depth of every route).
- **NV-3** Budgets. The main bar: thirteen areas in six titled groups, with Settings pinned under them, the same with or without a fleet (Home; Applications and data: Applications, Catalog, Databases, Scheduled tasks; Network: Domains, Network; Observe: Logs, Alerts, Activity; Protect: Backups, Security; Infrastructure: Server, which is Servers on All servers). A column: about sixteen entries, grouped (an application's has seventeen, in five groups, and the one over is accepted: 4.2 design, 10.3). A strip of view tabs, where a page has one (NV-23): 8; a section that repeats another area's view is a filter of that view, not a section. A settings list: 8 items per group and 7 groups, 26 in all (the server's and the instance's lists of 4.0 are one now, grouped by what a section is for). Over budget a destination is merged, moved to Settings or left to the palette; there is no ninth tab. The budgets are regression guards, not a dogma (nothing backs a universal "three clicks"): what is held is the click count of the reference tasks and "name, then Enter" in the palette; a change that raises a budget says so and why. *Enforced by:* `nav.register.test.ts`; `e2e/click-budget.spec.ts`.
- **NV-4** What earns a sidebar destination: a noun the operator says every day or every week; it lists what exists on the server or shows what happened; most installations use it. Not one-time setup, not configuration (that is Settings), not a catalogue of what to create (a tab of what it creates), not another destination's filtered view, not a module that is off. *Enforced by:* G-8 (the PR carries the register entry and these answers); `nav.register.test.ts` for the count.
- **NV-5** Names. An area's label is one or two words and a noun, without "and" ("Domains", not "Domains and certificates"), and is the glossary's (1 of the 4.1 navigation design); the same word in the bar, the title, the tab and the palette. Two areas never share a name; a scoped view of the same concept may ("Logs" inside an application). A renamed destination keeps its old name as a palette keyword for at least one minor release (`RENAMED`: Overview, Cron, Server, Recipes and Projects are found by their 4.0 names). *Enforced by:* `nav.register.test.ts` (no "and", no duplicate, the thirteen names, `RENAMED` among the keywords).
- **NV-6** Shortcuts. Every area has `g` and a letter, listed in `?`, and a letter is taken in the same change as the destination; the same two keys open an area at its fleet face from the fleet's pages. A shortcut matches the character the key produces when it is a Latin letter or a symbol, and the letter of the physical key (`event.code`) when it is not, so it works on AZERTY, QWERTZ and non-Latin layouts. *Enforced by:* `nav.register.test.ts` (every area, no collision); `app/shortcuts.test.ts` with AZERTY, QWERTZ and Cyrillic fixtures.
- **NV-7** Where a setting lives. The first yes wins. It follows the person, not the machine: **Account**. It concerns one resource: **that resource's settings**. It concerns a group of resources (team, project, environment): **that group's page**. It is a setting of Noust, found by what it is for: **Settings > People and access, Notifications, Connections, Compliance and audit, API, Fleet or Noust**. Whose a section is (the selected server's, the central's whichever server is selected, the fleet's) is its `owner` and decides where a node's console sends it, not which group it is under. It is used once, or by few: **an "Advanced" disclosure inside its section, or the CLI with a `CommandHint`**. Ties go to the place nearer to what it changes. *Enforced by:* the `owner` of every settings entry in the register (a test fails without one); G-9.
- **NV-8** One home per concept. A concept has one canonical page; anywhere else it is a link, the same component filtered to that scope, or a read-only summary that says where it is managed. Theme and language live in the account menu only. The concepts with more than one editable home are listed with their reason, and the list only gets shorter. *Enforced by:* ratchet `concept-doors` (13 today).
- **NV-9** A settings section is one subject and fits in one and a half screens at 1440 px. Two sections of less than a screen on one subject are one section; one over a screen and a half is split. No section is called "Integrations" with unrelated cards, "Other" or "Misc"; "General" is only the first section of a scope. *Enforced by:* `e2e/settings-length.spec.ts`.
- **NV-10** Scope and content are apart. The scope is chosen in the scope selector of the top bar, never in the bar's list or a tab, and there are two: one server, and All servers. The central is one more server of the list, marked as the central; there is no "This central" and no third place. The bar is the same areas on both: from All servers each area that has a fleet face (Home, Applications, Domains, Backups, Servers, Activity, Alerts) opens it, the areas of a machine (Network, Security, Servers) list the servers with a summary of the area, and choosing one is choosing it in the selector; each area that has neither leads back to the server the person came from. The sections of Settings that are every server's (accounts, tokens, compliance, audit...) say "All the fleet" once for a group in which every section is, over the group and not on each row, and beside the name in a group that mixes; in the header of their page, on the title's line, with the sentence that says for how many servers they hold; and opening one never changes the selector. What a server shows before anyone signs in (the picture of its sign-in) is its own and never the fleet's (`Dest.own`). No page header names the server (6.11). Where there is nothing to tell apart (one server, one team, one project) none of it is drawn. The server's name is always on screen: in the selector, in the machine strip, in the second column of an application and in every confirmation. *Enforced by:* `app/frame/frame.test.tsx` (the same areas on the fleet's pages, at their faces); `nodes/nodes.test.tsx` (two contexts, and a selector that does not move on the pages of the instance); `app/frame/SettingsColumn.test.tsx` (the tag once per group); `e2e/settings-area.spec.ts`; `e2e/fleet-screens.spec.ts` and `e2e/servers-lane.spec.ts` (a lone server draws no selector).
- **NV-11** Settings groups say what their sections are for (people and access, notifications, connections, compliance and audit, API, fleet, Noust), the same on a lone server and on a fleet, so a person who does not know where a setting is finds it by its purpose. A section is in exactly one group. The list has a filter box from 12 items. `/settings` is the hub: every section as a door under its group, with one line on what is inside and, where it is known, what is true now (`SettingsHome`), so a first visit shows everything before the second column is read. The title of a section is its own name, never "Settings" repeated (NV-15). *Enforced by:* `nav.register.test.ts`; `e2e/settings-nav.spec.ts` (the list never outgrows the window at 1440 by 900: it scrolls inside, below the top bar, with its group headings and its filter).
- **NV-12** Disclosure has two levels: the visible section and one `Disclosure` inside it. Never an "Advanced" route or tab. A module that is off is absent, not disabled; it is found through the empty state of the area that would use it, the palette and the CLI hint. What a role may not see is absent from the palette too. *Enforced by:* `nav.register.test.ts` (no entry named "Advanced"); the palette coverage test (permissions).
- **NV-13** Off-strip views (Diagnose, Terminal, Files) keep a URL but need a contextual way in (a banner, a header action, a link from the Overview) and a palette entry. A view with neither is deleted. *Enforced by:* `nav.register.test.ts` (every `hidden` entry names where it is reached from and is in the palette).
- **NV-14** The palette finds every screen in the register by its label and by every keyword, says where it lives ("Server > System updates"), finds every resource the operator may read, and runs verbs and creations. Order: exact, prefix, word start, substring, keywords, subsequence only from four characters. A scope pill shows the current scope; `>` limits to actions and `@` changes scope. With nothing typed it offers the three most recent, and the CLI command when a result has one. *Enforced by:* `app/CommandPalette.coverage.test.tsx` (every entry is the first result of its label and appears for each keyword).
- **NV-15** Breadcrumbs are hierarchy, never history, and they are generated: `crumbsOf` gives the entries above a page, the page's own is last, and a resource is named by what it is (an application by its domain, a database by its name; never translated). They are in the top bar on every page of two levels or more, and the first line of the page on a phone; an area's own first page has none, because the bar says where it is. The step of a resource and the page's own have a switch that lists what is next to it (the other applications; the sections beside this one), so a person jumps without going back up. No page writes its own trail or its own back link; on an organised server the place line (team / project / environment) stays under the title. *Enforced by:* `app/crumbs.test.ts`; `PageHeader.test.tsx` (the header draws neither a trail nor the server); ratchet `own-back-link` (1 to 0: `DeploymentPage`).
- **NV-16** Global state. The chrome holds four things: the machine strip, running jobs, what needs the operator (the inbox) and the live connection. One global notice at a time, by priority. A fifth indicator needs a row here and a design review. *Enforced by:* G-8; the unit test of the notice priority.
- **NV-17** Toast, inbox, activity. A toast acknowledges what the operator just did and is not visible on screen; what failed on its own or needs a decision stays in the inbox until resolved; Activity is the history. *Enforced by:* `e2e/inbox.spec.ts` (a failing alert is in the inbox after its toast is gone, and is acknowledged in the row).
- **NV-18** Changing the structure. Structure and look do not change in one release. A structural change ships in a major version; every URL that changes redirects, the old names stay as keywords, and the release notes carry the table of where each thing is now. *Enforced by:* `routes/redirects.test.tsx` (the `REDIRECTS` table of `app/nav.ts`).
- **NV-19** Icons. One icon for each object the console is about, from one map of the kit (`components/ui/objectIcons.ts`, `OBJECT_ICONS`): the register reads it (`features/nav/icons.ts` says which object each area and each section of a list on the side is), and so do the sections' heads (`Section icon`) and the buttons that act on an object, so a screen, a heading and a button of one thing look like one thing. The sidebar, the lists on the side (Settings, an application's settings, the account) and the palette draw it at 16px (`size-icon-md`) in the quiet colour of the list (`text-fg-faint`, `text-fg` on the current section: colour is for state), red only on the destructive section, and `aria-hidden`. A tab has none of its own, because no strip draws one (its width is the budget): the palette shows the icon of its area. No two entries of one list share an icon (I-5). *Enforced by:* `nav.register.test.ts` (the table holds the areas and the sections and nothing else; none repeats in a list); `components/page/SettingsLayout.test.tsx`; `components/page/Section.test.tsx`.
- **NV-20** The frame. One main bar and one second column from 1024px, whatever the person likes: there is no presentation to choose, no "one bar" and no switch for it, and the account menu has none. By width, below 1024px the areas are a bar at the foot (four areas and "More", which opens a sheet with all of them in their groups, then Settings and the account) and the column is the section selector at the top of the page, which opens a sheet with the same list (never tabs); from 1024 to 1439px the main bar is folded to icons and unfolds over the page without pushing it (its button says Close, Escape and a press outside dismiss it), because two open bars need about 1410px; and from 1440px it is unfolded and the saved choices (the fold, the width of the page) decide. The main bar is drawn unfolded by default, with an icon and a name, because thirteen icons are not understood at first and a phone has no tooltip; its groups carry a small title in sentence case and a joint (a dark line with the lit edge under it, the joint between two courses of the wall), which is all there is of a group when the bar is folded; it folds by a button with `aria-expanded`, and a folded icon says its name on hover and on focus. Nothing folds by use. *Enforced by:* `app/navLayout.test.ts` (every edge of the width rules); `app/preferences.test.ts`; `app/frame/frame.test.tsx`; `e2e/frame.spec.ts` (the widths 1920, 1440, 1280 and 390, with the keyboard and with reduced motion, both themes).
- **NV-21** The frame moves with the person. The raised piece of the current item slides to the new one when the route changes (the browser's back and forward too), in the main bar and in the second column, each with its own piece, by the move curve of 2.7 and never with `ease-in-out`; it does not slide when the page loads, when the bar folds or the window resizes, or with reduced motion. *Enforced by:* `components/ui/SlidingIndicator.test.tsx`; `e2e/frame.spec.ts` (the transition that runs, its duration and where it ends).
- **NV-22** Every area has a column, and so has every resource a person enters. An area with several pages lists them; an area that is one page lists its fixed views, presets of its own filters that live in the address and save nothing (`features/nav/views.ts`); Home lists its panels. Where a resource is entered (an application, a database, a service, a team, a server on a fleet) the column slides to its sections with the way back to the area ("‹ Applications") and a switch to its siblings, so a person jumps to another without going back to the list; the new list comes in decelerating and the old goes out accelerating and in less time, and with reduced motion it is instant. The column never disappears and a third never appears, so the content does not jump from one side to the other between two areas. Names come from the register: the same entries, drawn in the column, in the phone's sheet and in the palette. *Enforced by:* `app/columns.test.ts` (every area of the register has a column, and a resource's has its area's way back); `app/nav.register.test.ts`; `app/frame/frame.test.tsx` and `e2e/frame.spec.ts` (the slide, its two durations, reduced motion).
- **NV-23** Tabs only change the view of one thing. A strip of tabs never lists the pages of an area or the sections of a resource: that is the column's (NV-22). What stays a strip is the choice between ways of looking at the same object on the same page (a site's Text, Structure and Diagram; a zone's Records, DNSSEC and Delegation), each a view of one thing. A page whose sections were tabs in 4.1 (Alerts, Backups, Catalog, Projects, Fleet, Account, Domains, a database, a service, the views of Server) has them in the column. *Enforced by:* ratchet `link-tabs-in-feature` (`LinkTabs` written outside the kit and the shell: 15 files to 2 in 4.2, Home's and the Map's, which the Home lane removes); `app/columns.test.ts` (a section of a resource is not a tab of its page).
- **NV-24** No entry of a column leaves its column. Every entry of every column resolves to a route whose column is that column (an entry's own, or the resource's under it); what lives in another area is linked from the content, with the words of the page, and never listed as a section. The way back to the area is the one exit, and it is the head's. *Enforced by:* `app/columns.test.ts` (each entry of each column, with its parameters filled in, is under the column it is listed in).

### 6.15 Observability and data

- **LG-1** Every log surface (application, server, global, a service, a job) uses one toolbar: the
  query, level chips with counts, the source, the range (with "Live" as one of its options) and
  "Copy link". It keeps its state in the URL and has one query grammar. *Enforced by:* the unit tests
  of `LogToolbar`; `e2e/log-query.spec.ts` and `e2e/logs-probes.spec.ts` (the same controls on every
  surface); ratchet `live-switch`.
- **LG-2** Every log line can be opened in context and can seed a filter from its values where its
  source supports it; a link to a line carries the range and the line. *Enforced by:*
  `e2e/log-query.spec.ts`.
- **LG-3** A control that follows the output has one name and one place: the viewer's "Follow". A
  page never adds a second "Live" or "Follow" switch. *Enforced by:* ratchet `live-switch`.
- **AL-1** A firing alert or a failing probe is shown where the operator looks (the inbox,
  Overview's "Needs attention", the sidebar counter, the affected application's header) with its
  acknowledgement in place, not only in Alerts. The server lists both among what needs attention
  (`managers/overview.py`), so a fleet reading each server's list counts them too. *Enforced by:*
  `attention.test.ts` (the kinds `alert` and `probe`); `tests/test_overview.py`; `e2e/inbox.spec.ts`.
- **AL-2** Acknowledging is called "Acknowledge" and "Reopen", shows who and when, and stops the
  reminders; it never resolves. *Enforced by:* `i18n/verbs.test.ts`.
- **DB-1** A restore goes to a new database by default (`RestoreDialog`); over an existing one it takes a safety copy
  first, names it, asks for the name and shows what it will replace before it runs. *Enforced by:*
  `e2e/databases.spec.ts`; one `RestoreDialog` for applications and databases.
- **DB-2** A data-gap message under a chart or a log ("not recorded yet") is one line under its
  title, not a `Notice`. *Enforced by:* ratchet `notice-info-bare`.
- **DB-3** A database is read through its column, in three groups (View, Manage, Danger) made from what its engine says
  it can do: a SQL database has Data and Query, a key-value store has Keys and no Query, and Delete is alone and last. An
  address that names a section the engine has says so and leads back to the overview; it never draws an editor that will
  fail. *Enforced by:* `app/columns.test.ts`; `features/databases/DatabaseLayout.test.tsx`.
- **DB-4** A database whose engine does not answer is a state, said once for every section (the banner of the layout:
  "<engine> is stopped", with "Start the engine" as its one action) and never a skeleton that does not end or an error in
  the system's words. What needs the engine is not asked of it (no request is made), what does not (its backups, its
  settings, its delete) stays. The list keeps its row, marked stopped. *Enforced by:*
  `features/databases/DatabaseLayout.test.tsx`; `e2e/databases.spec.ts`.
- **DB-5** One dialog makes a database, empty or from a dump, on the engine chosen as a card, whether it is opened from the
  list, from an application or from Home. "Upgrade" says before it is pressed that it makes a new instance and moves the
  databases, in numbered steps taken from the plan, and is reached from the engine and from each database of an old one.
  *Enforced by:* `features/databases/CreateDatabaseDialog.test.tsx`; `features/databases/instances/Instances.test.tsx`.

### 6.16 Content that says what each thing is, and who can change it

A page of several subjects is read by its shapes before its words (owner items 109, 121, 122 and 123). The rules,
each held by the test it names:

- **CT-1** A page of more than one subject is made of `SurfaceSection`s: the head row (the object's icon, the title, one
  line, the section's own action and, if the rule is long, a "?") and the content inside. A heading over loose
  paragraphs, each with the weight of the next, is not drawn. Slabs are 24px apart (`Sections surfaces`). A table that
  sits in one is `embedded`, with its rows edge to edge under the head. *Enforced by:* `SurfaceSection.test.tsx`,
  `DataTable.test.tsx`; G-2.
- **CT-2** The first time of a thing inside a section is `FirstUse`: a drawing, one sentence that says what it gives you
  and the button that starts it (or none, where the page's header holds it). Never a muted line with the button
  somewhere else. The first time of a whole page stays `EmptyState variant="firstUse"` (IL-1). *Enforced by:*
  `FirstUse.test.tsx`, the ratchet `inline-empty-by-hand`; G-7.
- **CT-3** The facts of one thing are `KeyValueList` pairs; a value that is pasted somewhere else (`pasted`) wears
  its copy button always. *Enforced by:* `KeyValueList.test.tsx`; ratchet `copy-box-by-hand`.
- **CT-4** A thing that exists, and is the point of its section, is a `ResourceCard`: icon or logo, name, state, two or
  three facts, actions. More than a few of them is a table (G-2). *Enforced by:* `ResourceCard.test.tsx`.
- **CT-5** A name's tags are on its line (`NameWithTags`): the first ones as pebbles, "+N" for the rest in a popover,
  and the row is the one height every row is. Tags are never stacked under the name. *Enforced by:*
  `NameWithTags.test.tsx`; `Column.stacked` stays only for a cell that really holds more than one line.
- **CT-6** The long rule is behind a "?" (`HelpPopover`), a real button that a finger and a keyboard open. The page shows the
  state; the explanation is asked for. *Enforced by:* `HelpPopover.test.tsx`; ratchet `tooltip-by-hand`.
- **PM-1** An action declares the permission it takes (the server's own name, `apps.manage`). The page's primary
  action is a `GuardedButton`: for a role that cannot, it stays, disabled, with a lock and the reason one press away,
  because it tells a reader that the thing exists and who can do it. The actions of a row, a menu or a toolbar are
  inside `Can` (or the table is given no `rowActions`): they are not drawn, because a disabled button repeated on every
  row is noise. A page says once, at its top, that it can only be read (`ReadOnlyLine`). A button that is disabled for
  something other than the role (no account yet) says so in its "?" (`unavailable`), without the lock. A form or a
  dialog opened from a declared action says so on its write with a `design-exception: write-without-permission`
  and the action that gates it. *Enforced by:* `permissions.test.tsx`; ratchet `write-without-permission` (each
  `.mutate(` of a feature is covered by one declaration of the file: `GuardedButton`, `Can`, `ReadOnlyLine`, `useCan(`,
  `can("...")`; the counts may only go down; the four pages of K1 owe none).
- **CL-1** A `CommandHint` has a `reason`: `solo-terminal` (the console cannot do it), `otro-servidor` (it is for another
  server), `salida-del-sistema` (the system's own output names it). The reason leads the command when no label does.
  A command that repeats a button is taken out; an `EmptyState` has no command to draw beside its action. *Enforced by:*
  `CommandHint.test.tsx`; ratchet `command-hint-reason` (the commands with no reason, which may only go down; the four pages
  of K1 owe none); `features/terminalHints.test.ts` holds the areas of applications, data and network to the list of the
  commands that stay, each with its reason, and to no command in a first-use empty state.
- **HD-1** A header is as wide as its content: the title and its actions end where the content does, and never at the edge of
  the window (`DetailPage measure="settings"` for a settings section and the account). The master switch of what a page
  governs is on the title's line, right after it (`PageHeader master`, `SettingsMasterSwitch`). *Enforced by:*
  `templates.test.tsx`, `PageHeader.test.tsx`, `NotificationSettings.test.tsx`.
- **MS-1** A feature that lacks the tool it runs on says "X is missing" with `MissingSoftware`
  (`features/server/software/MissingSoftware.tsx`): one notice, drawn where the feature's own warning was, that names the piece
  by the Software page's name for it, says what the feature loses without it in a sentence (`whyKey`, or the piece's purpose),
  and offers to install it right there, as a task the task centre follows and the notice shows while it runs. It reads the
  registry the Software page reads, so the two never disagree about what is missing, and a page that reads something else says
  what to show when the registry has nothing to add (`fallback`). For a role that may not install, it says what it takes and
  whom to ask (never a disabled button repeated beside it); on a server that did not give the central access to its system, it
  says so and what to run there, which is a command with a reason (`otro-servidor`, CL-1). A page does not write its own
  paragraph about a missing program, a `Notice` with an "install" button of its own, or a command to paste. *Enforced by:*
  `MissingSoftware.test.tsx`; G-7.
---

## 7. Content

- **Voice**: direct, technical, unadorned. No exclamation marks, no "please", "sorry", "oops" or
  "successfully". Sentence case in both languages.
- **Languages**: English and Spanish, from the typed catalogs of `panel/src/i18n` only, and any
  language contributed as a file (`docs/TRANSLATING.md`, shown in English where it has not been
  translated); whole sentences with placeholders, never fragments assembled in code. Spanish addresses the
  operator as *tú*, buttons are infinitives. The glossary and the rules are in
  `panel/src/i18n/README.md`; a term changes there first.
- **Not translated**: the system's output, commands, paths, unit names, product names and
  language autonyms.
- **Errors**: what failed, then what to do (one imperative sentence), then what the system
  said (see 6.5).
- **Buttons**: in a dialog, a verb and its object ("Delete application"); in a page, one or two
  words ("Deploy", "Restart"); the toast reuses the verb in the past tense.
- **Verbs**: Create (Noust makes it), Add (links something that exists), New (the page button
  of a top-level entity), Delete (destroys data Noust owns), Remove (unlinks; it still exists
  elsewhere), Revoke (invalidates a credential), Drop (SQL only), Discard (unsaved changes),
  Enable / Disable (reversible), Start / Stop / Restart (a service's life), Roll back (to an
  earlier version), Save (persists) / Apply (persists and runs).
- **The verbs of an application do not collide** (checklist M-32). Each word means one thing:
  **Update** pulls the source and builds a new release; **Deploy** is the wizard's last button,
  which creates the application and builds its first; **Restart** starts the same release again
  (no build); **Stop** and **Start** are the service, with no build; **Roll back** goes to an
  earlier release. A button that only opens a page is never called Deploy ("New application",
  "From a recipe"). Two other things are called updates and are named by what they update:
  **System updates** (the operating system's packages: Server, Overview, the fleet's "Noust
  and system updates") and **Update Noust** (Noust itself). Never a bare "Updates" for either.
  `i18n/verbs.test.ts` holds the words in both languages.
- **Retry, try again, run again** are three verbs: **Retry** repeats an action that failed (a
  deployment, a certificate, a job); **Try again** reads again after a read that failed;
  **Run again** repeats a check. **Rebuild** builds the same commit again. **Clear** empties a
  field, a list or a cache; **Dismiss** closes a notice; **Done** only follows a secret shown once.
  **Acknowledge** and **Reopen** are an alert's, and acknowledging never resolves it. The whole
  table, in both languages, is the glossary in `panel/src/i18n/README.md`, "The words of the
  interface".
- **Empty states**: the title says what this place is and what is missing ("No applications
  yet"); one or two sentences say the next step; the action is an imperative of one or two
  words; the command, when the CLI has one.
- **Numbers, sizes and dates** go through `lib/format.ts` (binary sizes, two units of duration,
  "3 min ago" with the exact moment in a tooltip). A used/total pair is written in one unit,
  the larger value's (`formatBytesPair`: "0.71 TB of 0.98 TB", never "727 GB of 0.98 TB"). An ellipsis is one character, `…` (U+2026),
  never three dots.
  Titles have no final full stop; sentences do.
- **No jargon**: the surface speaks of the result for the operator; the technical term is a
  value in mono, a tooltip or a secondary line. Examples of the rewrite:

| Instead of | Write |
|---|---|
| Releases / Enable releases | Instant rollback / Turn on instant rollback |
| Each deploy is a release; rollback is instant | Each deploy is kept apart; going back takes seconds |
| In place | Single folder |
| Health check | Startup check |
| Payload URL / forge | Webhook URL / GitHub, GitLab or Gitea |
| Deliveries | Pushes received |
| Unit / systemd unit | Service (the unit name in mono) |
| Unit security | Service security |
| Run as an argv, without a shell. | Runs directly, not through a shell: pipes, && and $VARS do not work. |
| Drain | Keep the old version for (seconds) |
| Covered / Not covered | HTTPS / No HTTPS |
| Emergency access (the access token) | Access token |
| Live version | Live now |
| Domains and certificates | Domains |
| Console access | Console address |
| Roles and access | Roles |
| Log shipping | Logs and metrics export |
| Reading ... | Loading … |
| Make a file | New file |
| Confirm / OK | Continue, or the verb of what is confirmed |

- **TX-1** Each action has one verb and each thing one name, in both languages, from the glossary
  in `panel/src/i18n/README.md`; a term changes there first. *Enforced by:* `i18n/verbs.test.ts`;
  `i18n/glossary.test.ts`.
- **TX-2** A page button has no article and a dialog title has one ("Add server" / "Add a server");
  "Make", "Confirm" and "OK" are not button words; a title has no final full stop; an ellipsis is
  one character. *Enforced by:* `i18n/glossary.test.ts`.
- **TX-3** The accessible text of every loading region is "Loading …"; a state word is a state,
  not a verb (in Spanish "Fallido", not "Falló"). *Enforced by:* `i18n/glossary.test.ts`;
  `LoadingRegion` takes its label as a sentence.
- **TX-4** A label that means the same in two places is one key, and a sentence repeated three
  times moves to `common.*`. *Enforced by:* review; the divergence count of
  `i18n/glossary.test.ts` holds the English texts whose Spanish differs from key to key.

---

## 8. Accessibility

WCAG 2.2 AA, verified rather than declared:

- **A-1** axe (WCAG 2.0-2.2 A and AA, `target-size` included) runs on every route in both themes
  (`e2e/pages.spec.ts`) and on the gallery (`e2e/design-contract.spec.ts`); component tests run
  axe too.
- **A-2** Focus is always visible (2px `--focus`, offset 2); a skip link leads to the content;
  after each navigation focus goes to the page's `h1`; dialogs trap focus and give it back;
  sticky chrome never hides the focused element (`scroll-padding-top`).
- **A-3** Targets are 24px at least; controls are 28/32/40px, and 36/40/44px with a coarse
  pointer.
- **A-4** Every state is colour, shape and word; the glyphs differ in silhouette (tested).
- **A-5** Live regions: polite for progress and success, assertive for failures and only for the
  outcome of the operator's own action; never for log lines or readings that change every few
  seconds; one announcer for the whole console.
- **A-6** Everything is reachable by keyboard: arrows between table rows, menus and dialogs as
  the ARIA patterns describe them, `aria-sort` on sorted columns; a tooltip is never the only
  source of information.
- **A-7** Charts and diagrams (`FlowDiagram`) have a written summary and a data table; colour is
  never the only channel.
- **A-8** Reduced motion drops every animation (2.7); `lang` follows the language; system values
  carry `translate="no"`; the console reflows at 320px and 200% zoom without sideways scrolling.
- **A-10** A field is identified at 3:1 without a line round it (closed; owner decision, 4.1): the
  strong edge of its hollow, the shadow along the top inside in light and the lit lip below in dark
  (C-12). It is measured twice, on the tokens and on the pixels of a browser, and the focus ring
  (3:1) is the other thing that identifies it. Every other control meets 1.4.11: the raised ones by
  their joint and their label.
- **A-11** The sliding indicator and the illustrations are `aria-hidden`; the items carry the state and
  the drawing is decoration unless it is given a label. A touch screen has no tooltip, so what needs
  saying is a `HelpPopover`.
- **A-9** Under the strict Content Security Policy (`style-src 'self'`, Trusted Types), no
  library that injects `<style>` or writes HTML strings is used; the E2E suite fails on any
  violation.

---

## 9. Enforcement

What a machine can check is not left to review. Every layer blocks CI (`npm test`, `npm run
lint`, the E2E suite).

| Layer | Where | What it holds |
|---|---|---|
| Construction | `app.css` | Tailwind's palette, type scale, shadows and radii do not exist; only token utilities compile |
| Tokens | `styles/tokens.test.ts` (`lib/contrast.ts` does the maths) | Every text pair 4.5:1, every control boundary 3:1, tinted borders 1.4:1, hairlines visible; surfaces, edges and text are stone (chroma under 0.02, the stone's hue); height by luminance in both themes; the joint and the edges of every height seen against the stone they sit on; every `--elev-*` made of hard edges (nothing blurrier than 2px, no translucent black); the `viz` pair's contrast, colour-vision distance, hue distance, lightness and chroma; every colour token has a utility and at least one verified pair; each curve and duration of the motion and its zero with reduced motion; the fonts' stacks, their metric-matched fallbacks (declared and scaled, the title's twice) and that nothing is fetched or inlined; the values of radii, control heights, layers, widths, measures, icon sizes and the seven type sizes; `scrollbar-gutter: stable` |
| Lint | `eslint.config.js`, `no-restricted-syntax` and `no-restricted-imports` | Outside the kit (`components/`, `app/`, `dev/`) and the legacy list: raw hex, arbitrary dimensions (`max-w-[...]`, `h-[...]`, `rounded-[...]`, `z-[...]`), colour functions, hand-written `h1`-`h4`, `<label>`, `style=`, severity icons imported from lucide instead of `icons.ts`, and the icon of an object imported from lucide instead of `objectIcons.ts` (everywhere, the kit included) |
| Content and who can do what | `components/page/permissions.test.tsx`, `SurfaceSection.test.tsx`, `components/ui/{FirstUse,ResourceCard,NameWithTags,HelpPopover}.test.tsx`, `styles/design-rules.test.ts` | The pieces of 6.16, each with axe in both themes; a write that declares no permission and a command with no reason, counted per file against a baseline that only goes down (PM-1, CL-1) |
| Ratchet | `styles/design-rules.test.ts`, rules in `styles/designRules.ts`, counts in `styles/design-baseline.json` | Per rule and per file, counts that may only go down (the rule ids below); ESLint's legacy list only shrinks; the `viz` colours stay inside `Chart`; no component of the system is left unused except those still waiting for their pages |
| The tics and the heights | `styles/design-rules.test.ts` (`KIT_HEIGHTS` in `styles/designRules.ts`) | The list of 2.12, with samples that must be caught and samples that must not, in the sources and in the three stylesheets (a gradient, a blur, capitals, a wide tracking, a coloured side, a coloured one-sided shadow); each kit piece has the height of its role and no other, and a piece with no role that draws a height fails (a raised panel and a carved button are cases that must be caught) |
| A field is found | `styles/tokens.test.ts`, `e2e/field-edge.spec.ts` | Each theme's strong edge of the carved hollow is 3:1 against the page, a panel and a raised surface; in the browser, the pixels painted on a field, a checkbox and a switch say the same, and the other edge stays under 3:1 (C-12, A-10) |
| Motion | `components/ui/SlidingIndicator.test.tsx`, `e2e/indicator.spec.ts` | The indicator goes where the chosen item is, slides only between two items, never on the first frame, on a resize, with reduced motion or from nothing, queues nothing; in the browser, its transition lasts the token's duration, ends at the target's box, and does not exist with reduced motion |
| Structure | `styles/design-structure.test.ts`, rules in `styles/designStructure.ts`, counts in `styles/design-structure-baseline.json` | The same contract for what a regular expression per line cannot see, found in the syntax tree: a component used where a kit piece exists, a file with three notices, a notice read in English words (the structural rule ids below). `npm run ux:counts` prints how much of the console is built from each piece, from the same code. Examples of what each rule counts and what it does not are `styles/design-structure.cases.test.ts` |
| Copy | `styles/setting-copy.test.ts`, `styles/concept-doors.test.ts`, `styles/dead-class.test.ts`, `i18n/verbs.test.ts`, `i18n/glossary.test.ts` (baselines `i18n/glossary-baseline.json`, `styles/concept-doors.json`) | The words of a setting row (SW-3, SW-4); the concepts with more than one editable home (NV-8), a list that only gets shorter; a colour class that names no colour of the system; the glossary's verbs and states in both languages; a retired value that comes back, a glossary word translated otherwise, one English text translated several ways |
| Browser | `e2e/design-contract.spec.ts` | On a sample of every template, at 1440 and 390, both themes, and the gallery: font sizes, families and weights, radii, border widths, every opaque colour grey or a token, one primary button per view (clean and with unsaved changes), at most one banner per route, the height of a setting row |
| Forms and length | `e2e/leave-guard.spec.ts`, `e2e/settings-length.spec.ts` (baseline `e2e/settings-length.baseline.json`) | The question asked before a form with changes is left, and that discarding puts the value back; the height of every settings route at 1440, which may only go down |
| Loading | `routes/loading.test.tsx` | Every route of the console, on the real router and a server that answers only the session: the body shows a skeleton inside an `aria-busy` region, no skeleton is outside one, and no empty state is drawn while the answer is awaited. A route with no entry in the table fails, so a page cannot ship without someone having looked at what it draws while it waits |
| Layout | `e2e/layout-shift.spec.ts`, `e2e/tab-switch-layout.spec.ts` | Cumulative layout shift 0.05 or less on load; switching between a long and a short tab (with real scrollbars) does not move the content column |
| Accessibility and CSP | `e2e/pages.spec.ts`, `e2e/fixtures.ts` | axe on every route and theme; any CSP violation or console error fails the test |

The ratchet's rules (`designRules.ts`):

| Rule id | What it counts | Instead |
|---|---|---|
| `hand-surface` | `rounded-card` with `bg-surface` outside the kit | `Card` |
| `tinted-border`, `alpha-on-token` | `border-warn/40`, `bg-fail-soft/50`, any `/NN` on a colour token | `Notice`, `*-border`, `*-soft` |
| `arbitrary-dimension` (lint) | `max-w-[...]`, `h-[...]`, `rounded-[...]`, `z-[...]`... | a named utility or token |
| `arbitrary-grid` | `grid-cols-[...]` | a template, `KeyValueList` |
| `chip-radius` | `rounded-[4px]`, `rounded-[5px]` | `rounded-chip` |
| `off-scale-space` | spacing steps 7, 9, 11, 13, 14, 15, 6.5 | the scale (2.4) |
| `tone-map` | `TONE_TEXT`/`TONE_SOFT`/`TONE_RAIL` redeclared | `stateTextClass`, `StatusGlyph` |
| `empty-cell` | `function Nothing`, a bare `-` cell | `EmptyCell` |
| `mono-utility` | a `mono` class outside the kit | `<Mono>` |
| `raw-heading`, `raw-label`, `inline-style` (lint) | `<h2>`, `<label>`, `style=` in a feature | `Section`/`Subsection`/`Card`, `Field`, a utility |
| `semantic-icon-import` (lint) | `CircleCheck`, `TriangleAlert`, `Info`... from lucide in a feature | `ICONS` |
| `colour-function` (lint) | `rgb(...)`, `color-mix(...)` in a class | a token |
| `tailwind-palette`, `off-role-text` | `bg-red-500`, `text-sm` (they do not compile) | tokens, type roles |
| `semibold` | `font-semibold`, `font-bold` in a feature (the kit may write 600, 2.3) | `title`, `font-medium` |
| `side-stripe` | a one-sided border or inset shadow in a state or accent colour, `state-rail-*` | a glyph, a word and a tinted surface (2.12) |
| `gradient`, `blur`, `diffuse-shadow` | `bg-linear-*`, `mask-*`, `backdrop-blur`, `shadow-md`... | a tone of stone, a hard edge (2.12, 2.6) |
| `caps-label` | `uppercase`, `tracking-wide`... | sentence case |
| `middle-dot`, `arrow-glyph`, `arrow-button` | a middle dot, an arrow in a text, a trailing arrow icon on a button | a column, a chip, a verb |
| `icon-tile` | a sized, rounded, grounded box that centres an icon (outside `LogoTile`) | a bare icon; `LogoTile` for a real logo |
| `tooltip-by-hand` | `<Tooltip` outside the kit | `IconButton`, `HelpPopover` |
| `elev-outside-kit` | `elev-*` outside the kit | `Card`, `Button`, `Field` |
| `z-arbitrary` | `z-[...]` | `z-sticky`, `z-overlay`... |
| `dashed-empty` | `border-dashed` outside `EmptyState` | `EmptyState` |
| `spinner-outside-kit` | `<Spinner>` in a feature | `JobProgress`, `Button loading` |
| `focus-ring-repeat` | `focus-visible:outline-2` | the global ring |
| `confirm-hand-rolled` | `AlertDialog.Popup` outside `ConfirmDialog` (a hand-written danger button is the structural rule `danger-button-outside-confirm`) | `ConfirmDialog` |
| `page-without-template` | `<PageHeader>` written by a feature | a template's `header` prop |
| `feature-state-notice` | a `Notice` whose title or text is an on/off key (`.on`, `.offTitle`, `.enabled`...) | `Card status`, a `Setting` row |
| `write-without-permission` | each `.mutate(`/`.mutateAsync(` of a feature, less one for each declaration of the file (`GuardedButton`, `Can`, `ReadOnlyLine`, `useCan(`, `can("...")`) | `GuardedButton`, `Can`, `ReadOnlyLine` (PM-1) |
| `command-hint-reason` | a `<CommandHint` with no `reason` | a `reason`, or take the command out (CL-1) |
| `object-icon-import` (lint) | an object's icon (`Database`, `Globe`, `ScrollText`, `Users`...) imported from lucide outside the two maps | `OBJECT_ICONS`, `VERB_ICONS` (2.8) |

The structural rules (`designStructure.ts`), found in the syntax tree:

| Rule id | What it counts | Instead |
|---|---|---|
| `badge-state` | a `Badge` with tone `ok`, `warn`, `fail`, or a tone the code computes | `StatusPill`; then `BadgeTone` is `neutral \| accent` |
| `notice-stack` | a file with three `<Notice` or more (one per file; a design-exception anywhere in the file, with its reason, excuses it) | the worst one, an aggregate, help text or a risk line |
| `notice-info-bare` | an `info` Notice with neither an action nor a title | the help text of its field, row or section |
| `notice-success` | a `success` Notice outside a Dialog, Drawer, ConfirmDialog or Wizard | nothing (the interface changes) or a toast |
| `notice-long` | a Notice of more than 45 English words, title and body (the test resolves the catalog keys) | a "What changes" Popover, a Disclosure, a Drawer or the docs |
| `switch-in-form` | a `<Switch` in a file with a `<form`, a `type="submit"` or a `SaveBar` | a `Setting` row, or a `Checkbox` in the form |
| `card-save` | a Save or Apply button inside a `Card` and outside a dialog | the `SaveBar`, or immediate settings |
| `submit-disabled-invalid` | a primary, danger or submit button whose `disabled` reads an empty or invalid value (a lower bound) | press it, focus the first field with an error |
| `own-leave-dialog` | `useBlocker(` outside `lib/useLeaveGuard.ts` | `SaveBar`'s guard, or `LeaveGuard` |
| `hand-aria-busy` | `aria-busy="true"` outside the kit | `QueryState`, `LoadingRegion` (the codemod rewrites the common form) |
| `hand-error-ternary` | `isError && data === undefined` | `QueryState` |
| `inline-empty-by-hand` | a muted `<p>` whose only content is an `empty` key | `EmptyState variant="inline"` |
| `danger-button-outside-confirm` | `variant="danger"` outside `ConfirmDialog` and `DangerZone` | `ConfirmDialog`, `DangerZone` |
| `native-details` | `<details` | `Disclosure variant="card"` |
| `row-actions-local` | a `RowActions` component of a feature | `RowMenu` |
| `bordered-list` | a `ul`, `ol` or `div` with `divide-y`, a border and the card's or the control's radius | `RowList` |
| `row-link-class` | the classes `hover:underline hover:underline-offset-2` written in a feature | `TextLink variant="row"` |
| `copy-box-by-hand` | a sunken, framed box that holds a value with its own copy button, or a `select-all` box | `CopyValue` |
| `field-error-by-hand` | a `<p>` in `text-fail`, or a `role="alert"` on a plain element | `Field`, `FieldGroup` |
| `destination-outside-register` | an array of `{ label, to }` destinations in a file that deals in tabs, outside `app/nav.ts` | the register (`tabsOf`, 6.14) |
| `own-back-link` | a `TextLink` or `Link` that wears a left arrow | the breadcrumbs of the template |
| `empty-means-off` | an English catalog string that says "Empty keeps", "Blank means", "0 turns it off" | a `Setting` whose switch reveals the number |
| `help-starts-with-state` | an English `*Help`, `*Description`, `*Note` or `*Hint` that starts with "On," or "Off," | help that says what the setting does |
| `live-switch` | a `Switch` named Live or Follow | the viewer's "Follow" (LG-3) |
| `toast-success` | `toast.success(` | nothing, when the result is on screen (FM-7) |

**Exceptions.** A line that has to break a rule says so, on that line or the one above:
`// design-exception: <rule-id> <reason>`. For a lint rule, the same words go after the
directive: `// eslint-disable-next-line no-restricted-syntax -- design-exception: <rule-id>
<reason>`. The ratchet fails on an exception without a known rule id or without a reason, and
on a design lint rule silenced without one. The structural rules are excused by the same comment
(`notice-stack`, which is about a file as a whole, by one anywhere in it).

**Commands** (in `panel/`):

```bash
npx vitest run src/styles src/i18n     # tokens, the ratchets, the copy and the glossary
npm run design:baseline                # lock in lower counts after a migration (refuses if any went up)
npm run ux:counts                      # how much of the console is built from each piece, by directory
node scripts/ux-counts.mjs --rules     # what each structural rule counts today
node scripts/codemods/loading-region.mjs src/features/x   # rewrite hand-written loading regions (--write)
npm run lint                           # includes the design rules
NODE_ENV=development npx vite build --mode development --outDir /tmp/noust-dev-build
NOUST_E2E_STATIC=/tmp/noust-dev-build npx playwright test design-contract tab-switch-layout
```

---

## 10. Governance

- **G-1 The gallery first.** A change to the system starts in the gallery (`/__design`): the
  new or changed token, component or pattern, in both themes, with a "Don't" beside it when it
  prevents a mistake. Then the tokens and their tests, then `DESIGN.md`, then the pages. A
  change that skips the gallery is incomplete.
- **G-2 The rule of three.** A pattern that appears for the third time moves into the kit before
  it is written again.
- **G-3 New rules enter with today's count** as their baseline, and the count may only go down.
  A page moved onto its template lowers the counts in its files and runs `npm run
  design:baseline`, which rewrites `design-baseline.json`, `design-structure-baseline.json` and
  `i18n/glossary-baseline.json` (and refuses to raise any count); they are committed with it.
- **G-4 New pages** are composed of a template and existing components; a page that cannot be
  extends a template or adds a component first (gallery, tests, this document).
- **G-5 Copy** changes in the catalogs, English and Spanish together, and a term of the glossary
  changes in `panel/src/i18n/README.md` first.
- **G-6 Builds.** Every change under `panel/src` ships with `npm run build` and the committed
  `src/noust/web/static` (the packaging rule of `CONTRIBUTING.md`).
- **G-7 Visual review.** Screenshots of every route at 390, 1440 and 1920 in both themes and in
  Spanish are reviewed before a release with seven questions: is there one primary action? Does
  the state read in greyscale? Is anything coloured that is neither a state, an action nor a
  series? Does anything move as it loads? Is the server named where something is destroyed?
  Does the empty state say what to do? **Is any notice or state block taller than the control it
  explains?** Is any mark of 2.12 drawn (a stripe, a gradient, a blur, a tile behind an icon, a middle
  dot)? Does each piece have the height of its role, and does the indicator slide to where the person
  pointed and not before?
- **G-8 A new destination** enters with its register entry (`app/nav.ts`), its answers to the
  four tests of NV-4 (a noun the operator says every day or every week; it lists what exists or
  shows what happened; most installations use it; it is not set-up, a catalogue, another
  destination's view or a module that is off) and, if it repeats a concept, its row in
  `styles/concept-doors.json` with the reason. A fifth global indicator needs a row in NV-16.
- **G-9 A new setting** says in its change which step of NV-7 it falls on (account, resource,
  group, server, instance, fleet) and how the other places that could hold it link to it.

---

## Appendix: tokens

| Family | Tokens |
|---|---|
| Surface | `--bg-rail`, `--bg-column`, `--bg`, `--bg-sunken`, `--bg-sunken-deep`, `--surface`, `--surface-raised`, `--surface-hover`, `--surface-active`, `--rail-hover` |
| Edge | `--edge`, `--joint`, `--carved-shade`, `--carved-lip`, `--border`, `--border-strong` |
| Text | `--text`, `--text-muted`, `--text-faint` |
| Interactive | `--accent`, `--accent-hover`, `--accent-soft`, `--accent-text`, `--accent-edge`, `--accent-joint`, `--on-accent`, `--focus`, `--selection`, `--match` |
| State | `--ok`, `--ok-soft`, `--ok-border`, `--warn`, `--warn-soft`, `--warn-border`, `--fail`, `--fail-soft`, `--fail-border`, `--fail-strong`, `--on-fail`, `--fail-edge`, `--fail-joint`, `--idle`, `--idle-soft`, `--idle-border` |
| Chart series | `--viz-1`, `--viz-2` |
| Program colours | `--ansi-blue`, `--ansi-magenta`, `--ansi-cyan` |
| Illustration | `--il-stroke`, `--il-stroke-2` |
| Height | `--elev-carved`, `--elev-carved-fine`, `--elev-seated`, `--elev-raised`, `-high`, `-pressed`, and the same for `-accent`, `-fail` and `-chosen` |
| Veil | `--backdrop`, `--bg-veiled` |
| Type | `--font-sans`, `--font-display`, `--font-mono`, `--fs-12` ... `--fs-32`, `--lh-12` ... `--lh-32`, `--stretch-title` 90%, `--stretch-display` 78%, `--tracking-title`, `--tracking-display` |
| Measure | `--measure` 68ch, `--measure-help` 52ch |
| Space | `--space` 0.25rem |
| Radius | `--radius-chip` 6px, `--radius-control` 9px, `--radius-card` 14px, `--radius-wall` 22px, `--radius-pill` 999px |
| Controls | `--control-sm` 2rem, `--control-md` 2.25rem, `--control-lg` 2.75rem (2.5 / 2.75 / 3rem with a coarse pointer) |
| Icons | `--icon-xs` 0.75rem, `--icon-sm` 0.875rem, `--icon-md` 1rem, `--icon-lg` 1.25rem, `--icon-xl` 1.5rem |
| Layers | `--z-sticky` 30, `--z-backdrop` 40, `--z-overlay` 50, `--z-toast` 60, `--z-skip` 70 |
| Widths | `--width-page`, `--width-wizard`, `--width-settings-nav`, `--width-settings`, `--width-auth`, `--width-dialog-sm/md/lg/xl`, `--width-drawer-md/lg`, `--width-search`, `--height-editor` |
| Motion | `--duration-press` 100ms, `--duration-fast` 140ms, `--duration-move` 200ms, `--duration-enter` 240ms, `--duration-large` 360ms; `--ease-enter`, `--ease-exit`, `--ease-standard` (and `--ease-out` for the loops) |
