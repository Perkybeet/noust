/**
 * The design system's text rules: what a feature must not write because a token, a utility or
 * a component already says it (docs/DESIGN.md, "Enforcement"). styles/design-rules.test.ts
 * counts every rule in every production file and holds each count to the baseline in
 * design-baseline.json, which may only go down. The rules marked `lint` are also ESLint
 * rules (eslint.config.js); their textual form here is a superset of what ESLint matches, so a
 * file with none of them can leave ESLint's legacy list.
 *
 * A line that has to break a rule says why, on that line or the one above:
 * `// design-exception: <rule-id> <reason>` (or, for an ESLint rule,
 * `// eslint-disable-next-line no-restricted-syntax -- design-exception: <rule-id> <reason>`).
 */

export interface DesignRule {
  id: string;
  /** What to write instead, for the failure message. */
  fix: string;
  pattern: RegExp;
  /** Which files the rule applies to (paths relative to src/, "features/apps/AppsPage.tsx"). */
  applies: (path: string) => boolean;
  /** Also enforced by ESLint outside the kit, with a legacy list of files not yet migrated. */
  lint?: boolean;
  /**
   * What covers a match, one for one: the declarations that offset the matches of a file. A file with five writes and
   * two declarations counts three, so a declared action lowers the count and an undeclared one cannot hide behind it.
   */
  covered?: RegExp;
}

/** The kit: the components, the app shell (PageHeader, LinkTabs, the top bar) and the gallery. */
const KIT = /^(components|app|dev)\//;
const everywhere = (): boolean => true;
const outsideKit = (path: string): boolean => !KIT.test(path);
const except =
  (...paths: string[]) =>
  (path: string): boolean =>
    !paths.includes(path);

