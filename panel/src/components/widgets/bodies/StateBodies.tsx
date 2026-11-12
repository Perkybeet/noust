/**
 * The widgets that say how things stand: what needs the operator, the six figures of a server, the work
 * in progress, what happened lately and the alerts that are firing. Each reads the queries the rest of the
 * console reads (so a panel full of them asks the server no more than the page that came before), and
 * draws the room it has: a count in a tile, a line each in a band, with more beside each line in a taller one.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { overviewQuery } from "../../../api/queries/overview";
import { activeJobsQuery } from "../../../api/queries/jobs";
import { fleetActiveQuery, operationsActiveQuery } from "../../../api/queries/operations";
import type { Job } from "../../../api/queries/jobs";
import type { Operation } from "../../../api/queries/operations";
import { openBell } from "../../../features/inbox/bell";
import { describeActor, jobActionLabel } from "../../../features/activity/data";
import { objectOf, textOf } from "../../../features/fleet/data";
import { AttentionActionButton } from "../../../features/inbox/AttentionActionButton";
import { attentionIcon } from "../../../features/inbox/attentionIcons";
import { useRowPress } from "../../../features/inbox/useRowPress";
import { attentionAction, attentionTarget } from "../../../features/overview/attentionTarget";
import { itemTitle, originLabel, reasonDetail, reasonText } from "../../../features/overview/attentionWords";
import { sinceOf } from "../../../features/overview/homeData";
import type { ActivityLine, HomeAttention } from "../../../features/overview/homeData";
import { useScopedHome } from "../../../features/overview/useHome";
import { figuresOf, fleetWorkOf, workOf } from "../../../features/overview/widgetData";
import type { Work, WorkLine } from "../../../features/overview/widgetData";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { RelativeTime } from "../../page/RelativeTime";
import { deployStatus } from "../../page/status";
import { Mono } from "../../ui/Mono";
import { Progress } from "../../ui/Progress";
import { serverSentence } from "../../ui/ServerText";
import { stateTextClass } from "../../ui/StatusPill";
import { textLinkClassName } from "../../ui/TextLink";
import { Big, Glyph, StateLine, WidgetPending } from "../parts";
import type { WidgetBodyProps } from "../types";

// ---------------------------------------------------------------------------------------
// Needs attention

/** The most lines drawn: a tall band shows this many, a short one the first three. */
const ATTENTION_ROWS = 8;

/**
 * What the line is about: the name. A line whose button leads to the same screen has no second link to it (the line itself is
 * pressed, `useRowPress`); one whose button does something else (taking an alert) names the screen with the name, which is
 * stretched over the line, so a press anywhere on it opens the screen that deals with it (item 161).
 */
function subjectOf(t: T, entry: HomeAttention, link: boolean) {
  const title = itemTitle(t, entry.item);
  const name = title.mono ? <Mono>{title.text}</Mono> : title.translate ? title.text : <span translate="no">{title.text}</span>;
  const target = attentionTarget(entry.item);
  if (target === null || !link) return name;
  const node = entry.server === null ? undefined : entry.server.node;
  return (
    <Link
      to={target.to as never}
      {...(target.params ? { params: target.params as never } : {})}
      {...(target.search || node !== undefined ? { search: { ...target.search, node } as never } : {})}
      className="rounded-chip after:absolute after:inset-0 group-hover:underline"
    >
      {name}
    </Link>
  );
}

/**
 * One line of what needs attention. Wide enough, it says all of it: the state in a word, what it is about, the server on a
 * fleet, where it comes from, what is wrong, since when and the one thing to press. A phone's line keeps what it can be
 * pressed by: the glyph of its state, the name, where it comes from and the button; the sentence is one press away.
 */
