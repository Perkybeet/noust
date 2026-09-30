import type { LogLine, Status } from "../components/ui";

export interface SampleApp {
  domain: string;
  type: string;
  status: Status;
  port: number | null;
  commit: string;
  deployedMinutesAgo: number;
}

export const SAMPLE_APPS: SampleApp[] = [
  { domain: "shop.example.dev", type: "Next.js", status: "running", port: 3004, commit: "a1b2c3d", deployedMinutesAgo: 12 },
  { domain: "api.example.dev", type: "FastAPI", status: "deploying", port: 8001, commit: "9f8e7d6", deployedMinutesAgo: 1 },
  { domain: "status.example.dev", type: "Static", status: "static", port: null, commit: "4c5d6e7", deployedMinutesAgo: 2880 },
  { domain: "worker.example.dev", type: "Node.js", status: "failed", port: 3011, commit: "e3f4a5b", deployedMinutesAgo: 45 },
  { domain: "legacy.example.dev", type: "Vite", status: "stopped", port: 3020, commit: "0a9b8c7", deployedMinutesAgo: 20160 },
  { domain: "labs.example.dev", type: "Python", status: "unknown", port: 8040, commit: "77aa11c", deployedMinutesAgo: 360 },
];

export function ago(minutes: number): string {
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${String(minutes)} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${String(hours)} h ago`;
  return `${String(Math.round(hours / 24))} d ago`;
}

const ESC = "\u001b";

const BUILD_LOG = [
  `${ESC}[2m$ git fetch --depth 1 origin main${ESC}[0m`,
  "From github.com:acme/shop",
  " * branch            main       -> FETCH_HEAD",
  `${ESC}[2mHEAD is now at a1b2c3d Fix checkout rounding for EUR totals${ESC}[0m`,
  `${ESC}[1m${ESC}[36m==>${ESC}[0m ${ESC}[1mInstalling dependencies${ESC}[0m`,
  `${ESC}[2m$ npm ci --no-audit --no-fund${ESC}[0m`,
  `${ESC}[33mnpm warn${ESC}[0m deprecated inflight@1.0.6: This module is not supported, and leaks memory.`,
  `${ESC}[33mnpm warn${ESC}[0m deprecated glob@7.2.3: Glob versions prior to v9 are no longer supported`,
  "added 812 packages in 21s",
  `${ESC}[1m${ESC}[36m==>${ESC}[0m ${ESC}[1mBuilding${ESC}[0m`,
  `${ESC}[2m$ npm run build${ESC}[0m`,
  "",
  "> shop@4.2.0 build",
  "> next build",
  "",
  `   ${ESC}[1m${ESC}[35m▲ Next.js 15.5.2${ESC}[0m`,
  "   - Environments: .env.production",
  "",
  `   Creating an optimized production build ...`,
  ` ${ESC}[32m✓${ESC}[0m Compiled successfully in 18.4s`,
  ` ${ESC}[32m✓${ESC}[0m Linting and checking validity of types`,
  ` ${ESC}[32m✓${ESC}[0m Collecting page data`,
  ` ${ESC}[32m✓${ESC}[0m Generating static pages (38/38)`,
  "",
  "Route (app)                                 Size  First Load JS",
  "┌ ○ /                                    5.21 kB         118 kB",
  "├ ○ /cart                                3.02 kB         116 kB",
  "├ ƒ /checkout                            8.77 kB         124 kB",
  "└ ƒ /api/orders                            142 B         102 kB",
  "",
  `${ESC}[1m${ESC}[36m==>${ESC}[0m ${ESC}[1mActivating release 20260925-143012-a1b2c3d${ESC}[0m`,
  "Restarting wasm-shop.example.dev.service",
  `${ESC}[31mError:${ESC}[0m health check GET http://127.0.0.1:3004/ returned 502 after 30s`,
  `${ESC}[31m${ESC}[1mRolled back${ESC}[0m to release 20260924-101500-9f8e7d6. The previous version is serving traffic.`,
];

const LEVELS: Record<number, LogLine["level"]> = { 6: "warn", 7: "warn", 32: "error", 33: "error" };

export const SAMPLE_BUILD_LOG: LogLine[] = BUILD_LOG.map((text, index) => {
  const level = LEVELS[index];
  return { id: index + 1, text, ...(level ? { level } : {}) };
});

const JOURNAL = [
  "Started wasm-shop.example.dev.service - shop.example.dev (Next.js).",
  "▲ Next.js 15.5.2",
  "- Local:        http://127.0.0.1:3004",
  "✓ Ready in 412ms",
  "GET /checkout 200 in 38ms",
  "GET /api/orders 200 in 12ms",
  "POST /api/orders 201 in 64ms",
  "GET /cart 200 in 9ms",
];

let journalCounter = 0;

/** A plausible journal line for the streaming demo. */
export function nextJournalLine(id: number): LogLine {
  journalCounter += 1;
  const base = JOURNAL[journalCounter % JOURNAL.length] ?? "";
  const seconds = String(10 + (journalCounter % 50)).padStart(2, "0");
  return { id, ts: `14:31:${seconds}`, text: base };
}

/** Thirty minutes of minute samples with a believable load curve. */
export function sampleMetrics(): { timestamps: number[]; cpu: number[]; memory: number[] } {
  const start = Date.UTC(2026, 8, 25, 14, 0) / 1000;
  const timestamps: number[] = [];
  const cpu: number[] = [];
  const memory: number[] = [];
  for (let i = 0; i < 31; i++) {
    timestamps.push(start + i * 60);
    const wave = Math.sin(i / 4) * 9 + Math.sin(i / 1.7) * 4;
    cpu.push(Math.max(2, Math.round((22 + wave + (i > 20 && i < 25 ? 41 : 0)) * 10) / 10));
    memory.push(Math.round((412 + i * 3.2 + Math.sin(i / 3) * 14) * 10) / 10);
  }
  return { timestamps, cpu, memory };
}

/** Seven days of hourly samples, for the chart demo's adaptive date labels. */
export function sampleWeekMetrics(): { timestamps: number[]; cpu: number[] } {
  const end = Date.UTC(2026, 8, 25, 14, 0) / 1000;
  const start = end - 7 * 86_400;
  const timestamps: number[] = [];
  const cpu: number[] = [];
  for (let i = 0; i <= 7 * 24; i++) {
    const t = start + i * 3_600;
    timestamps.push(t);
    const daily = Math.sin((t % 86_400) / 86_400 * 2 * Math.PI - Math.PI / 2) * 8;
    cpu.push(Math.max(2, Math.round((20 + daily + Math.sin(i / 9) * 6) * 10) / 10));
  }
  return { timestamps, cpu };
}

/**
 * A day of 10-minute cells for the chart demo's reading features: history that starts six hours
 * into the window, an hour the monitor was down (null cells, a break, never zero), a mean with
 * its peak per cell, and memory creeping towards its limit.
 */
export function sampleDayWithGaps(): {
  timestamps: number[];
  domain: readonly [number, number];
  firstSampleAt: number;
  cpu: (number | null)[];
  cpuPeak: (number | null)[];
  memory: (number | null)[];
} {
  const end = Date.UTC(2026, 8, 25, 14, 0) / 1000;
  const start = end - 86_400;
  const step = 600;
  const firstSampleAt = start + 6 * 3_600;
  const timestamps: number[] = [];
  const cpu: (number | null)[] = [];
  const cpuPeak: (number | null)[] = [];
  const memory: (number | null)[] = [];
  for (let at = firstSampleAt; at <= end; at += step) {
    const i = (at - firstSampleAt) / step;
    timestamps.push(at);
    // The monitor was down for an hour: no reading, drawn as a break.
    const down = i >= 40 && i < 46;
    const mean = Math.max(3, 18 + Math.sin(i / 7) * 7 + Math.sin(i / 2.3) * 3 + (i > 70 && i < 76 ? 30 : 0));
    cpu.push(down ? null : Math.round(mean * 10) / 10);
    cpuPeak.push(down ? null : Math.round((mean * 1.6 + 4) * 10) / 10);
    memory.push(down ? null : Math.round((380 + i * 1.9 + Math.sin(i / 5) * 12) * 10) / 10);
  }
  return { timestamps, domain: [start, end] as const, firstSampleAt, cpu, cpuPeak, memory };
}
