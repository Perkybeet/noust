import type { ReactNode } from "react";

import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { Badge } from "../ui/Badge";
import { Mono } from "../ui/Mono";

/** Where the model runs, as the server says it. */
export type ModelLocation = "machine" | "network" | "eu" | "outside" | "unknown";

const LOCATIONS: readonly ModelLocation[] = ["machine", "network", "eu", "outside", "unknown"];

/**
 * Where the model is, in words. A place the server names that this console does not know is "not stated": it says nothing
 * it cannot stand behind.
 *
 * @param t - The translator.
 * @param location - The `location` the server sent, which may be missing.
 * @returns The words for it.
 */
export function locationWord(t: T, location: string | null | undefined): string {
  const known = LOCATIONS.find((candidate) => candidate === location) ?? "unknown";
  return t(`assistant.location.${known}`);
}

export interface AiMarkProps {
  /** The model that wrote the answer, as the provider names it. */
  model: string;
  /** Where it runs, already in words (`locationWord`). */
  where: string;
  /** When it was written, already formatted. */
  at?: ReactNode;
}

/**
 * The mark that says an answer was written by an AI: fixed and small on every reply (the panel's title says it too), with the
 * model and where it runs, so a person can weigh what they read. Achromatic, because it is a fact and not a state; for a screen
 * reader it is one sentence.
 */
export function AiMark({ model, where, at }: AiMarkProps) {
  const t = useT();
  return (
    <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 text-12 text-fg-muted">
      <Badge>
        <span aria-hidden="true">{t("assistant.mark.ai")}</span>
        <span className="sr-only">{t("assistant.mark.named", { model })}</span>
      </Badge>
      <Mono tone="muted" truncate className="max-w-40">
        {model}
      </Mono>
      <span>{where}</span>
      {at !== undefined ? <span className="ml-auto tabular-nums">{at}</span> : null}
    </p>
  );
}
