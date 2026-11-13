import { Check } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { Disclosure } from "../ui/Disclosure";
import { InlineMessage } from "../ui/InlineMessage";
import { Spinner } from "../ui/Spinner";
import { StatusGlyph } from "../ui/StatusPill";

/**
 * What the assistant read while it investigated, one step a line: the proof of what an answer rests on. `what` is the sentence
 * Noust wrote of the step ("Read the log of deployment 14"), never the model's.
 */
export interface ReadStep {
  id: string;
  what: ReactNode;
  /** How much was read, already in words ("120 lines"). */
  size?: string;
  /** How many values that looked like secrets were hidden before they left the server. */
  masked?: number;
  /** What it read was written by other people. */
  outside?: boolean;
  state: "done" | "running" | "failed" | "refused" | "proposed";
  /** Why it failed or was refused, in the sentence Noust gave. */
  error?: ReactNode;
  /** The server a step ran on, said only on a fleet. */
  server?: string;
}

function Glyph({ state }: { state: ReadStep["state"] }) {
  const t = useT();
  switch (state) {
    case "running":
      return <Spinner size={14} className="text-warn" label={t("assistant.steps.readingStep")} />;
    case "failed":
    case "refused":
      return <StatusGlyph state="failed" className="text-fail" />;
    case "proposed":
      return <StatusGlyph state="queued" className="text-warn" />;
    case "done":
      return <Check aria-hidden="true" className="size-icon-sm text-fg-faint" />;
  }
}

function stateWord(t: ReturnType<typeof useT>, state: ReadStep["state"]): string | null {
  if (state === "failed") return t("assistant.steps.failed");
  if (state === "refused") return t("assistant.steps.refused");
  if (state === "proposed") return t("assistant.steps.proposed");
  return null;
}

function Line({ step }: { step: ReadStep }) {
  const t = useT();
  const word = stateWord(t, step.state);
  return (
    <li className="flex min-w-0 flex-col gap-0.5 py-1.5" data-step-state={step.state}>
      <div className="flex min-w-0 items-start gap-2 text-13 leading-5">
        <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
          <Glyph state={step.state} />
        </span>
        <span className="min-w-0 flex-1 text-pretty text-fg">
          {step.what}
          {word !== null ? <span className="sr-only">{`. ${word}`}</span> : null}
        </span>
        {step.size !== undefined ? <span className="shrink-0 text-12 text-fg-muted tabular-nums">{step.size}</span> : null}
      </div>
      {step.server !== undefined ? <p className="pl-6 text-12 text-fg-muted">{t("assistant.steps.server", { server: step.server })}</p> : null}
      {step.error !== undefined ? <p className="pl-6 text-12 text-pretty text-fg-muted">{step.error}</p> : null}
      {step.outside === true ? <p className="pl-6 text-12 text-fg-muted">{t("assistant.steps.outside")}</p> : null}
      {step.masked !== undefined && step.masked > 0 ? (
        <div className="pl-6">
          <InlineMessage tone="info">{t("assistant.steps.masked", { count: step.masked })}</InlineMessage>
        </div>
      ) : null}
    </li>
  );
}

export interface ReadStepsProps {
  steps: readonly ReadStep[];
  /** The assistant is reading now: the list is open and says what it is doing. When it ends it folds under the answer. */
  running?: boolean;
  defaultOpen?: boolean;
}

/**
 * The list of what the assistant read. While it runs it is open and announced as a status, one sentence a step; when it ends it
 * folds under the answer as "Read 5 things" and stays one press away. Reading is neutral (a check is not the state of anything);
 * a read that failed carries the failed glyph and a word for a screen reader; what was hidden before it left is said under the
 * line that read it.
 */
export function ReadSteps({ steps, running = false, defaultOpen = false }: ReadStepsProps) {
  const t = useT();
  if (steps.length === 0) return null;
  const list = (
    <ul className="m-0 flex list-none flex-col divide-y divide-border p-0" aria-label={t("assistant.steps.list")}>
      {steps.map((step) => (
        <Line key={step.id} step={step} />
      ))}
    </ul>
  );
  if (running) {
    return (
      <div className="flex flex-col gap-1" role="status" aria-live="polite">
        <p className="text-12 font-medium text-fg-muted">{t("assistant.steps.reading")}</p>
        {list}
      </div>
    );
  }
  return (
    <Disclosure label={t("assistant.steps.read", { count: steps.length })} {...(defaultOpen ? { defaultOpen: true } : {})}>
      {list}
    </Disclosure>
  );
}
