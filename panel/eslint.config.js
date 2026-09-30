import { readFileSync } from "node:fs";

import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import { defineConfig } from "eslint/config";
import tseslint from "typescript-eslint";

// Colour lives in tokens; a raw hex in a component is a design-system bypass.
const HEX = {
  selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]",
  message: "Use a design token (var(--...)) instead of a raw colour.",
};

// The design system's rules (docs/DESIGN.md, "Enforcement"). The two patterns are the ones
// src/styles/designRules.ts counts for the ratchet, character for character: a test holds them
// equal. Components, the app shell and the gallery are the kit, and are exempt.
const ARBITRARY_DIMENSION = String.raw`(?:^|[\s"'\x60:])-?(?:m[trblxy]?|p[trblxy]?|gap(?:-[xy])?|space-[xy]|size|w|h|min-w|max-w|min-h|max-h|text|rounded(?:-[a-z]{1,2})?|top|right|bottom|left|inset(?:-[xy])?|z|leading|tracking|basis|shadow)-\[`;
const COLOUR_FUNCTION = String.raw`(?<![a-zA-Z0-9-])(?:rgba?|hsla?|oklch|oklab|color-mix)\((?=[\s_]*(?:\d|\.|in[\s_]|var\(|from[\s_]))`;

const DESIGN = [
  HEX,
  {
    selector: `Literal[value=/${ARBITRARY_DIMENSION}/]`,
    message: "arbitrary-dimension: use a named utility or token (h-control-md, max-w-measure, rounded-chip, z-overlay).",
  },
  {
    selector: `TemplateElement[value.raw=/${ARBITRARY_DIMENSION}/]`,
    message: "arbitrary-dimension: use a named utility or token (h-control-md, max-w-measure, rounded-chip, z-overlay).",
  },
  { selector: `Literal[value=/${COLOUR_FUNCTION}/]`, message: "colour-function: colour lives in tokens.css; use a token utility." },
  { selector: `TemplateElement[value.raw=/${COLOUR_FUNCTION}/]`, message: "colour-function: colour lives in tokens.css; use a token utility." },
  {
    selector: "JSXOpeningElement[name.name=/^h[1-4]$/]",
    message: "raw-heading: headings come from PageHeader, Section, Subsection, Card or a template.",
  },
  { selector: "JSXOpeningElement[name.name='label']", message: "raw-label: use Field, which wires the label, help and error to the control." },
  { selector: "JSXAttribute[name.name='style']", message: "inline-style: use a utility; inline styles belong to the kit (a chart, a virtual list)." },
];

const SEMANTIC_ICONS = {
  paths: [
    {
      name: "lucide-react",
      importNames: ["CircleCheck", "TriangleAlert", "CircleAlert", "OctagonAlert", "ShieldCheck", "ShieldAlert", "Info"],
      message: "semantic-icon-import: severity and verb icons come from components/ui/icons.ts (ICONS.success, ICONS.warning...).",
    },
  ],
};

// Files written before the design rules, not migrated yet: ESLint holds them to the hex rule
// only, and the ratchet (src/styles/design-rules.test.ts) holds every count in them down. The
// list only shrinks: `npm run design:baseline` drops a file once it breaks none of these rules.
const LEGACY = JSON.parse(readFileSync(new URL("./src/styles/design-baseline.json", import.meta.url), "utf8")).lintLegacy;

export default defineConfig(
  { ignores: ["dist", "test-results", "playwright-report", "src/routeTree.gen.ts", "src/api/schema.gen.ts"] },
  js.configs.recommended,
  tseslint.configs.strictTypeChecked,
  tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: { allowDefaultProject: ["eslint.config.js"] },
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    ...reactHooks.configs.flat["recommended-latest"],
  },
  {
    files: ["src/**/*.tsx"],
    ...jsxA11y.flatConfigs.strict,
  },
  {
    files: ["src/**/*.tsx"],
    rules: {
      // Scrollable regions (logs, tables, chart data) must take focus to be scrolled from
      // the keyboard (WCAG 2.1.1).
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["tabpanel", "region"] }],
    },
  },
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": ["error", ...DESIGN],
      "no-restricted-imports": ["error", SEMANTIC_ICONS],
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "@typescript-eslint/no-confusing-void-expression": ["error", { ignoreArrowShorthand: true }],
    },
  },
  {
    // The kit builds with what the rules forbid elsewhere; legacy files wait for their migration.
    files: ["src/components/**", "src/app/**", "src/dev/**", ...LEGACY],
    rules: { "no-restricted-syntax": ["error", HEX], "no-restricted-imports": "off" },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/styles/**"],
    rules: { "no-restricted-syntax": "off", "no-restricted-imports": "off" },
  },
  {
    files: ["**/*.js"],
    extends: [tseslint.configs.disableTypeChecked],
  },
  {
    files: ["*.{js,ts}", "scripts/**/*.mjs", "e2e/**/*.ts", "mock/**/*.ts"],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
);
