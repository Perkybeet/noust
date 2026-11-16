import { describe, expect, it } from "vitest";

/**
 * A component says an API error through `describeError`, never by reading `error.detail` or `error.hint`.
 *
 * Those two are the server's English. When the error carries the code of one of the server's sentences,
 * `describeError` says it in this console's language; a component that draws `error.detail` itself shows
 * English in a Spanish console and did, for the sign-in, the confirmation and a dozen forms. This holds
 * the console to it the way the project holds its other rules: a file may read them no more often than the
 * list below says, so a new read fails here, and a file that reads fewer fails until its line is lowered.
 *
 * What is left in the list is a read that is not to draw the words: logic that matches the English
 * (`detail.includes("exists already")`, which an error with a code would not break because the English is
 * always sent), a message the console wrote itself (a cancelled confirmation), or the reader of an error in
 * a place `describeError` does not reach yet. The files that are the readers (`api/`, `lib/errors.ts`,
 * `lib/held.ts`) are not counted.
 */

const RAW = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../test/**", "!../api/schema.gen.ts"], { eager: true, query: "?raw", import: "default" });

const PATTERN = /\b(?:\w+\.)*(?:error|err|cause|exception)\.(?:detail|hint)\b/g;

/** The readers: the contract itself, the one function that says an error, and what an approval answers. */
const READERS = new Set(["api/client.ts", "api/errors.ts", "lib/errors.ts", "lib/held.ts", "lib/errorBuffer.ts", "lib/serverSentence.ts"]);

/** Reads of `error.detail` or `error.hint` that are still not `describeError`, by file. It only goes down. */
const STILL_READ: Readonly<Record<string, number>> = {
  "features/app/files/FilesPage.tsx": 1,
  // The assistant's own turn error (a sentence with its code, said through `say`) and the raw answer of a call a person approves.
  "features/assistant/TurnView.tsx": 2,
  "features/assistant/runProposal.ts": 2,
  "features/app/schemaChange.ts": 2,
  "features/app/settings/StartupCheck.tsx": 1,
  "features/backups/DestinationsPage.tsx": 1,
  "features/cron/CronJobDialog.tsx": 1,
  "features/domains/SiteConfigPage.tsx": 1,
  "features/domains/configErrors.ts": 1,
  "features/fleet/NodeJobDrawer.tsx": 2,
  "features/new-app/AdoptStack.tsx": 2,
  "features/new-app/exportFile.ts": 2,
  "features/new-app/wizard.ts": 3,
  "features/server/errors.tsx": 4,
  "features/settings/github/CreateGitHubApp.tsx": 1,
  "features/settings/github/GitHubCallback.tsx": 1,
  "features/settings/gitlab/GitLabCallback.tsx": 1,
  "nodes/nodeErrors.ts": 1,
};

function readsByFile(): Record<string, number> {
  const found: Record<string, number> = {};
  for (const [file, source] of Object.entries(RAW)) {
    const path = new URL(file, "file:///src/lib/errorReading.test.ts").pathname.replace(/^\/src\//, "");
    if (READERS.has(path)) continue;
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:'"`])\/\/.*$/gm, "$1");
    const count = [...code.matchAll(PATTERN)].length;
    if (count > 0) found[path] = count;
  }
  return found;
}

describe("how a component says the failure of a job", () => {
  it("does not hand job.error to the error block as its own detail: jobFailure(job) carries the sentence behind it", () => {
    const own: string[] = [];
    for (const [file, source] of Object.entries(RAW)) {
      const path = new URL(file, "file:///src/lib/errorReading.test.ts").pathname.replace(/^\/src\//, "");
      if (READERS.has(path)) continue;
      // `{ detail: job.error ?? ... }`: the English of whoever ran the job, in a Spanish console.
      if (/\{\s*detail:\s*[A-Za-z_.]*\b(?:job|failed|run)\.error\b/.test(source)) own.push(path);
    }
    expect(own).toEqual([]);
  });
});

describe("how a component says an API error", () => {
  const found = readsByFile();

  it("does not read error.detail or error.hint where the list does not allow it", () => {
    const more = Object.entries(found)
      .filter(([file, count]) => count > (STILL_READ[file] ?? 0))
      .map(([file, count]) => `${file}: ${String(STILL_READ[file] ?? 0)} -> ${String(count)}`);
    expect(more, "Say the error with describeError(error) from lib/errors: it puts the server's sentence in this console's language.").toEqual([]);
  });

  it("keeps the list shrinking: a file that reads fewer has its line lowered", () => {
    const fewer = Object.entries(STILL_READ)
      .filter(([file, count]) => (found[file] ?? 0) < count)
      .map(([file, count]) => `"${file}": ${String(found[file] ?? 0)}, (was ${String(count)})`);
    expect(fewer).toEqual([]);
  });
});
