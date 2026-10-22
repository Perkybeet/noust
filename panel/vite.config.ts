import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import type { Plugin, ProxyOptions } from "vite";

import { mockBackend } from "./mock/backend.ts";
import { LICENSES_FILE, licensesOf } from "./scripts/bundle-licenses.mjs";

// The console talks to the real FastAPI backend in development. Point VITE_BACKEND at any
// running `wasm web` instance; the default is the panel's own default bind address.
const DEFAULT_BACKEND = "http://127.0.0.1:8080";

/**
 * The font files every page draws with before anything else: the Latin subsets of Mona Sans
 * (the interface) and JetBrains Mono (system values, the hostname in the top bar). Only the
 * Latin ones: the other subsets load when a glyph of theirs is on screen, which is rare.
 */
const CRITICAL_FONTS = [/\/mona-sans-latin-wdth-normal-[\w-]+\.woff2$/, /\/jetbrains-mono-latin-wght-normal-[\w-]+\.woff2$/];

/**
 * Preloads the critical fonts from index.html. Without it the browser discovers them only once
 * the stylesheet is parsed and the first text is laid out, so the page is drawn in the fallback
 * and redrawn on arrival. A `<link rel="preload">` is markup, not script or style, so the
 * strict CSP (no inline anything) is untouched; `crossorigin` is required for a font preload to
 * be reused by the @font-face request. Build only: the dev server serves fonts from
 * node_modules paths that have no hash to find.
 */
function preloadFonts(): Plugin {
  return {
    name: "wasm-preload-fonts",
    apply: "build",
    transformIndexHtml: {
      order: "post",
      handler(_html, context) {
        const files = Object.keys(context.bundle ?? {}).map((name) => `/${name}`);
        return CRITICAL_FONTS.flatMap((pattern) => {
          const href = files.find((file) => pattern.test(file));
          if (href === undefined) throw new Error(`no built font matches ${String(pattern)}: the preload would be silently lost`);
          return [{ tag: "link", attrs: { rel: "preload", href, as: "font", type: "font/woff2", crossorigin: "" }, injectTo: "head" as const }];
        });
      },
    },
  };
}

/**
 * Writes `third-party-licenses.txt` next to index.html: the copyright and the licence text of every npm
 * package whose code, stylesheet or font went into this build (React, Base UI, TanStack, lucide and the
 * rest are MIT or ISC, which ask for both to travel with every copy). The build is committed and shipped
 * in the wheel and the packages, and NOTICE points to this file. It is read from what the bundler wrote
 * into each chunk, so a package that was tree-shaken out is not listed and a new one is, with nothing to
 * remember; scripts/bundle-licenses.mjs has the reading and its tests.
 */
function thirdPartyLicenses(): Plugin {
  return {
    name: "noust-third-party-licenses",
    apply: "build",
    generateBundle(_options, bundle) {
      this.emitFile({
        type: "asset",
        fileName: LICENSES_FILE,
        source: licensesOf(bundle, {
          // Tailwind and uPlot are bare imports of the entry stylesheet, compiled into the CSS and so not a module of any chunk.
          projectDir: import.meta.dirname,
          stylesheets: ["src/styles/app.css"],
        }),
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const backend = env.VITE_BACKEND ?? DEFAULT_BACKEND;
  // VITE_MOCK=1 answers the API from mock/backend.ts inside the dev server, for reviewing the
  // console without a Python backend. Serve-only: `vite build` never includes it.
  const mock = env.VITE_MOCK === "1";
  const proxy: Record<string, ProxyOptions> = mock
    ? {}
    : {
        "/api": { target: backend, changeOrigin: false },
        "/events": { target: backend, changeOrigin: false },
        "/hooks": { target: backend, changeOrigin: false },
        "/ws": { target: backend, ws: true, changeOrigin: false },
      };

  return {
    // The router plugin must run before the React plugin so that it can split route files.
    plugins: [
      tanstackRouter({ target: "react", autoCodeSplitting: true, quoteStyle: "double" }),
      react(),
      tailwindcss(),
      preloadFonts(),
      thirdPartyLicenses(),
      ...(mock ? [mockBackend()] : []),
    ],
    base: "/",
    build: {
      // The build is committed: OBS packages from `git archive HEAD` with no network and
      // never runs Node, so the Python package has to carry the console ready to serve.
      // CI rebuilds and fails when this directory differs from what the source produces.
      outDir: "../src/noust/web/static",
      emptyOutDir: true,
      assetsDir: "assets",
      // Never inline an asset as a data: URI. Vite inlines anything under 4 KiB by default,
      // which turned the smallest font subset into data:font/woff2 - and the CSP's
      // `font-src 'self'` blocks that, so the glyphs it covers silently fell back.
      assetsInlineLimit: 0,
      sourcemap: false,
      // Rolldown (Vite 8) names chunks by content hash and has no parallel-file-ops or
      // CommonJS-plugin knobs; determinism is verified by building twice and diffing.
      reportCompressedSize: false,
      rolldownOptions: {
        output: {
          // An icon that three or more chunks draw goes to one chunk of icons, not to a file of its
          // own each. Left alone, rolldown made each such icon a chunk, and every route that reaches
          // one lists it in the preload of its stub: a hundred file names in a hundred stubs, which
          // the first page pays for and no icon is worth (about 18 kB gzip of totalJs and 1 kB of the
          // initial script, measured on the navigation lane). One chunk is one name in each list, and
          // one request where there were dozens. An icon only one or two chunks draw stays with them.
          codeSplitting: { groups: [{ name: "lucide", test: /node_modules[\\/]lucide-react/, minShareCount: 3 }] },
        },
      },
    },
    server: {
      proxy,
    },
  };
});