/** Tailwind utilities that take a length, as an arbitrary value: `max-w-[46rem]`, `h-[26rem]`. */
export const ARBITRARY_DIMENSION =
  /(?:^|[\s"'\x60:])-?(?:m[trblxy]?|p[trblxy]?|gap(?:-[xy])?|space-[xy]|size|w|h|min-w|max-w|min-h|max-h|text|rounded(?:-[a-z]{1,2})?|top|right|bottom|left|inset(?:-[xy])?|z|leading|tracking|basis|shadow)-\[/g;

/**
 * A CSS colour function in a class or style string: `rgb(0 0 0 / .2)`, `color-mix(in oklab,
 * ...)`, or Tailwind's arbitrary form with underscores, `shadow-[0_1px_0_rgb(255_255_255/0.16)]`.
 */
export const COLOUR_FUNCTION = /(?<![a-zA-Z0-9-])(?:rgba?|hsla?|oklch|oklab|color-mix)\((?=[\s_]*(?:\d|\.|in[\s_]|var\(|from[\s_]))/g;

/**
 * A stripe of colour down one side of a container: a one-sided border (`border-l-2`) with a state or
 * accent colour in the same class string, in either order, a one-sided inset shadow in such a colour
 * (`shadow-[inset_2px_0_0_var(--fail)]`) and the retired `state-rail-*` utilities. A state is told by a
 * glyph, a word and, when it is a problem, a tinted surface; never by a line at the edge of a card, a
 * row or an alert, which is the commonest mark of a templated interface (docs/DESIGN.md 2.12).
 */
export const SIDE_STRIPE =
  /(?<=border-(?:ok|warn|fail|idle|accent)(?:-border)?\b[^"'`\n]*)(?<![\w-])border-[lrtbse](?:-\d+)?(?![\w-])|(?<![\w-])border-[lrtbse](?:-\d+)?(?![\w-])(?=[^"'`\n]*\bborder-(?:ok|warn|fail|idle|accent)(?:-border)?\b)|(?<![\w-])border-[lrtbse]-(?:ok|warn|fail|idle|accent)\b|(?<![\w-])state-rail-|shadow-\[inset_(?:-?\d+px_0_0|0_-?\d+px_0)_(?:0_)?var\(--(?:ok|warn|fail|idle|accent)\b/g;

/**
 * A tile behind an icon: a small square or round box with a ground that centres a glyph. The only
 * tile in the console is a real logo's (`LogoTile`); an icon of the console's own stands bare, and a
 * tile behind it is a decoration that says nothing.
 */
export const ICON_TILE =
  /(?<=["'`])(?=[^"'`\n]*\bsize-(?:8|9|10|11|12|14)\b)(?=[^"'`\n]*\bitems-center\b)(?=[^"'`\n]*\bjustify-center\b)(?=[^"'`\n]*\brounded-(?:control|card|chip)\b)(?=[^"'`\n]*\bbg-(?:surface|bg-sunken|accent-soft|ok-soft|warn-soft|fail-soft|idle-soft|accent|surface-raised)\b)[^"'`\n]*/g;

/**
 * What declares the permission an action takes: the page's own primary action (`GuardedButton`), what is drawn only
 * for who may (`Can`), a question the page asks of the session (`useCan(`, a team's `org.can(`) or a page that says it
 * is read only (`ReadOnlyLine`). Each covers one write of the file.
 */
export const WRITE_DECLARATION = /<GuardedButton\b|<Can\b|<ReadOnlyLine\b|\buseCan\(|\bcan\(\s*["'`]/g;

/** A `<CommandHint ... />` that never says `reason=`: its attributes are read up to the closing `/>`, `=>` and all. */
export const COMMAND_HINT_WITHOUT_REASON = /<CommandHint\b(?:(?!\breason=)(?:[^/]|\/(?!>)))*\/>/g;

export const DESIGN_RULES: readonly DesignRule[] = [
  {
    id: "hand-surface",
    fix: "Use Card (padding md, sm or none) instead of rounded-card + bg-surface by hand.",
    pattern: /rounded-card[^"'`\n]*\bbg-surface\b|\bbg-surface\b[^"'`\n]*rounded-card/g,
    applies: outsideKit,
  },
  {
    id: "tinted-border",
    fix: "Use Notice, or border-{ok,warn,fail,idle}-border: a token, not an alpha.",
    pattern: /\bborder-(?:ok|warn|fail|idle|accent)\/\d+/g,
    applies: everywhere,
  },
  {
    id: "alpha-on-token",
    fix: "Use a token (*-soft, *-border, text-muted, text-faint), never a colour token with /NN.",
    pattern: /\b(?:bg|text|border|divide|ring|outline|fill|stroke)-(?:ok|warn|fail|idle|accent|fg|surface|bg|border|on-accent)(?:-[a-z]+)*\/\d+/g,
    applies: everywhere,
  },
  {
    id: "arbitrary-dimension",
    fix: "Use a named utility or token (h-control-md, max-w-measure, w-dialog-md, rounded-chip, z-overlay).",
    pattern: ARBITRARY_DIMENSION,
    applies: everywhere,
    lint: true,
  },
  {
    id: "arbitrary-grid",
    fix: "Use a template or Split/KeyValueList instead of a grid with invented column widths.",
    pattern: /\bgrid-cols-\[/g,
    applies: everywhere,
  },
  {
    id: "chip-radius",
    fix: "Use rounded-chip (4px).",
    pattern: /\brounded(?:-[a-z]{1,2})?-\[[45]px\]/g,
    applies: everywhere,
  },
  {
    id: "off-scale-space",
    fix: "Use the spacing scale: half steps to 16px (0.5-4), then 5, 6, 8, 10, 12, 16.",
    pattern: /(?<![\w-])-?(?:p|px|py|pt|pb|pl|pr|ps|pe|m|mx|my|mt|mb|ml|mr|ms|me|gap|gap-x|gap-y|space-x|space-y)-(?:7|9|11|13|14|15|6\.5)(?![\w.])/g,
    applies: everywhere,
  },
  {
    id: "tone-map",
    fix: "Import stateTextClass or StatusGlyph from components/ui/StatusPill; do not redeclare tone maps.",
    pattern: /\b(?:TONE_TEXT|TONE_SOFT|TONE_RAIL)\b\s*[:=]/g,
    applies: except("components/ui/StatusPill.tsx"),
  },
  {
    id: "empty-cell",
    fix: "Use EmptyCell (an en dash with its reason for screen readers).",
    pattern: /\bfunction Nothing\b|\bconst Nothing\b|text-fg-faint">-<\/span>/g,
    applies: everywhere,
  },
  {
    id: "mono-utility",
    fix: "Use <Mono> for a system value (it also sets translate=\"no\").",
    pattern: /"[^"\n]*\bmono\b[^"\n]*"|`[^`\n]*\bmono\b[^`\n]*`/g,
    applies: outsideKit,
  },
  {
    id: "raw-heading",
    fix: "Headings come from PageHeader, Section, Subsection, Card or a template.",
    pattern: /<h[1-4][\s>]/g,
    applies: outsideKit,
    lint: true,
  },
  {
    id: "raw-label",
    fix: "Use Field, which wires the label, help and error to the control.",
    pattern: /<label[\s>]/g,
    applies: outsideKit,
    lint: true,
  },
  {
    id: "inline-style",
    fix: "Use a utility; inline styles are for values only the data knows (a chart, a virtual list), in the kit.",
    pattern: /\bstyle=\{/g,
    applies: outsideKit,
    lint: true,
  },
  {
    id: "semantic-icon-import",
    fix: "Take severity and verb icons from components/ui/icons.ts (ICONS.success, ICONS.warning...).",
    pattern: /import\s*(?:type\s*)?\{[^}]*\b(?:CircleCheck|TriangleAlert|CircleAlert|OctagonAlert|ShieldCheck|ShieldAlert|Info)\b[^}]*\}\s*from\s*"lucide-react"/g,
    applies: outsideKit,
    lint: true,
  },
  {
    id: "colour-function",
    fix: "Colour lives in tokens.css; use a token utility.",
    // A CSS colour function, as a class or a style writes one: numbers, `in`, `var(` or `from`
    // after the parenthesis. A TypeScript function called oklch(hex) is not one.
    pattern: COLOUR_FUNCTION,
    applies: everywhere,
    lint: true,
  },
  {
    id: "tailwind-palette",
    fix: "Tailwind's palette is switched off: use a token utility (text-fail, bg-surface...).",
    pattern:
      /\b(?:bg|text|border|ring|fill|stroke|from|to|via|outline|divide|decoration|shadow|caret)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3}\b/g,
    applies: everywhere,
  },
  {
    id: "off-role-text",
    fix: "Use a type role: text-12, 13, 14, 16, 18, 24 or 32.",
    pattern: /\btext-(?:xs|sm|base|lg|[2-9]?xl)\b/g,
    applies: everywhere,
  },
  {
    id: "semibold",
    fix: "Weights are 400 and 500 in a feature; 600 is the kit's (buttons, pills, table headers) and 650 and 700 come only through the title and display utilities.",
    pattern: /\bfont-(?:semibold|bold|extrabold|black)\b/g,
    // The kit writes 600 for what is pressed or names a column (docs/DESIGN.md 2.3); a feature never does.
    applies: outsideKit,
  },
  {
    id: "z-arbitrary",
    fix: "Use a named layer: z-sticky, z-backdrop, z-overlay, z-toast, z-skip.",
    pattern: /\bz-\[/g,
    applies: everywhere,
  },
  {
    id: "dashed-empty",
    fix: "Use EmptyState (firstUse or inline).",
    pattern: /\bborder-dashed\b/g,
    applies: except("components/ui/EmptyState.tsx"),
  },
  {
    id: "spinner-outside-kit",
    fix: "A job in hand is JobProgress; a busy button is Button loading.",
    pattern: /<Spinner\b/g,
    applies: outsideKit,
  },
  {
    id: "focus-ring-repeat",
    fix: "The focus ring is global (app.css); only a scroll box needs -outline-offset-2.",
    pattern: /\bfocus-visible:outline-2\b/g,
    applies: everywhere,
  },
  {
    id: "confirm-hand-rolled",
    fix: "Use ConfirmDialog with the friction the action deserves (none, simple, type). A danger Button written by hand in a dialog is counted by the structural rule danger-button-outside-confirm.",
    pattern: /\bAlertDialog\.Popup\b/g,
    applies: except("components/ui/ConfirmDialog.tsx"),
  },
  {
    // Owner item 56: whether a feature is on was a neutral notice in which only a word changed.
    id: "feature-state-notice",
    fix: "Show whether a feature is on with a StatusPill in the head of its Card (Card status) or a Setting row, never a Notice whose title or text is an on/off word.",
    pattern: /<Notice\b[^>]*\btitle=\{t\("[\w.]+\.(?:on|off|onTitle|offTitle|enabled|disabled|stateOn|stateOff)"\)|<Notice\b[^>]*>\s*\{t\("[\w.]+\.(?:on|off|onTitle|offTitle|enabled|disabled|stateOn|stateOff)"\)/g,
    applies: outsideKit,
  },
  {
    id: "boolean-select",
    fix: "A yes/no choice is a Switch (applies at once) or a Checkbox (saved with a button), never a Select of two options; excuse a value editor whose options are the data itself with a design-exception.",
    pattern: /\bvalue:\s*"(?:true|false|yes|no)"/g,
    applies: outsideKit,
  },
  // The tics of a templated interface, which the owner asked never to see again
  // (item 97, docs/DESIGN.md 2.12). Counted everywhere, the kit included.
  {
    id: "side-stripe",
    fix: "Say the state with its glyph, its word and a tinted surface (StatusPill, Notice), not with a stripe down one side of a container.",
    pattern: SIDE_STRIPE,
    applies: everywhere,
  },
  {
    id: "gradient",
    fix: "No gradients outside an illustration: a surface is a tone of stone, and an edge is a hard line.",
    pattern: /\bbg-(?:linear|radial|conic|gradient)\b|(?<![\w-])(?:from|via|to)-(?:accent|ok|warn|fail|idle|bg|surface|border)\b|(?:linear|radial|conic)-gradient\(|createLinearGradient|createRadialGradient|\bmask-(?:image|linear|radial)\b/g,
    applies: everywhere,
  },
  {
    id: "blur",
    fix: "No blur (a frosted bar, a soft halo): a bar over the content is a solid tone of stone.",
    pattern: /\bbackdrop-(?:blur|filter)\b|(?<![\w-])blur-(?:xs|sm|md|lg|xl|2xl|3xl|\[)|(?<![\w-])filter-blur|(?<![\w.-])blur\(\d/g,
    applies: everywhere,
  },
  {
    id: "diffuse-shadow",
    fix: "Height is a hard edge, not a blurred shadow: take elev-carved, elev-seated or elev-raised (docs/DESIGN.md 2.6).",
    pattern: /(?<![\w-])(?:shadow-(?:xs|sm|md|lg|xl|2xl|inner)|drop-shadow(?:-[a-z0-9]+)?)(?![\w-])/g,
    applies: everywhere,
  },
  {
    id: "caps-label",
    fix: "Sentence case, never capitals with a wide tracking above a title or a label.",
    pattern: /(?<![\w-])(?:uppercase|small-caps|capitalize|tracking-(?:wide|wider|widest))(?![\w-])/g,
    applies: everywhere,
  },
  {
    id: "middle-dot",
    fix: "No middle dot between facts: put each one in its own place (a column, a line, a chip), or join them with a sentence.",
    pattern: /·|&middot;|&#183;|\\u00b7/g,
    applies: everywhere,
  },
  {
    id: "arrow-glyph",
    fix: "No arrow in a label or a button: a button says its verb; a way from here to there is the route's breadcrumbs.",
    pattern: /→|&rarr;|\\u2192/g,
    applies: everywhere,
  },
  {
    id: "arrow-button",
    fix: "A button does not end in an arrow: its verb and, when it needs one, its own icon at the start.",
    pattern: /trailingIcon=\{<(?:ArrowRight|ChevronRight|MoveRight|ArrowBigRight|ArrowUpRight)\b/g,
    applies: everywhere,
  },
  {
    id: "icon-tile",
    fix: "An icon stands bare; a tile behind one is only for a real logo (LogoTile).",
    pattern: ICON_TILE,
    applies: except("components/ui/LogoTile.tsx"),
  },
  {
    id: "tooltip-by-hand",
    fix: "A Tooltip only names an icon, and IconButton does it; what needs explaining is a HelpPopover, which a touch screen can open.",
    pattern: /<Tooltip\b/g,
    applies: outsideKit,
  },
  {
    id: "elev-outside-kit",
    fix: "A feature does not build its own heights: a panel is a Card, a pressed piece a Button, a field a Field (docs/DESIGN.md 2.6).",
    pattern: /(?<![\w-])elev-[a-z-]+/g,
    applies: outsideKit,
  },
  {
    // Atlas pattern 1 and design 11.1.4: a reader saw every action of a page and met a 403 on pressing it.
    id: "write-without-permission",
    fix: "Declare the permission the action takes: a GuardedButton for the page's primary action, Can (or RowMenu inside it) for a row's, and a ReadOnlyLine at the top of the page (components/page/permissions.tsx).",
    pattern: /\.mutate(?:Async)?\(/g,
    covered: WRITE_DECLARATION,
    applies: (path) => path.startsWith("features/"),
  },
  {
    // Item 123: the terminal is the terminal and the web is the web; a command stays only for one of three reasons.
    id: "command-hint-reason",
    fix: "Say why the page shows a command (reason: solo-terminal, otro-servidor or salida-del-sistema) or take it out: the console does what the CLI does.",
    pattern: COMMAND_HINT_WITHOUT_REASON,
    applies: everywhere,
  },
  {
    id: "page-without-template",
    fix: "Compose the page from a template (ListPage, DetailPage, SettingsLayout, DashboardPage, Wizard, FileEditorPage, AuthLayout).",
    pattern: /<PageHeader\b/g,
    applies: (path) => !path.startsWith("components/") && !path.startsWith("app/"),
  },
];

/** The three heights of the console, and the utilities each is made of (app.css). */
export type Height = "carved" | "seated" | "raised";

const HEIGHT_UTILITIES: readonly (readonly [Height, RegExp])[] = [
  ["carved", /(?<![\w-])elev-carved(?:-fine)?(?![\w-])/],
  ["seated", /(?<![\w-])elev-seated(?![\w-])/],
  ["raised", /(?<![\w-])(?:elev-raised(?:-accent|-fail|-chosen)?|elev-rest|elev-lift)(?![\w-])/],
];

/**
 * The height each piece of the kit has by its role (docs/DESIGN.md 2.6): what is typed or chosen
 * inside is carved, what is read is seated, what is pressed is raised, and a piece that has two
 * (a select is a carved field that opens a seated list) says both. A piece that is not here has
 * none: a new one is added with its role, and the test that reads this table fails until it is.
 */
export const KIT_HEIGHTS: Readonly<Record<string, readonly Height[]>> = {
  // carved: where something is typed or chosen inside, and the tracks and meters
  "components/ui/Input.tsx": ["carved"],
  "components/ui/Checkbox.tsx": ["carved"],
  "components/ui/Switch.tsx": ["carved", "raised"],
  "components/ui/Progress.tsx": ["carved"],
  "components/ui/CopyValue.tsx": ["carved"],
  "components/ui/FirstUse.tsx": ["carved"],
  "components/ui/LogoTile.tsx": ["carved"],
  "components/page/SegmentedControl.tsx": ["carved", "raised"],
  "components/page/SettingsLayout.tsx": ["raised"],
  "components/ui/Tabs.tsx": ["carved", "raised"],
  "app/LinkTabs.tsx": ["carved", "raised"],
  // seated: what is read
  "components/ui/Card.tsx": ["seated"],
  "components/ui/DataTable.tsx": ["seated"],
  "components/ui/Dialog.tsx": ["seated"],
  "components/ui/Drawer.tsx": ["seated"],
  "components/ui/Disclosure.tsx": ["seated"],
  "components/ui/Kbd.tsx": ["seated"],
  "components/ui/Popover.tsx": ["seated"],
  "components/ui/RowList.tsx": ["seated"],
  "components/ui/Toast.tsx": ["seated"],
  "components/ui/StatusPill.tsx": ["seated"],
  "components/ui/Chart.tsx": ["seated"],
  "components/ui/LogViewer.tsx": ["seated"],
  "components/ui/Select.tsx": ["seated"],
  "components/ui/Combobox.tsx": ["seated"],
  "components/ui/Menu.tsx": ["seated"],
  "components/page/StatTile.tsx": ["seated"],
  "components/page/JobProgress.tsx": ["seated"],
  "components/page/DangerZone.tsx": ["seated"],
  "components/ui/FleetCourses.tsx": ["carved", "seated", "raised"],
  // Home's grid: the hollow a widget leaves in its place while it is held
  "components/widgets/WidgetGrid.tsx": ["carved"],
  // the gallery's cards are pressed (and dragged), and the phone's sheet over the page is a seated slab
  "components/widgets/Gallery.tsx": ["raised"],
  "components/widgets/GallerySheet.tsx": ["seated"],
  // and its widgets: the tracks of a bar and a strip of days are carved, the shortcuts are pressed
  "components/widgets/bodies/MachineBodies.tsx": ["carved"],
  "components/widgets/bodies/ProtectionBodies.tsx": ["carved"],
  "components/widgets/bodies/ShortcutBodies.tsx": ["raised"],
  // raised: what is pressed
  "components/ui/Button.tsx": ["raised"],
  "components/ui/IconButton.tsx": ["raised"],
  "components/ui/ChoiceCards.tsx": ["raised"],
  // the frame: every item of a bar and of the second column is flat until the pointer is on it and the
  // current one is the sliding indicator's raised piece; the choice of bar is a carved rail of raised options
  "app/frame/Rail.tsx": ["raised"],
  "app/frame/areas.tsx": ["raised"],
  "app/frame/BottomBar.tsx": ["raised"],
  "app/frame/AreasSheet.tsx": ["raised"],
  // the phone's selector at the top of the page: one raised button that opens the sheet of the column's list
  "app/frame/SectionSelector.tsx": ["raised"],
  "app/frame/ColumnList.tsx": ["raised"],
  "app/Topbar.tsx": ["carved"],
  "app/Shell.tsx": ["seated"],
  "app/ThemeSwitch.tsx": ["carved"],
  "app/LanguageSwitch.tsx": ["carved"],
  // the screens before the console: the slab is seated, the hollow of its identity is carved into it, and the
  // doors under the form are pressed
  "components/page/AuthLayout.tsx": ["carved", "seated"],
  "components/page/AuthDoors.tsx": ["raised"],
};

/** The heights a source draws, by the utilities it writes. */
export function heightsUsed(source: string): Set<Height> {
  const used = new Set<Height>();
  for (const [height, pattern] of HEIGHT_UTILITIES) if (pattern.test(source)) used.add(height);
  return used;
}

/**
 * What is wrong with the heights of one kit file: it draws a height that is not its role's, or it
 * draws one and has no role. A file outside the kit is the ratchet's (`elev-outside-kit`).
 */
export function heightViolations(path: string, source: string): string[] {
  const used = heightsUsed(source);
  if (used.size === 0) return [];
  const allowed = KIT_HEIGHTS[path];
  if (allowed === undefined) return [`${path} draws ${[...used].join(" and ")} and has no role in KIT_HEIGHTS (styles/designRules.ts): say what it is`];
  return [...used].filter((height) => !allowed.includes(height)).map((height) => `${path} is ${allowed.join(" and ")} and draws ${height}`);
}

/** `design-exception: <rule-id> <reason>` on a line or the one above it excuses that line. */
export const EXCEPTION = /design-exception:\s*([a-z-]+)\s+(\S.*)?/;

/**
 * Matches of each rule in one file's source, skipping lines excused by a design-exception
 * comment for that rule. Multi-line matches are counted at the line they start on.
 */
export function countRules(path: string, source: string): Record<string, number> {
  const lines = source.split("\n");
  const starts: number[] = [];
  let offset = 0;
  for (const line of lines) {
    starts.push(offset);
    offset += line.length + 1;
  }
  const lineOf = (index: number): number => {
    let low = 0;
    let high = starts.length - 1;
    while (low < high) {
      const mid = (low + high + 1) >> 1;
      if ((starts[mid] ?? 0) <= index) low = mid;
      else high = mid - 1;
    }
    return low;
  };
  const excused = (line: number, rule: string): boolean =>
    [lines[line], lines[line - 1]].some((text) => {
      const match = text === undefined ? null : EXCEPTION.exec(text);
      return match?.[1] === rule;
    });
  const counts: Record<string, number> = {};
  for (const rule of DESIGN_RULES) {
    if (!rule.applies(path)) continue;
    let count = 0;
    for (const match of source.matchAll(new RegExp(rule.pattern.source, "g"))) {
      if (!excused(lineOf(match.index), rule.id)) count += 1;
    }
    if (rule.covered !== undefined) count = Math.max(0, count - [...source.matchAll(new RegExp(rule.covered.source, "g"))].length);
    if (count > 0) counts[rule.id] = count;
  }
  return counts;
}

/** Files whose count of ESLint-mirrored rules is not zero: ESLint's legacy list may hold only these. */
export function lintViolations(counts: Record<string, number>): number {
  return DESIGN_RULES.filter((rule) => rule.lint === true).reduce((sum, rule) => sum + (counts[rule.id] ?? 0), 0);
}
