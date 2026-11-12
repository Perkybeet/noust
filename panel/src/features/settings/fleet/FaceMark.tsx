import type { ReactNode } from "react";

import { Badge } from "../../../components/ui/Badge";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { Cell } from "./data";
import { faceOf, isWaiting } from "./faces";
import type { Face } from "./faces";

/**
 * The state each face is drawn with. `null` is well, and is plain text with no colour: only what is not well is
 * tinted. Independent, a ceiling that does not let the central change settings, an older Noust and a route that
 * matches nothing are decisions or facts, not failures: they are grey, with their own shape and their word, never
 * red. A server that does not answer is said as the list of servers says it.
 */
const STATE: Record<Face, Status | null> = {
  follows: null,
  exception: null,
  changedHere: "warning",
  pending: "queued",
  off: "failed",
  independent: "stopped",
  refused: "stopped",
  unsupported: "stopped",
  notSent: "stopped",
  none: null,
  unknown: "unknown",
};

/** The face of one cell, with its word and its shape: one thing at a time. */
export function FaceMark({ face }: { face: Face }) {
  const t = useT();
  const word = t(`fleetSettings.face.${face}`);
  const state = STATE[face];
  if (state !== null) return <StatusPill state={state} label={word} appearance="inline" size="sm" />;
  if (face === "exception") return <Badge>{word}</Badge>;
  return <span className="text-13 text-fg">{word}</span>;
}

/** A server's name as a value: in mono, with "central" beside the one that is. */
export function ServerName({ name, central = false }: { name: string; central?: boolean }) {
  const t = useT();
  return (
    <span className="inline-flex min-w-0 items-center gap-2">
      <Mono tone="default">{name}</Mono>
      {central ? <Badge>{t("fleetSettings.panel.central")}</Badge> : null}
    </span>
  );
}

/** The sentence that says why a server holds a setting the way it does, and what it needs. */
export function whyText(t: ReturnType<typeof useT>, cell: Cell): ReactNode {
  const face = faceOf(cell);
  if (face === "off") return isWaiting(cell) ? t("fleetSettings.why.offWaiting") : t("fleetSettings.why.off");
  return t(`fleetSettings.why.${face}`);
}
