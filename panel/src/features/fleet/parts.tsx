import { useQuery } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { OUTCOME_VIEW, fleetViewQuery, outcomeKind, serverNames, troubled } from "./data";
import type { FleetResource, FleetView, NodeOutcome, RowOrigin } from "./data";
import { ServerLink } from "./links";

/** One fleet view, with each server's outcome at hand by name. */
export function useFleetView(resource: FleetResource) {
  const query = useQuery(fleetViewQuery(resource));
  const outcomes = useMemo(() => new Map((query.data?.nodes ?? []).map((outcome) => [outcome.name, outcome])), [query.data]);
  return { ...query, outcomes };
}

/** How a server answered, as a state: colour, shape and word. */
export function OutcomePill({ outcome }: { outcome: NodeOutcome }) {
  const t = useT();
  const view = OUTCOME_VIEW[outcomeKind(outcome)];
  return <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />;
}

const NAME_LINK = "min-w-0 rounded-chip font-medium text-fg hover:underline hover:underline-offset-2";

/**
 * The server a row came from, named in mono and opening that server; this central is said to
 * be one. A row shown from a server's last good answer says how old it is.
 */
export function ServerCell({ origin, outcome }: { origin: RowOrigin; outcome?: NodeOutcome | undefined }) {
  const t = useT();
  const stale = outcome !== undefined && outcomeKind(outcome) === "stale";
  return (
    <span className="flex min-w-0 flex-col items-start gap-0.5">
      <ServerLink
        node={origin.local ? null : origin.node}
        path="/"
        className={NAME_LINK}
        aria-label={t("fleet.server.open", { name: origin.node })}
      >
        <Mono truncate>{origin.node}</Mono>
      </ServerLink>
      {origin.local ? <span className="text-12 text-fg-muted">{t("fleet.server.thisCentral")}</span> : null}
      {stale && outcome.fetched_at !== null && outcome.fetched_at !== undefined ? (
        <span className="inline-flex items-center gap-1 text-12 text-warn">
          <StatusGlyph state="warning" size={10} />
          <span className="text-fg-muted">{t.rich("fleet.server.staleFrom", { age: <RelativeTime key="age" value={outcome.fetched_at} /> })}</span>
        </span>
      ) : null}
    </span>
  );
}

/** One server that did not answer fully: its state, why, the fix, and its own words on request. */
function TroubledServer({ outcome }: { outcome: NodeOutcome }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const outputId = useId();
  const kind = outcomeKind(outcome);
  const view = OUTCOME_VIEW[kind];
  const why =
    kind === "unsupported" && (outcome.missing ?? []).length > 0
      ? t("fleet.partial.missing", { paths: (outcome.missing ?? []).join(", ") })
      : (outcome.message ?? t(view.label));
  return (
    <li className="flex min-w-0 flex-col gap-1 py-2 first:pt-0 last:pb-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <span className={cx("inline-flex items-center gap-1 font-medium", stateTextClass(view.state))}>
          <StatusGlyph state={view.state} size={12} />
          <span>{t(view.label)}</span>
        </span>
        <Mono className="font-medium">{outcome.name}</Mono>
        {kind === "stale" && outcome.fetched_at !== null && outcome.fetched_at !== undefined ? (
          <span className="text-12 text-fg-muted">
            {t.rich("fleet.partial.staleSince", { age: <RelativeTime key="age" value={outcome.fetched_at} /> })}
          </span>
        ) : null}
      </div>
      <p className="max-w-measure text-13 text-pretty text-fg">{why}</p>
      {outcome.hint !== null && outcome.hint !== undefined ? <p className="max-w-measure text-13 text-pretty text-fg-muted">{outcome.hint}</p> : null}
      {(outcome.warnings ?? []).length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-13 text-fg-muted">
          {(outcome.warnings ?? []).map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
      {outcome.error_verbatim !== null && outcome.error_verbatim !== undefined && outcome.error_verbatim !== "" ? (
        <div className="flex flex-col gap-1.5">
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={open}
            aria-controls={outputId}
            className="-ml-2 self-start"
            onClick={() => {
              setOpen((value) => !value);
            }}
          >
            {open ? t("fleet.partial.hideOutput", { name: outcome.name }) : t("fleet.partial.showOutput", { name: outcome.name })}
          </Button>
          <div id={outputId} hidden={!open}>
            {open ? (
              <SystemOutput label={t("fleet.partial.showOutput", { name: outcome.name })} maxHeight="max-h-40">
                {outcome.error_verbatim}
              </SystemOutput>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}

/**
 * Above a fleet view, when some server did not answer fully: how many, and for each one its
 * state, why, the fix and its own words verbatim. Its rows are never hidden - a server that
 * stopped answering shows its last good answer with its age - so the page is never blank
 * because one server is down. An older Noust is information, not a fault.
 */
export function PartialNotice({ view }: { view: FleetView | undefined }) {
  const t = useT();
  const trouble = troubled(view);
  if (view === undefined || trouble.length === 0) return null;
  const faulty = trouble.some((outcome) => {
    const kind = outcomeKind(outcome);
    return kind !== "unsupported";
  });
  return (
    <Notice tone={faulty ? "warning" : "info"} variant="banner" title={t("fleet.partial.title", { count: trouble.length, total: view.nodes.length })}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure text-pretty text-fg-muted">{t("fleet.partial.description")}</p>
        <ul aria-label={t("fleet.partial.label")} className="flex flex-col divide-y divide-border">
          {trouble.map((outcome) => (
            <TroubledServer key={outcome.name} outcome={outcome} />
          ))}
        </ul>
      </div>
    </Notice>
  );
}

const ALL = "@all";

/** "Every server" and each one by name, this central first: the server filter of every fleet view. */
export function ServerFilter({ view, value, onChange }: { view: FleetView | undefined; value: string | undefined; onChange: (server: string | undefined) => void }) {
  const t = useT();
  const names = serverNames(view);
  // A server chosen in a shared link that is not in the fleet any more still shows as chosen.
  const options = value !== undefined && !names.includes(value) ? [...names, value] : names;
  return (
    <Select
      aria-label={t("fleet.filters.server")}
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(next === ALL ? undefined : next);
      }}
      options={[{ value: ALL, label: t("fleet.filters.everyServer") }, ...options.map((name) => ({ value: name, label: name }))]}
      mono={false}
      className="min-w-40"
    />
  );
}

/** A view's state filter: "Every state" and the view's own. */
export function StateFilter({
  value,
  options,
  onChange,
}: {
  value: string | undefined;
  options: readonly { value: string; label: string }[];
  onChange: (state: string | undefined) => void;
}) {
  const t = useT();
  return (
    <Select
      aria-label={t("fleet.filters.state")}
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(next === ALL ? undefined : next);
      }}
      options={[{ value: ALL, label: t("fleet.filters.everyState") }, ...options]}
      className="min-w-40"
    />
  );
}

/** The foot of a fleet view: its terminal equivalent. */
export function ViewFooter({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3">{children}</div>;
}
