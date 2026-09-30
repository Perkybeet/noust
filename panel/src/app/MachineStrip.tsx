import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { sessionQuery } from "../api/queries/auth";
import { machineQuery } from "../api/queries/system";
import type { Machine } from "../api/queries/system";
import { Mono } from "../components/ui/Mono";
import { Meter } from "../components/ui/Progress";
import { Skeleton } from "../components/ui/Skeleton";
import { StatusGlyph, StatusPill } from "../components/ui/StatusPill";
import { Tooltip } from "../components/ui/Tooltip";
import { countsOf, fleetViewQuery, outcomeKind } from "../features/fleet/data";
import { useT } from "../i18n";
import type { T } from "../i18n";
import { cx } from "../lib/cx";
import { formatDuration, formatLoad } from "../lib/format";
import { useHasFleet } from "../nodes/servers";
import { useConsoleContext, useNode } from "../nodes/useNode";
import { useStreamStatus } from "../realtime/events";

/** The one sentence that says what the unit tally means, for the tooltip and the link's accessible name alike. */
export function unitTallySummary(units: Machine["units"], t: T): string {
  return t("shell.machine.unitsSummary", { running: units.running, failed: units.failed, stopped: units.stopped });
}

/** The recent one-minute load as a line, scaled to its own peak (at least 1). */
export function sparklinePath(samples: readonly number[], width: number, height: number): string {
  if (samples.length === 0) return "";
  const peak = Math.max(1, ...samples);
  const step = samples.length > 1 ? width / (samples.length - 1) : 0;
  return samples
    .map((value, index) => {
      const x = samples.length > 1 ? index * step : width;
      const y = height - (Math.max(0, value) / peak) * (height - 2) - 1;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function Divider({ className }: { className?: string | undefined }) {
  return <span aria-hidden="true" className={cx("h-6 w-px shrink-0 bg-border", className)} />;
}

const STRIP_LINK = "rounded-control px-1.5 py-1 hover:bg-surface-hover";

function Load({ machine }: { machine: Machine }) {
  const t = useT();
  const [one, five, fifteen] = machine.load;
  return (
    <div
      className="flex items-center gap-2"
      title={t("shell.machine.loadAverage", { one: formatLoad(one, t.locale), five: formatLoad(five, t.locale), fifteen: formatLoad(fifteen, t.locale) })}
    >
      <span className="text-12 text-fg-muted">{t("shell.machine.load")}</span>{" "}
      <svg width="48" height="18" viewBox="0 0 48 18" aria-hidden="true" className="shrink-0 text-fg-muted">
        <path d={sparklinePath(machine.load_history, 48, 18)} fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <Mono className="text-13">{formatLoad(one, t.locale)}</Mono>{" "}
      <span className="sr-only">
        {t("shell.machine.loadDetail", { five: formatLoad(five, t.locale), fifteen: formatLoad(fifteen, t.locale) })}
      </span>
    </div>
  );
}

/**
 * Noust's services on the machine, in words: "15 running, 0 failed, 1 stopped". The words give
 * way to the glyphs on a narrower strip; the sentence stays the link's name and its tooltip.
 */
function UnitTally({ units }: { units: Machine["units"] }) {
  const t = useT();
  const parts = [
    { state: "running" as const, count: units.running, word: t("shell.machine.running", { count: units.running }), tone: units.running > 0 ? "text-ok" : "text-fg-muted" },
    { state: "failed" as const, count: units.failed, word: t("shell.machine.failed", { count: units.failed }), tone: units.failed > 0 ? "text-fail" : "text-fg-muted" },
    { state: "stopped" as const, count: units.stopped, word: t("shell.machine.stopped", { count: units.stopped }), tone: "text-idle" },
  ];
  // One sentence, not two: the tooltip (hover and keyboard focus) and the accessible name say
  // the same thing, so the symbols next to it are never the only place the meaning lives.
  const summary = unitTallySummary(units, t);
  return (
    <Tooltip content={summary}>
      <Link to="/server/services" aria-label={summary} className={cx("flex items-center gap-3", STRIP_LINK)}>
        <span aria-hidden="true" className="text-12 text-fg-muted">
          {t("shell.machine.units")}
        </span>
        {parts.map((part) => (
          <span key={part.state} aria-hidden="true" className={cx("inline-flex items-center gap-1 whitespace-nowrap", part.tone)}>
            <StatusGlyph state={part.state} size={10} />
            <span className={cx("text-13 tabular-nums", part.count > 0 && part.state === "failed" ? "font-medium" : "text-fg")}>{part.word}</span>
          </span>
        ))}
      </Link>
    </Tooltip>
  );
}

/**
 * One machine's readout: how long it has been up, load, CPU, memory and disk, and how many of
 * Noust's services are running, failed or stopped. Painted from GET /api/system/machine, then
 * kept current by the `machine` event every five seconds. Deliberately not a live region:
 * numbers that change every five seconds are not news.
 *
 * On a lone server it starts with the machine's name; on a fleet the selector beside it names
 * the server (or "This central") already, and a second name would only repeat it.
 */
function ServerStrip({ className, showName }: { className?: string | undefined; showName: boolean }) {
  const t = useT();
  const stream = useStreamStatus();
  // While the stream is down (a restarting panel, a proxy that buffers it) the strip polls,
  // so it never shows numbers from minutes ago as if they were current.
  const { data: machine, isError } = useQuery({ ...machineQuery(), refetchInterval: stream === "live" ? false : 15_000 });
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  // The session is this server's: on a node, its name stands in until its machine answers.
  const { node } = useNode();
  const name = machine?.hostname ?? node ?? hostname;

  return (
    <div role="group" aria-label={t("shell.machine.landmark")} className={cx("@container min-w-0", className)}>
      <div className="flex items-center gap-2 @min-[26rem]:gap-4">
        {showName ? (
          <Link to="/server" className={cx("flex min-w-0 items-baseline gap-2", STRIP_LINK)}>
            {name !== undefined ? (
              <Mono truncate className="text-13 font-medium">
                {name}
              </Mono>
            ) : (
              <Skeleton className="h-3.5 w-28" />
            )}
            {machine ? " " : null}
            {machine ? (
              <span className="hidden shrink-0 text-12 text-fg-muted tabular-nums @min-[62rem]:inline">
                {t("shell.machine.upFor", { duration: formatDuration(machine.uptime_s) })}
              </span>
            ) : null}
          </Link>
        ) : machine ? (
          // The selector beside the strip names the server already: only how long it has been up.
          <span className="hidden shrink-0 text-12 text-fg-muted tabular-nums @min-[62rem]:inline">
            {t("shell.machine.upFor", { duration: formatDuration(machine.uptime_s) })}
          </span>
        ) : null}

        {machine ? (
          <>
            <div className="hidden shrink-0 items-center gap-4 @min-[36rem]:flex">
              <Divider className={showName ? undefined : "hidden @min-[62rem]:block"} />
              <UnitTally units={machine.units} />
            </div>
            {/* Meters after the words: the Overview and Server show these readings in full. */}
            <div className="hidden shrink-0 items-center gap-4 @min-[43rem]:flex">
              <Divider />
              <Meter size="sm" label={t("shell.machine.cpu")} value={machine.cpu_percent} className="w-24" />
              <Meter size="sm" label={t("shell.machine.memory")} value={machine.memory.percent} className="w-24" />
              <Meter size="sm" label={t("shell.machine.disk")} value={machine.disk.percent} className="w-24" />
            </div>
            <div className="hidden shrink-0 items-center gap-4 @min-[72rem]:flex">
              <Divider />
              <Load machine={machine} />
            </div>
            {machine.units.failed > 0 ? (
              <Link
                to="/server/services"
                aria-label={t("shell.machine.failedUnits", { count: machine.units.failed })}
                className={cx("inline-flex shrink-0 items-center gap-1 text-12 font-medium text-fail @min-[36rem]:hidden", STRIP_LINK)}
              >
                <StatusGlyph state="failed" size={10} />
                <span aria-hidden="true" className="tabular-nums">
                  {t("shell.machine.failedUnits", { count: machine.units.failed })}
                </span>
              </Link>
            ) : null}
          </>
        ) : isError ? (
          <span className="hidden truncate text-12 text-fg-muted @min-[26rem]:inline">{t("shell.machine.unavailable")}</span>
        ) : (
          <div aria-hidden="true" className="hidden items-center gap-4 @min-[30rem]:flex">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-6 w-24" />
          </div>
        )}

        {stream === "reconnecting" ? (
          <StatusPill state="deploying" label={t("shell.machine.reconnecting")} appearance="inline" size="sm" className="shrink-0" />
        ) : null}
      </div>
    </div>
  );
}

/**
 * The fleet's readout, on its pages: how many servers there are, how many answer, how many do
 * not, and what needs attention anywhere - from the central's one summary, the same answer the
 * Fleet's summary shows, never a request per server.
 */
function FleetStrip({ className }: { className?: string | undefined }) {
  const t = useT();
  const { data: view, isError } = useQuery(fleetViewQuery("summary"));
  const total = view?.nodes.length ?? 0;
  const down = view?.nodes.filter((outcome) => {
    const kind = outcomeKind(outcome);
    return kind === "unreachable" || kind === "stale" || kind === "error";
  }).length ?? 0;
  const failedApps = (view?.items ?? []).reduce((sum, row) => sum + (countsOf(row, "apps")?.failed ?? 0), 0);
  const answering = total - down;
  const summary = view === undefined ? "" : t("shell.fleetStrip.summary", { total, answering, down, failed: failedApps });

  return (
    <div role="group" aria-label={t("shell.fleetStrip.landmark")} className={cx("@container min-w-0 overflow-hidden", className)}>
      {view !== undefined ? (
        <Tooltip content={summary}>
          <Link to="/fleet" aria-label={summary} className={cx("flex min-w-0 items-center gap-3", STRIP_LINK)}>
            <span aria-hidden="true" className="hidden shrink-0 text-13 font-medium text-fg tabular-nums @min-[20rem]:inline">
              {t("shell.fleetStrip.servers", { count: total })}
            </span>
            <span aria-hidden="true" className="hidden items-center gap-1 text-13 whitespace-nowrap text-ok @min-[24rem]:inline-flex">
              <StatusGlyph state="running" size={10} />
              <span className="text-fg tabular-nums">{t("shell.fleetStrip.answering", { count: answering })}</span>
            </span>
            {down > 0 ? (
              <span aria-hidden="true" className="inline-flex min-w-0 items-center gap-1 truncate text-13 font-medium whitespace-nowrap text-fail">
                <StatusGlyph state="failed" size={10} />
                <span className="tabular-nums @max-[24rem]:hidden">{t("shell.fleetStrip.down", { count: down })}</span>
                <span className="tabular-nums @min-[24rem]:hidden">{t("shell.fleetStrip.downShort", { count: down })}</span>
              </span>
            ) : null}
            {failedApps > 0 ? (
              <span aria-hidden="true" className="hidden items-center gap-1 text-13 font-medium whitespace-nowrap text-fail @min-[36rem]:inline-flex">
                <StatusGlyph state="failed" size={10} />
                <span className="tabular-nums">{t("shell.fleetStrip.failedApps", { count: failedApps })}</span>
              </span>
            ) : null}
          </Link>
        </Tooltip>
      ) : isError ? (
        <span className="truncate text-12 text-fg-muted">{t("shell.fleetStrip.unavailable")}</span>
      ) : (
        <Skeleton className="h-6 w-48" />
      )}
    </div>
  );
}

/**
 * The instrument readout under the top bar's selector: the machine on screen, or, on the
 * fleet's pages, the fleet at once. It sizes itself by its own width (a container query), not
 * the viewport's: readings drop out in order of importance, load first, a failure count last.
 */
export function MachineStrip({ className }: { className?: string }) {
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  if (context.kind === "fleet") return <FleetStrip className={className} />;
  return <ServerStrip className={className} showName={!hasFleet} />;
}
