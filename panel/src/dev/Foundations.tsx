import { Badge, ICON_SIZE, ICONS, StatusGlyph, StatusPill, stateTextClass } from "../components/ui";
import type { Status } from "../components/ui";
import { contrastRatio, deltaE, hueDistance, oklch, readThemeTokens } from "../lib/contrast";
import { cx } from "../lib/cx";
import tokensCss from "../styles/tokens.css?raw";
import { BothThemes, Item, Row, Section, Stage } from "./gallery";

const TOKENS = readThemeTokens(tokensCss);
const THEMES = ["light", "dark"] as const;

interface Swatch {
  token: string;
  role: string;
  /** Ground the token is read on, to print its contrast. */
  on?: string;
}

const GROUPS: { title: string; swatches: Swatch[] }[] = [
  {
    title: "Surfaces",
    swatches: [
      { token: "bg-sunken", role: "Logs, code, dialog and card footers" },
      { token: "bg", role: "The page" },
      { token: "surface", role: "Cards, tables" },
      { token: "surface-raised", role: "Everything that floats; info notices" },
      { token: "surface-hover", role: "Hover on a row or control" },
      { token: "surface-active", role: "Pressed, current nav item" },
      { token: "border", role: "Hairlines that separate", on: "surface" },
      { token: "border-strong", role: "Bounds of a control, 3:1", on: "surface" },
    ],
  },
  {
    title: "Text",
    swatches: [
      { token: "text", role: "Content", on: "bg" },
      { token: "text-muted", role: "Secondary", on: "bg" },
      { token: "text-faint", role: "Meta, placeholders, never decoration", on: "surface-raised" },
    ],
  },
  {
    title: "Interactive (violet)",
    swatches: [
      { token: "accent", role: "Primary button fill", on: "surface" },
      { token: "accent-hover", role: "Its hover" },
      { token: "accent-soft", role: "Selected ground" },
      { token: "accent-text", role: "Links, the active tab rule", on: "accent-soft" },
      { token: "focus", role: "Focus ring", on: "bg" },
      { token: "selection", role: "Selected text" },
      { token: "match", role: "Search match in logs" },
    ],
  },
  {
    title: "State",
    swatches: [
      { token: "ok", role: "Running, succeeded", on: "ok-soft" },
      { token: "warn", role: "In progress, queued, warning", on: "warn-soft" },
      { token: "fail", role: "Failed, down", on: "fail-soft" },
      { token: "fail-strong", role: "Danger button fill" },
      { token: "idle", role: "Stopped on purpose, unknown", on: "idle-soft" },
    ],
  },
  {
    title: "State tints: soft ground and border",
    swatches: [
      { token: "ok-soft", role: "Success notice ground" },
      { token: "ok-border", role: "Its border, 1.4:1 or more", on: "ok-soft" },
      { token: "warn-soft", role: "Warning notice ground" },
      { token: "warn-border", role: "Its border", on: "warn-soft" },
      { token: "fail-soft", role: "Error notice, ErrorBlock" },
      { token: "fail-border", role: "Its border", on: "fail-soft" },
      { token: "idle-soft", role: "Stopped pill ground" },
      { token: "idle-border", role: "Its border", on: "idle-soft" },
    ],
  },
  {
    title: "Chart series (inside a plot only)",
    swatches: [
      { token: "viz-1", role: "Series 1, solid", on: "surface" },
      { token: "viz-2", role: "Series 2, dashed", on: "surface" },
    ],
  },
];

/** A solid token's value in a theme; a missing one is a bug in tokens.css, said loudly. */
function solid(theme: "light" | "dark", token: string): string {
  const value = TOKENS[theme][token];
  if (value === undefined) throw new Error(`--${token} is not a solid colour in the ${theme} theme`);
  return value;
}

function ratio(theme: "light" | "dark", fg: string, bg: string): string {
  const a = TOKENS[theme][fg];
  const b = TOKENS[theme][bg];
  if (a === undefined || b === undefined) return "-";
  return `${contrastRatio(a, b).toFixed(1)}:1`;
}