export function AttentionRow({ t, entry, showServer }: { t: T; entry: HomeAttention; showServer: boolean }) {
  const { item, server } = entry;
  const fail = item.severity === "fail";
  const state = fail ? "failed" : "warning";
  // Every reason is a sentence of the line. What the system said is shown as it came, after the sentences that say what it
  // means (Principle 5): a sentence of Noust's own in the text's face and in this console's language, what a tool printed in mono.
  const sentences = item.reasons.map((reason) => reasonText(t, reason)).join(", ");
  const detail = item.reasons.map((reason) => reasonDetail(t, reason)).find((candidate) => candidate !== null) ?? null;
  const since = sinceOf(item);
  const node = server === null ? undefined : server.node;
  const target = attentionTarget(item);
  const buttonLeads = attentionAction(item)?.mode === "open";
  const press = useRowPress(buttonLeads ? target : null, { node });
  return (
    <li
      data-origin={item.origin ?? "state"}
      {...(press !== undefined ? { onClick: press } : {})}
      className={cx("group relative flex h-9 shrink-0 items-center gap-2.5 border-b border-border last:border-b-0", target !== null && "hover:bg-surface-hover", press !== undefined && "cursor-pointer")}
    >
      <Glyph state={state} />
      <span className={cx("hidden w-12 shrink-0 text-12 font-semibold w-wide:block", stateTextClass(state))}>{fail ? t("widgets.attention.failure") : t("common.statusPill.warning")}</span>
      <span className="sr-only w-wide:hidden">{fail ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}</span>
      <span className="min-w-0 flex-1 truncate text-13 font-medium text-fg w-wide:w-44 w-wide:flex-none w-xwide:w-56">{subjectOf(t, entry, !buttonLeads)}</span>
      {showServer && server !== null ? (
        <span translate="no" className="hidden shrink-0 rounded-chip bg-bg-sunken px-1.5 text-12 text-fg-muted w-xwide:inline">
          {server.name}
        </span>
      ) : null}
      <span data-slot="origin" className="shrink-0 text-12 text-fg-faint w-wide:w-16">
        {originLabel(t, item.origin)}
      </span>
      <span className="hidden min-w-0 flex-1 truncate text-13 text-fg-muted w-wide:block">
        {sentences}
        {detail !== null ? (
          <>
            {": "}
            {detail.sentence ? detail.text : <Mono tone="muted">{detail.text}</Mono>}
          </>
        ) : null}
      </span>
      {since !== null ? <RelativeTime value={since} className="hidden shrink-0 text-12 text-fg-faint w-xwide:inline" /> : null}
      <span className="relative z-10 shrink-0">
        <AttentionActionButton item={item} node={node} appearance="link" iconFor={attentionIcon} compact />
      </span>
    </li>
  );
}

/** What fails or is about to, a line each, the worst first; when nothing does, one calm line. */
export function AttentionBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const home = useScopedHome(scope);
  const title = t("widgets.attention.title");
  if (home.entries === undefined) return <WidgetPending title={title} failed={home.error !== null} onRetry={home.retry} />;
  if (home.entries.length === 0) {
    return (
      <p className="flex items-start gap-2 text-13 text-fg-muted">
        <span className="mt-0.5 flex h-4 shrink-0 items-center">
          <Glyph state="running" size={14} />
        </span>
        <span>
          <span className="font-medium text-fg">{t("overview.attention.emptyTitle")}</span> {t("widgets.attention.checked")}
        </span>
      </p>
    );
  }
  return (
    <ul className="flex h-full flex-col overflow-hidden [&>li:nth-child(n+4)]:hidden w-tall:[&>li:nth-child(n+4)]:flex">
      {home.entries.slice(0, ATTENTION_ROWS).map((entry) => (
        <AttentionRow key={entry.key} t={t} entry={entry} showServer={home.several} />
      ))}
    </ul>
  );
}

/**
 * "See all 7": what needs attention is the inbox's list, and on a server the head of the widget opens the bell. The bell is the
 * selected server's, so on the fleet's panel, which gathers every server's list, it leads to the servers instead, where each says
 * what is wrong with it.
 */
export function AttentionLink({ scope }: Pick<WidgetBodyProps, "scope">) {
  const t = useT();
  const home = useScopedHome(scope);
  const count = Math.max(home.total, home.entries?.length ?? 0);
  if (count === 0) return null;
  const className = cx(textLinkClassName("ui"), "shrink-0 cursor-pointer px-1 text-12");
  if (scope === "fleet") {
    return (
      <Link to="/fleet/servers" className={className}>
        {t("widgets.attention.seeAll", { count })}
      </Link>
    );
  }
  return (
    <button type="button" onClick={openBell} className={className}>
      {t("widgets.attention.seeAll", { count })}
    </button>
  );
}

// ---------------------------------------------------------------------------------------
// The six figures

