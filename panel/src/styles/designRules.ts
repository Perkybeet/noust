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
    fix: "Weights are 400 and 500; 600 only through the title utility.",
    pattern: /\bfont-(?:semibold|bold|extrabold|black)\b/g,
    applies: everywhere,
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
    fix: "Use ConfirmDialog with the friction the action deserves (none, simple, type).",
    pattern: /\bAlertDialog\.Popup\b/g,
    applies: except("components/ui/ConfirmDialog.tsx"),
  },
  {
    // Owner item 56: whether a feature is on was a neutral notice in which only a word changed.
    id: "feature-state-notice",
    fix: "Show whether a feature is on with FeatureState (on, off, problem), never a Notice whose title or text is an on/off word.",
    pattern: /<Notice\b[^>]*\btitle=\{t\("[\w.]+\.(?:on|off|onTitle|offTitle|enabled|disabled|stateOn|stateOff)"\)|<Notice\b[^>]*>\s*\{t\("[\w.]+\.(?:on|off|onTitle|offTitle|enabled|disabled|stateOn|stateOff)"\)/g,
    applies: outsideKit,
  },
  {
    id: "page-without-template",
    fix: "Compose the page from a template (ListPage, DetailPage, SettingsLayout, DashboardPage, Wizard, FileEditorPage, AuthLayout).",
    pattern: /<PageHeader\b/g,
    applies: (path) => !path.startsWith("components/") && !path.startsWith("app/"),
  },
];

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
    if (count > 0) counts[rule.id] = count;
  }
  return counts;
}

/** Files whose count of ESLint-mirrored rules is not zero: ESLint's legacy list may hold only these. */
export function lintViolations(counts: Record<string, number>): number {
  return DESIGN_RULES.filter((rule) => rule.lint === true).reduce((sum, rule) => sum + (counts[rule.id] ?? 0), 0);
}
