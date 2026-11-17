/**
 * The small pieces every page of the zone repeats: a container's state, a stack's, whose a thing is and the name of a container or a
 * stack as a link to its page. Each draws the same way on the summary, in the lists and in a page of one thing.
 */

import type { ReactNode } from "react";

import { OwnerMark } from "../../components/page/OwnerMark";
import { RelativeTime } from "../../components/page/RelativeTime";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { DockerOwner, DockerStack, DockerVolume } from "../../api/queries/docker";
import { ownerOf, ownerTarget, stateView, volumeCopyOf } from "./model";
import type { VolumeCopy } from "./model";

/** A container's state: the shape and colour of what it is and the word of what happened to it. */
export function ContainerPill({ kind, appearance = "inline", size = "sm" }: { kind: string; appearance?: "pill" | "inline"; size?: "sm" | "md" }) {
  const t = useT();
  const view = stateView(kind);
  // The words the glossary names are the shared ones; the rest are Docker's own.
  const label =
    view.word === "running"
      ? t("common.statusPill.running")
      : view.word === "stopped" || view.word === "terminated"
        ? t("common.statusPill.stopped")
        : view.word === "other"
          ? t("common.statusPill.unknown")
          : t(`docker.state.${view.word}`);
  return <StatusPill state={view.status} label={label} appearance={appearance} size={size} />;
}

/** The states of a stack, as the server counts them from its workers (a job that ended well is not a failure). */
const STACK_STATUS: Readonly<Record<string, Status>> = { running: "running", warning: "warning", failed: "failed", stopped: "stopped" };

/** A stack's state in the console's vocabulary: one pill, one word. */
export function StackPill({ state, appearance = "inline", size = "sm" }: { state: DockerStack["state"]; appearance?: "pill" | "inline"; size?: "sm" | "md" }) {
  const t = useT();
  const known = state === "running" || state === "warning" || state === "failed" || state === "stopped" ? state : null;
  if (known === null) return <StatusPill state="unknown" label={t("common.statusPill.unknown")} appearance={appearance} size={size} />;
  const label = known === "running" ? t("common.statusPill.running") : known === "failed" ? t("common.words.failedFeminine") : known === "stopped" ? t("docker.stack.state.stopped") : t("docker.stack.state.warning");
  return <StatusPill state={STACK_STATUS[known] ?? "unknown"} label={label} appearance={appearance} size={size} />;
}

/** Whose a thing is, with the link where it has one. */
export function OwnerCell({ owner }: { owner: DockerOwner }) {
  const mark = ownerOf(owner);
  const target = ownerTarget(owner);
  return <OwnerMark kind={mark.kind} subject={mark.subject} {...(target !== undefined ? { target } : {})} />;
}

/** A container's name, a system value, as a link to its page. */
export function ContainerLink({ name }: { name: string }) {
  return (
    <TextLink to="/server/docker/containers/$ref" params={{ ref: name }} variant="row" size="ui" className="inline-block max-w-full truncate align-bottom">
      <Mono>{name}</Mono>
    </TextLink>
  );
}

/** A stack's name as a link to its page, with the stack's own icon. */
export function StackLink({ name }: { name: string }) {
  return (
    <TextLink to="/server/docker/stacks/$name" params={{ name }} variant="row" size="ui" className="inline-flex max-w-full items-center gap-1.5">
      <OBJECT_ICONS.compose aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
      <Mono truncate>{name}</Mono>
    </TextLink>
  );
}

/** The order the copy column sorts in: what has no copy first, the thing to look at. */
export const COPY_RANK: Readonly<Record<VolumeCopy["state"], number>> = { none: 0, copied: 1, scheduled: 2, unknown: 3 };

/**
 * Whether a volume is in a copy, in words and with a shape where it asks for a look: a volume with none, and one with a copy that nothing
 * takes again, wear a warning; one a schedule takes is its words alone. A node that does not say (a Noust of 4.2) leaves the cell empty,
 * which is not the same as "no copy".
 */
export function CopyCell({ volume }: { volume: Pick<DockerVolume, "backup"> }) {
  const t = useT();
  const copy = volumeCopyOf(volume);
  if (copy.state === "unknown") return <EmptyCell reason={t("docker.volumes.copy.unknown")} />;
  const warn = (words: ReactNode) => (
    <span className="flex items-center gap-1.5 font-medium text-fg">
      <StatusGlyph state="warning" size={12} className="shrink-0 text-warn" />
      <span>{words}</span>
    </span>
  );
  if (copy.state === "none") return warn(t("docker.volumes.copy.none"));
  const when = copy.lastAt === null ? null : <RelativeTime key="when" value={copy.lastAt} />;
  if (copy.state === "copied") return warn(when === null ? t("docker.volumes.copy.none") : t.rich("docker.volumes.copy.copiedOnly", { when }));
  return <span>{when === null ? t("docker.volumes.copy.scheduledNone") : t.rich("docker.volumes.copy.scheduled", { when })}</span>;
}