/** The six figures of the server: each is a link to where the whole of it is. */
export function FiguresBody() {
  const t = useT();
  const overview = useQuery(overviewQuery());
  if (overview.data === undefined) return <WidgetPending title={t("widgets.figures.title")} failed={overview.isError} onRetry={() => void overview.refetch()} />;
  const figures = figuresOf(overview.data, t);
  return (
    <ul className="grid h-full grid-cols-3 content-start gap-x-4 gap-y-2.5 w-tall:gap-y-4 w-xwide:grid-cols-6 w-xwide:content-center">
      {figures.map((figure) => (
        <li key={figure.id} className="min-w-0">
          <Link to={figure.to as never} title={figure.label} className="group block min-w-0 rounded-control">
            <span className="block truncate text-12 text-fg-muted group-hover:text-fg">{figure.label}</span>
            <span className="flex items-center gap-1.5">
              {figure.state !== null ? <Glyph state={figure.state} /> : null}
              <span className="title text-16 text-fg tabular-nums w-wide:text-18 w-xwide:text-24">{figure.value}</span>
            </span>
            {figure.context !== null ? <span className="hidden truncate text-12 text-fg-muted w-tall:block w-xwide:block">{figure.context}</span> : null}
          </Link>
        </li>
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------
// Work in progress

/** The work of this server, or of the whole fleet. */
function useWork(scope: WidgetBodyProps["scope"], t: T): { work: Work | undefined; unsupported: string[]; failed: boolean; retry: () => void } {
  const jobs = useQuery({ ...activeJobsQuery(), enabled: scope === "server" });
  const operations = useQuery({ ...operationsActiveQuery(), enabled: scope === "server" });
  const fleet = useQuery({ ...fleetActiveQuery(), enabled: scope === "fleet" });
  if (scope === "fleet") {
    if (fleet.data === undefined) return { work: undefined, unsupported: [], failed: fleet.isError, retry: () => void fleet.refetch() };
    const rows = fleet.data.items.map((row) => ({
      server: textOf(row["node"]) ?? "",
      jobs: Array.isArray(row["jobs"]) ? (row["jobs"] as Job[]) : [],
      operations: Array.isArray(row["operations"]) ? (row["operations"] as Operation[]) : [],
    }));
    const unsupported = fleet.data.items.filter((row) => textOf(objectOf(row)?.["status"]) === "unsupported").map((row) => textOf(row["node"]) ?? "");
    return { work: fleetWorkOf(rows, t), unsupported, failed: false, retry: () => void fleet.refetch() };
  }
  // A server older than this console has no route for operations: its jobs are what it has to say.
  if (jobs.data === undefined) return { work: undefined, unsupported: [], failed: jobs.isError, retry: () => void jobs.refetch() };
  return { work: workOf(jobs.data.jobs, operations.data?.operations ?? [], t), unsupported: [], failed: false, retry: () => void jobs.refetch() };
}

function WorkRow({ line, fleet }: { line: WorkLine; fleet: boolean }) {
  return (
    <li className="min-w-0">
      <div className="flex items-center gap-2">
        <Glyph state={line.state} size={10} />
        <span className="min-w-0 flex-1 truncate text-13 font-medium text-fg">{line.title}</span>
        {fleet && line.server !== null ? (
          <span translate="no" className="text-12 text-fg-muted">
            {line.server}
          </span>
        ) : null}
      </div>
      {line.step !== null ? <p className="truncate pl-4.5 text-12 text-fg-muted">{line.step}</p> : null}
      {line.percent !== null ? <Progress value={line.percent} label={line.title} showValue={false} className="mt-1 pl-4.5" /> : null}
    </li>
  );
}

/** What is being done now and what waits: a count and the first thing in a tile, every line in a band. */
export function WorkBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const { work, unsupported, failed, retry } = useWork(scope, t);
  if (work === undefined) return <WidgetPending title={t("widgets.work.title")} failed={failed} onRetry={retry} />;
  if (work.lines.length === 0) return <StateLine state="stopped">{t("widgets.work.nothing")}</StateLine>;
  const first = work.lines[0];
  return (
    <div className="flex h-full flex-col gap-2">
      <p className="flex items-baseline gap-2">
        <Big>{work.running}</Big>
        <span className="text-13 text-fg-muted">{t("widgets.work.running")}</span>
        {work.queued > 0 ? <span className="text-13 text-fg-muted">{t("widgets.work.queued", { count: work.queued })}</span> : null}
      </p>
      {first !== undefined ? (
        <div className="min-w-0 w-wide:hidden">
          {first.percent !== null ? <Progress value={first.percent} label={first.title} showValue={false} /> : <p className="truncate text-13 text-fg">{first.title}</p>}
          {first.step !== null ? <p className="mt-1 truncate text-12 text-fg-muted">{first.step}</p> : null}
        </div>
      ) : null}
      <ul className="hidden min-h-0 flex-1 flex-col gap-2.5 overflow-hidden w-wide:flex">
        {work.lines.map((line) => (
          <WorkRow key={line.key} line={line} fleet={scope === "fleet"} />
        ))}
      </ul>
      {unsupported.length > 0 ? <p className="hidden truncate text-12 text-fg-faint w-tall:block">{t("widgets.work.unsupported", { servers: unsupported.join(", ") })}</p> : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Recent activity

function ActivityRow({ t, line }: { t: T; line: ActivityLine }) {
  const { entry, at, server } = line;
  const view = deployStatus(entry.status, t.locale);
  const quiet = view.state === "running";
  const domain = entry.domain ?? null;
  const named = entry.actor_name !== null && entry.actor_name !== undefined && entry.actor_name !== "";
  const who = named ? (entry.actor_name ?? "") : entry.actor !== null && entry.actor !== undefined && entry.actor !== "" ? describeActor(t, entry.actor).label : null;
  return (
    <li className="flex shrink-0 items-start gap-2.5 border-b border-border py-2 last:border-b-0">
      <span className="mt-1">
        <Glyph state={view.state} size={10} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-13 text-fg">
          {domain !== null ? (
            <>
              {jobActionLabel(t, entry.type)}{" "}
              <Link to="/apps/$domain" params={{ domain }} {...(line.node !== undefined ? { search: { node: line.node ?? undefined } as never } : {})} className="font-medium hover:underline" translate="no">
                {domain}
              </Link>
            </>
          ) : (
            <span translate={entry.title_coded ? undefined : "no"}>{entry.title !== "" ? serverSentence(t.locale, entry.title_coded, entry.title, "text") : jobActionLabel(t, entry.type)}</span>
          )}{" "}
          <span className={cx("text-12", quiet ? "sr-only" : stateTextClass(view.state))}>{view.label}</span>
        </span>
        {who !== null || server !== null ? (
          <span className="block truncate text-12 text-fg-muted">
            <span translate={named ? "no" : undefined}>{who ?? ""}</span>
            {server !== null ? (
              <>
                {who !== null ? " " : ""}
                <Mono tone="muted">{server}</Mono>
              </>
            ) : null}
          </span>
        ) : null}
      </span>
      {at !== null ? <RelativeTime value={at.toISOString()} className="shrink-0 text-12 text-fg-faint" /> : null}
    </li>
  );
}

/** Who did what, the latest first. */
export function ActivityBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const home = useScopedHome(scope);
  if (home.lines === undefined) return <WidgetPending title={t("widgets.activity.title")} failed={home.error !== null} onRetry={home.retry} />;
  if (home.lines.length === 0) return <p className="text-13 text-fg-muted">{t("overview.activity.emptyTitle")}</p>;
  return (
    <ul className="flex h-full flex-col overflow-hidden [&>li:nth-child(n+4)]:hidden w-tall:[&>li:nth-child(n+4)]:flex">
      {home.lines.map((line) => (
        <ActivityRow key={line.key} t={t} line={line} />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------
// Alerts

/**
 * The alerts that are firing now, and the probes that are down: how many and which. They are read from the list of what
 * needs attention, which has them already (AL-1), so the widget asks nothing of its own, for one server or for all.
 */
export function AlertsBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const home = useScopedHome(scope);
  if (home.entries === undefined) return <WidgetPending title={t("widgets.alerts.title")} failed={home.error !== null} onRetry={home.retry} />;
  const firing = home.entries.filter((entry) => entry.item.subject["kind"] === "alert" || entry.item.subject["kind"] === "probe");
  if (firing.length === 0) return <StateLine state="running">{t("widgets.alerts.none")}</StateLine>;
  return (
    <div className="flex h-full flex-col gap-1.5">
      <p className="flex items-baseline gap-2">
        <Glyph state={firing.some((entry) => entry.item.severity === "fail") ? "failed" : "warning"} size={14} />
        <Big>{firing.length}</Big>
        <span className="text-13 text-fg-muted">{t("widgets.alerts.firing")}</span>
      </p>
      <ul className="flex min-h-0 flex-col gap-1 overflow-hidden">
        {firing.slice(0, 4).map((entry) => (
          <li key={entry.key} className="flex min-w-0 items-center gap-2 text-12 text-fg">
            <Mono truncate>{entry.item.title}</Mono>
            {home.several && entry.server !== null ? (
              <span translate="no" className="hidden shrink-0 text-fg-muted w-mid:inline">
                {entry.server.name}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