function ThemeChip({ theme, token }: { theme: "light" | "dark"; token: string }) {
  return (
    <div data-theme={theme} className="flex h-12 flex-1 items-end rounded-control border border-border bg-bg p-1.5">
      <div className="h-full w-full rounded-chip border border-border" style={{ background: `var(--${token})` }} />
    </div>
  );
}

function Palette() {
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      {GROUPS.map((group) => (
        <div key={group.title} className="min-w-0">
          <h3 className="title mb-3 text-14 text-fg">{group.title}</h3>
          <ul className="flex flex-col divide-y divide-border rounded-card border border-border bg-surface">
            {group.swatches.map((swatch) => (
              <li key={swatch.token} className="grid grid-cols-[7.5rem_1fr] items-center gap-4 px-3 py-2.5 sm:grid-cols-[9rem_1fr_auto]">
                <div className="flex gap-1.5">
                  <ThemeChip theme="light" token={swatch.token} />
                  <ThemeChip theme="dark" token={swatch.token} />
                </div>
                <div className="min-w-0">
                  <div className="mono truncate text-12 text-fg">--{swatch.token}</div>
                  <div className="text-12 text-fg-muted">{swatch.role}</div>
                </div>
                <div className="mono col-span-2 text-12 text-fg-faint sm:col-span-1 sm:text-right">
                  <div>
                    {TOKENS.light[swatch.token]} / {TOKENS.dark[swatch.token]}
                  </div>
                  {swatch.on !== undefined ? (
                    <div className="text-fg-muted">
                      {ratio("light", swatch.token, swatch.on)} / {ratio("dark", swatch.token, swatch.on)}
                      <span className="text-fg-faint"> on {swatch.on}</span>
                    </div>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** The two series and the neutral third, drawn as a chart draws them, with their measured distances. */
function SeriesFamily() {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {THEMES.map((theme) => {
        const a = solid(theme, "viz-1");
        const b = solid(theme, "viz-2");
        const hue = (token: string): number => oklch(solid(theme, token))[2];
        const nearest = Math.min(
          ...["viz-1", "viz-2"].flatMap((viz) => ["accent", "ok", "warn", "fail"].map((other) => hueDistance(hue(viz), hue(other)))),
        );
        return (
          <div key={theme} data-theme={theme} className="rounded-card border border-border bg-surface p-5 text-fg">
            <svg viewBox="0 0 240 80" className="h-20 w-full" aria-hidden="true">
              <path d="M0 60 L40 48 L80 52 L120 30 L160 36 L200 18 L240 24" fill="none" stroke="var(--viz-1)" strokeWidth="2" />
              <path d="M0 70 L40 64 L80 58 L120 62 L160 50 L200 54 L240 44" fill="none" stroke="var(--viz-2)" strokeWidth="2" strokeDasharray="4 3" />
              <path d="M0 40 L40 44 L80 36 L120 46 L160 40 L200 42 L240 36" fill="none" stroke="var(--text-muted)" strokeWidth="1.5" strokeDasharray="1.5 3" />
            </svg>
            <dl className="mono mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-12">
              <dt className="text-fg-muted">ΔE normal vision</dt>
              <dd>{deltaE(a, b).toFixed(1)} (15 or more)</dd>
              <dt className="text-fg-muted">ΔE protanopia</dt>
              <dd>{deltaE(a, b, "protan").toFixed(1)} (8 or more)</dd>
              <dt className="text-fg-muted">ΔE deuteranopia</dt>
              <dd>{deltaE(a, b, "deutan").toFixed(1)} (8 or more)</dd>
              <dt className="text-fg-muted">Nearest hue, state or accent</dt>
              <dd>{nearest.toFixed(0)}° (35 or more)</dd>
            </dl>
          </div>
        );
      })}
    </div>
  );
}

const TYPE: { name: string; spec: string; className: string; sample: string }[] = [
  { name: "Page title", spec: "24/32 title, the h1", className: "title text-24", sample: "Applications" },
  { name: "Page title, mono", spec: "24/32 mono 500", className: "mono text-24 font-medium", sample: "shop.example.com" },
  { name: "Section title", spec: "16/24 title, every h2", className: "title text-16", sample: "Deployments" },
  { name: "Subsection title", spec: "14/20 title, every h3", className: "title text-14", sample: "Startup check" },
  { name: "Reading", spec: "18/24 title, a tile's value", className: "title text-18", sample: "14 of 17 running" },
  { name: "Body", spec: "14/20 400, prose", className: "text-14", sample: "The previous version keeps serving until the new one answers." },
  { name: "Dense UI", spec: "13/20 400, the working size", className: "text-13", sample: "Restarted shop-example-com.service after a configuration change." },
  { name: "Label", spec: "13/20 500", className: "text-13 font-medium", sample: "Domain" },
  { name: "Help and meta", spec: "12/16 400", className: "text-12 text-fg-muted", sample: "Deployed 12 min ago by webhook" },
  { name: "System value", spec: "mono, 0.92em of its line", className: "mono text-13", sample: "/var/www/apps/shop/releases/20260925-143012" },
  { name: "System output", spec: "mono 12, verbatim", className: "mono text-12", sample: "nginx: [emerg] unknown directive \"proxy_pas\" in /etc/nginx/sites-enabled/shop:14" },
  { name: "Display", spec: "32/40, this gallery only", className: "display text-32", sample: "Your server, deployed." },
];

function TypeScale() {
  return (
    <Stage plain flush>
      <ul className="divide-y divide-border">
        {TYPE.map((row) => (
          <li key={row.name} className="grid gap-1 px-6 py-4 sm:grid-cols-[10rem_1fr] sm:items-baseline sm:gap-6 max-sm:px-4">
            <div className="flex items-baseline gap-2 sm:flex-col sm:gap-0.5">
              <span className="text-13 font-medium text-fg">{row.name}</span>
              <span className="mono text-12 text-fg-faint">{row.spec}</span>
            </div>
            <p className={cx("min-w-0 break-words text-fg", row.className)}>{row.sample}</p>
          </li>
        ))}
      </ul>
    </Stage>
  );
}

// Half steps to 16px, then 20, 24, 32, 40, 48, 64: nothing else.
const SPACE: [string, number][] = [
  ["0.5", 2],
  ["1", 4],
  ["1.5", 6],
  ["2", 8],
  ["2.5", 10],
  ["3", 12],
  ["3.5", 14],
  ["4", 16],
  ["5", 20],
  ["6", 24],
  ["8", 32],
  ["10", 40],
  ["12", 48],
  ["16", 64],
];

function Spacing() {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Stage plain>
        <ul className="flex flex-col gap-2">
          {SPACE.map(([step, px]) => (
            <li key={step} className="grid grid-cols-[5rem_1fr] items-center gap-3">
              <span className="mono text-12 text-fg-muted">
                {step} · {px}px
              </span>
              <span className="block h-3 rounded-chip bg-border-strong" style={{ width: px * 2 }} />
            </li>
          ))}
        </ul>
        <p className="mt-4 text-12 text-fg-muted">Not on the scale: 7, 9, 11, 13, 14, 15, 6.5 and every arbitrary value.</p>
      </Stage>
      <Stage plain>
        <dl className="grid grid-cols-[1fr_auto] gap-x-6 gap-y-2 text-13">
          <dt className="text-fg-muted">Header to content</dt>
          <dd className="mono">32</dd>
          <dt className="text-fg-muted">Between sections</dt>
          <dd className="mono">32</dd>
          <dt className="text-fg-muted">Section heading to its content</dt>
          <dd className="mono">16</dd>
          <dt className="text-fg-muted">Fields of a form</dt>
          <dd className="mono">20</dd>
          <dt className="text-fg-muted">Card body (md, sm)</dt>
          <dd className="mono">20, 16</dd>
          <dt className="text-fg-muted">Notice (banner, inline)</dt>
          <dd className="mono">16×12, 12×10</dd>
          <dt className="text-fg-muted">Table cell</dt>
          <dd className="mono">12, first 16</dd>
          <dt className="text-fg-muted">Page padding (phone, tablet, desktop)</dt>
          <dd className="mono">16, 24, 32</dd>
        </dl>
      </Stage>
    </div>
  );
}

function Shape() {
  return (
    <Stage plain>
      <Row>
        <Item label="Chip, 4: keys, badges, inline code">
          <div className="h-8 w-20 rounded-chip border border-border-strong bg-surface" />
        </Item>
        <Item label="Control, 6: buttons, fields, notices inline">
          <div className="size-16 rounded-control border border-border-strong bg-surface" />
        </Item>
        <Item label="Card, 10: cards, tables, overlays, banners">
          <div className="size-16 rounded-card border border-border bg-surface shadow-raised" />
        </Item>
        <Item label="Pill, 999: status, meters, tab rule">
          <div className="h-8 w-20 rounded-pill border border-border bg-surface" />
        </Item>
      </Row>
    </Stage>
  );
}

const SIZES: { token: string; value: string; use: string }[] = [
  { token: "--control-sm", value: "28px (36 coarse)", use: "Buttons and fields in rows, cards, toolbars" },
  { token: "--control-md", value: "32px (40 coarse)", use: "The default control" },
  { token: "--control-lg", value: "40px (44 coarse)", use: "Sign in, unlock, the wizard's Deploy" },
  { token: "--z-sticky", value: "30", use: "Top bar, save bar, wizard bar" },
  { token: "--z-backdrop", value: "40", use: "The veil behind a dialog or drawer" },
  { token: "--z-overlay", value: "50", use: "Dialog, drawer, menu, popover, tooltip" },
  { token: "--z-toast", value: "60", use: "Toasts, over everything the operator is doing" },
  { token: "--z-skip", value: "70", use: "The skip link" },
];

const WIDTHS: { token: string; value: string; use: string }[] = [
  { token: "--width-page", value: "1600px", use: "The shell's cap: T1, T2, T4, T6" },
  { token: "--width-settings-nav", value: "200px", use: "T3's side navigation, T5's step rail" },
  { token: "--width-settings", value: "880px", use: "T3's content column" },
  { token: "--width-wizard", value: "736px", use: "T5's step column" },
  { token: "--width-auth", value: "400px", use: "T7's single column" },
  { token: "--width-dialog-sm | md | lg | xl", value: "440 / 560 / 720 / 1280", use: "Question / form / two columns or steps / looking closely" },
  { token: "--width-drawer-md | lg", value: "480 / 720", use: "Forms and detail / logs" },
  { token: "--width-search", value: "288px", use: "Every FilterBar search box" },
  { token: "--measure | --measure-help", value: "68ch / 52ch", use: "Prose / help under a field" },
];

function TokenTable({ rows }: { rows: { token: string; value: string; use: string }[] }) {
  return (
    <div className="overflow-x-auto rounded-card border border-border bg-surface">
      <table className="w-full text-left text-13">
        <thead>
          <tr className="border-b border-border bg-bg-sunken text-12 text-fg-muted">
            <th scope="col" className="h-9 px-3 pl-4 font-medium">
              Token
            </th>
            <th scope="col" className="h-9 px-3 font-medium">
              Value
            </th>
            <th scope="col" className="h-9 px-3 font-medium">
              Use
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.token} className="border-b border-border last:border-0">
              <td className="mono h-9 px-3 pl-4 text-12 whitespace-nowrap">{row.token}</td>
              <td className="mono h-9 px-3 text-12 whitespace-nowrap text-fg-muted">{row.value}</td>
              <td className="h-9 px-3 text-fg-muted">{row.use}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Icons() {
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Stage plain>
        <ul className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          {Object.entries(ICONS).map(([name, Icon]) => (
            <li key={name} className="flex items-center gap-2 text-13">
              <Icon aria-hidden="true" className="size-icon-md text-fg-muted" />
              <span className="mono text-12">{name}</span>
            </li>
          ))}
        </ul>
      </Stage>
      <Stage plain>
        <Row>
          {Object.entries(ICON_SIZE).map(([size, utility]) => (
            <Item key={size} label={`${size} · ${utility}`}>
              <ICONS.info aria-hidden="true" className={cx(utility, "text-fg")} />
            </Item>
          ))}
        </Row>
      </Stage>
    </div>
  );
}

function Elevation() {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {THEMES.map((theme) => (
        <div key={theme} data-theme={theme} className="rounded-card border border-border bg-bg-sunken p-5 text-fg">
          <p className="mb-4 text-12 text-fg-muted">{theme === "light" ? "Light: surface plus a whisper of shadow" : "Dark: each step up is lighter"}</p>
          <div className="rounded-card border border-border bg-bg p-4">
            <span className="text-12 text-fg-faint">bg</span>
            <div className="mt-2 rounded-card border border-border bg-surface p-4 shadow-raised">
              <span className="text-12 text-fg-faint">surface</span>
              <div className="mt-2 rounded-card border border-border bg-surface-raised p-4 shadow-overlay">
                <span className="text-12 text-fg-faint">surface-raised, overlay shadow</span>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

const STATES: Status[] = ["running", "deploying", "queued", "warning", "failed", "stopped", "static", "unknown"];

function StateLanguage() {
  return (
    <BothThemes>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
        {STATES.map((state) => (
          <li key={state} className="flex flex-col items-start gap-2">
            <StatusGlyph state={state} size={24} className={stateTextClass(state)} />
            <StatusPill state={state} />
          </li>
        ))}
      </ul>
    </BothThemes>
  );
}

export function Foundations() {
  return (
    <>
      <Section
        id="colour"
        title="Colour"
        description="Colour means something or it is not there. Surfaces are achromatic; hue is spent on state, on what can be acted on (violet), and on a chart's series inside the plot. Each row shows the light and dark value and, for foreground tokens, the contrast on the ground they are read on."
      >
        <Palette />
      </Section>
      <Section
        id="state"
        title="State language"
        description="Every state is said three ways: colour, a distinct shape and a word. The four state colours are nearly indistinguishable to a deuteranope (ΔE 3.3), which is why the shape and the word are required, not decoration. Queued is a still dashed ring: waiting is not work."
      >
        <StateLanguage />
      </Section>
      <Section
        id="viz"
        title="Chart series"
        description="A series' identity lives in its own family, never in the accent (violet says 'act on this') or a state colour. Two chromatic series and a neutral third, each with its own stroke; a fourth is a small multiple. The distances below are computed live from tokens.css, as tokens.test.ts does."
      >
        <SeriesFamily />
      </Section>
      <Section
        id="type"
        title="Type roles"
        description="Mona Sans for the interface, JetBrains Mono for every value that comes from the system. Sizes are only these seven (12, 13, 14, 16, 18, 24, 32); weights 400 and 500, with 600 only through the title utility. Figures are tabular wherever numbers change."
      >
        <TypeScale />
      </Section>
      <Section
        id="space"
        title="Space"
        description="Tailwind's quarter-rem step, in half steps (2px) up to 16px inside components, then 20, 24, 32, 40, 48 and 64 for layout. The rhythm of a page is fixed by its template, never by the feature."
      >
        <Spacing />
      </Section>
      <Section id="shape" title="Shape" description="Four radii and one-pixel borders. The focus ring is 2px, offset 2, drawn once globally.">
        <Shape />
      </Section>
      <Section
        id="sizes"
        title="Controls and layers"
        description="Every control reads its height from a token, so a coarse pointer grows them all to a thumb's target in one place. Stacking is named: nothing else sets a z-index."
      >
        <TokenTable rows={SIZES} />
      </Section>
      <Section
        id="widths"
        title="Widths"
        description="The shell caps the page at 1600px and each template sets its inner grid from these tokens: a content column is never narrower than its own header."
      >
        <TokenTable rows={WIDTHS} />
      </Section>
      <Section
        id="icons"
        title="Icons"
        description="One set, lucide, at five sizes. A meaning has one icon, from components/ui/icons.ts; an entity's state is StatusGlyph's, a separate vocabulary. An icon replaces a word only in an IconButton, whose label is its accessible name and tooltip."
      >
        <Icons />
      </Section>
      <Section
        id="elevation"
        title="Elevation and motion"
        description="In dark, height is luminance: each layer is a step lighter. In light, surfaces add a whisper of shadow; overlays alone cast one. Motion is 120-180ms ease-out on colour and opacity, a state change pulses once, nothing slides for a state, and all of it drops to zero for reduced motion."
      >
        <Elevation />
        <Row>
          <Badge mono>--duration-fast 120ms</Badge>
          <Badge mono>--duration-base 180ms</Badge>
          <Badge mono>--ease-out cubic-bezier(0.16, 1, 0.3, 1)</Badge>
        </Row>
      </Section>
    </>
  );
}
