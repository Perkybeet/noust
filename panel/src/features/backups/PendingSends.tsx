/**
 * A copy that was made and has not reached a destination (item 201), said where copies are listed. It is a state of its own, and
 * the one of work in progress: the copy is safe on this server and the send is tried again by itself, so it is neither a success nor
 * a failure. The pill says to which destination (or how many), the line under it when it is tried again, and the "?" opens what
 * each destination said, verbatim, how many times it was tried and when it first failed. There is nothing to press here because
 * the server has no route to send it now: the pass that sends it again is a timer, and a command for whoever has just mended the
 * network.
 */

import { useMemo } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { useNow } from "../../components/page/clock";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { HelpPopover } from "../../components/ui/HelpPopover";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import type { PendingSend } from "../../lib/pendingSends";

/** When the soonest of them is tried again: said in the future when it is, and as the next pass when it is already due. */
function NextTry({ sends }: { sends: readonly PendingSend[] }) {
  const t = useT();
  const soonest = sends.find((send) => send.nextAttemptAt !== null)?.nextAttemptAt ?? null;
  const when = useMemo(() => parseTimestamp(soonest), [soonest]);
  const now = useNow(() => 30_000);
  if (soonest === null) return null;
  const due = when !== null && when.getTime() <= now;
  return <span className="text-12 text-fg-muted">{due ? t("backups.pending.dueNow") : t.rich("backups.pending.next", { when: <RelativeTime key="when" value={soonest} /> })}</span>;
}

function Detail({ send }: { send: PendingSend }) {
  const t = useT();
  return (
    <div data-pending-destination={send.destination} className="flex min-w-0 flex-col gap-1.5">
      <Mono tone="default" className="font-medium">
        {send.destination}
      </Mono>
      {send.reason !== "" ? (
        <SystemOutput label={t("backups.pending.said", { destination: send.destination })} className="rounded-control bg-bg-sunken px-2 py-1.5">
          {send.reason}
        </SystemOutput>
      ) : null}
      <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-12 text-fg-muted">
        {send.since !== null ? <span>{t.rich("backups.pending.since", { when: <RelativeTime key="when" value={send.since} /> })}</span> : null}
        <span>{send.attempts === 0 ? t("backups.pending.notRetried") : t("backups.pending.attempts", { count: send.attempts })}</span>
      </p>
      {send.permanent ? <p className="text-13 text-pretty text-fg">{t("backups.pending.permanent")}</p> : null}
    </div>
  );
}

export interface PendingSendsProps {
  /** The destinations the copy is owed to, from `pendingSendsOf`; nothing is drawn for none. */
  sends: readonly PendingSend[];
}

/** The state of a copy that waits to be sent, in a list or in the detail of one. */
export function PendingSends({ sends }: PendingSendsProps) {
  const t = useT();
  const first = sends[0];
  if (first === undefined) return null;
  return (
    <span data-slot="pending-sends" className="flex min-w-0 flex-col gap-0.5">
      <span className="inline-flex items-center gap-1">
        <StatusPill appearance="inline" size="sm" state="deploying" label={sends.length === 1 ? t("backups.pending.state", { destination: first.destination }) : t("backups.pending.stateMany", { count: sends.length })} />
        <HelpPopover label={t("backups.pending.helpLabel")} title={t("backups.pending.helpTitle")}>
          <div className="flex min-w-0 flex-col gap-3">
            <p className="text-13 text-pretty text-fg-muted">{t("backups.pending.explain")}</p>
            {sends.map((send) => (
              <Detail key={send.destination} send={send} />
            ))}
          </div>
        </HelpPopover>
      </span>
      <NextTry sends={sends} />
    </span>
  );
}

/**
 * The same, as the cell of a column that exists because some copy of the list waits: a copy that is everywhere says so with a dash and its
 * reason for a screen reader, instead of leaving a card with a label and nothing after it.
 */
export function PendingSendsCell({ sends }: PendingSendsProps) {
  const t = useT();
  return sends.length === 0 ? <EmptyCell reason={t("backups.pending.none")} /> : <PendingSends sends={sends} />;
}
