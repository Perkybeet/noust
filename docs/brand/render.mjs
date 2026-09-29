// Renders the Noust raster icons and the brand previews from the hand-written SVGs.
//
//   export PATH=/home/yago/.local/node22/bin:$PATH   # or any Node 22
//   node docs/brand/render.mjs [path/to/noust-logo-reference.png]
//
// Uses the Chromium that the console's Playwright suite already installs, so it runs offline
// with nothing added. The SVGs are the source; every PNG here is output and can be regenerated.
// With a reference image, also writes previews/compare.jpg: the approved raster above the redraw,
// at the same scale, and the two overlaid.

import { mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const brand = join(root, "panel/src/assets/brand");
const publicDir = join(root, "panel/public");
const previews = join(here, "previews");
mkdirSync(previews, { recursive: true });

const require = createRequire(join(root, "panel/package.json"));
const { chromium } = require("playwright");

const url = (file) => pathToFileURL(file).href;
const svg = (name) => url(join(brand, name));
const reference = process.argv[2];

const browser = await chromium.launch();

/** Screenshots `html` laid out in a box of `width` x `height` CSS pixels. */
async function shoot(path, width, height, html, { scheme = "light", transparent = false } = {}) {
  const page = await browser.newPage({ viewport: { width, height }, colorScheme: scheme });
  await page.goto(url(join(here, "BRAND.md"))); // any file: origin, so file:// images load
  await page.setContent(`<!doctype html><body style="margin:0">${html}</body>`, { waitUntil: "load" });
  await page.screenshot(path.endsWith(".jpg") ? { path, type: "jpeg", quality: 80 } : { path, omitBackground: transparent });
  await page.close();
}

const img = (src, w, h, extra = "") => `<img src="${src}" width="${w}" height="${h}" style="display:block;${extra}">`;

// Raster icons for the console. The favicon PNG keeps a transparent ground; the touch and
// app icons get an opaque tile, as iOS and Android draw a transparent icon on black.
await shoot(join(publicDir, "favicon-32.png"), 32, 32, img(svg("noust-icon.svg"), 32, 32), { transparent: true });
for (const [name, size] of [["apple-touch-icon.png", 180], ["icon-192.png", 192], ["icon-512.png", 512]]) {
  const inner = Math.round(size * 0.8);
  const pad = (size - inner) / 2;
  await shoot(join(publicDir, name), size, size, `<div style="width:${size}px;height:${size}px;background:#fff;padding:${pad}px;box-sizing:border-box">${img(svg("noust-icon.svg"), inner, inner)}</div>`);
}

// The icon at every size it has to survive, light and dark.
for (const size of [16, 32, 64, 256, 1024]) {
  await shoot(join(previews, `icon-${size}.png`), size, size, img(svg("noust-icon.svg"), size, size), { transparent: true });
}

// The mark and the lockup by height, on both grounds.
const row = (items, bg, color) =>
  `<div style="display:inline-flex;align-items:center;gap:32px;padding:24px;background:${bg};color:${color}">${items}</div>`;
for (const [name, file, ratio] of [
  ["mark", "noust-mark.svg", 140 / 36],
  ["mark-mono", "noust-mark-mono.svg", 140 / 36],
  ["wordmark", "noust-wordmark.svg", 254 / 36],
  ["wordmark-mono", "noust-wordmark-mono.svg", 254 / 36],
]) {
  const sizes = [16, 32, 64, 256];
  const items = sizes.map((h) => img(svg(file), Math.round(h * ratio), h)).join("");
  const width = sizes.reduce((sum, h) => sum + Math.round(h * ratio) + 32, 48);
  await shoot(join(previews, `${name}.png`), width, 256 + 48, row(items, "#ffffff", "#18181b"));
}

// Dark: the console draws the mark inline with its ink in the text colour; a file cannot know
// the page's theme, so this shows the favicon's own media query and the mono file on white text.
await shoot(
  join(previews, "dark.png"),
  900,
  180,
  row(
    [img(url(join(publicDir, "favicon.svg")), 32, 32), img(url(join(publicDir, "favicon.svg")), 128, 128), img(svg("noust-wordmark-mono.svg"), 508, 72, "filter:invert(1)")].join(""),
    "#111111",
    "#ededed",
  ),
  { scheme: "dark" },
);

if (reference) {
  // The approved raster is 1254 px square; the mark spans x 85-1168, y 494-787 in it.
  const scale = 1083 / 140;
  const w = 1200;
  const h = 360;
  const refLayer = `<img src="${url(resolve(reference))}" style="position:absolute;left:${60 - 85}px;top:${40 - 494}px">`;
  const drawLayer = (opacity) => img(svg("noust-mark.svg"), 1083, Math.round(36 * scale), `position:absolute;left:60px;top:40px;opacity:${opacity}`);
  const panel = (inner) => `<div style="position:relative;width:${w}px;height:${h}px;overflow:hidden;background:#fff">${inner}</div>`;
  readFileSync(reference); // fail loudly on a wrong path rather than render an empty panel
  await shoot(join(previews, "compare.jpg"), w, h * 3, panel(refLayer) + panel(drawLayer(1)) + panel(refLayer + drawLayer(0.5)));
}

await browser.close();
