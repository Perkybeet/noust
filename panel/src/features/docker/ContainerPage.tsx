/**
 * One container, from the inside: its state and why it is in it, its logs as they are written, what it uses, which image it comes
 * from, what it touches, and the stack and the application it belongs to. Whose it is decides what the header offers: an
 * application's is changed from the application, Noust's own is read, and what nobody of Noust made is the instance's to start, stop and
 * remove. The system's last words before it stopped are shown verbatim, never paraphrased.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { dockerContainerQuery } from "../../api/queries/docker";
import type { DockerContainerDetail } from "../../api/queries/docker";
import { DetailPage } from "../../components/page/DetailPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Can } from "../../components/page/permissions";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { OwnerMark } from "../../components/page/OwnerMark";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { ICONS } from "../../components/ui/icons";
import { LogViewer } from "../../components/ui/LogViewer";
import { MenuItem } from "../../components/ui/Menu";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { Meter } from "../../components/ui/Progress";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { NodeCapabilityGate } from "../../nodes/capability";
import { useNode } from "../../nodes/useNode";
import { useLogStream } from "../../realtime/sockets";
import { TerminalPanel } from "../terminal/TerminalPanel";
import { useContainerVerb } from "./actions";
import { dockerLine } from "./logLines";
import { isUp, ownerOf, ownerTarget, stateView } from "./model";
import { ContainerPill } from "./pieces";
import { RemoveDialog } from "./RemoveDialog";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

/** Most lines the live log holds; the oldest go first. */
const LOG_CAP = 5_000;

/** Lines read when the stream opens. */
const BACKLOG = 200;

type Detail = DockerContainerDetail;
type Why = NonNullable<Detail["why"]>;

/** The sentence under "Why it is like this": how it ended and, for a container that keeps falling, how often. */
function whySentence(t: T, why: Why): string | null {
  const code = why.exit_code;
  if (why.looping) {
    if (why.oom_killed) {
      return why.memory_limit_bytes !== null && why.memory_limit_bytes !== undefined
        ? t("docker.container.why.loopOomLimit", { limit: formatBytes(why.memory_limit_bytes, t.locale), count: why.restarts_recent, minutes: why.window_minutes })
        : t("docker.container.why.loopOom", { count: why.restarts_recent, minutes: why.window_minutes });
    }
    return t("docker.container.why.loop", { code: code ?? 0, count: why.restarts_recent, minutes: why.window_minutes });
  }
  switch (why.kind) {
    case "oom":
      return t("docker.container.why.oom");
    case "killed":
      return t("docker.container.why.killed");
    case "terminated":
      return t("docker.container.why.terminated");
    case "ended":
      return t("docker.container.why.ended");
    case "error":
      return t("docker.container.why.error", { code: code ?? 1 });
    case "unhealthy":
      return t("docker.container.why.unhealthy");
    case "dead":
      return t("docker.container.why.dead");
    default:
      return null;
  }
}

/** Whether the account of why a container is as it is has a line, a health check or the last words to put in it. */
function whyHasSomethingToSay(t: T, why: Why, up: boolean): boolean {
  if (whySentence(t, why) !== null) return true;
  if (why.health?.status === "unhealthy" && why.health.last_output !== "") return true;
  if (!up && why.finished_at !== null && why.finished_at !== undefined) return true;
  return why.last_lines !== "" && !up;
}

/** What reads as the page's banner: the state that asks for the operator, and what to do about it. */
function ProblemBanner({ detail, onOwnerPage }: { detail: Detail; onOwnerPage: ReactNode }) {
  const t = useT();
  const state = detail.state;
  if (state?.problem !== true) return null;
  const kind = state.kind === "loop" || state.kind === "oom" || state.kind === "killed" || state.kind === "error" || state.kind === "unhealthy" || state.kind === "dead" ? state.kind : "error";
  const owner = detail.owner === null || detail.owner === undefined ? null : ownerOf(detail.owner);
  return (
    <Notice variant="banner" tone={kind === "unhealthy" ? "warning" : "error"} title={t(`docker.container.banner.${kind}.title`)} {...(onOwnerPage !== null && owner?.kind === "app" ? { action: onOwnerPage } : {})}>
      {owner?.kind === "app" && (kind === "loop" || kind === "oom") ? t("docker.container.banner.limitInApp") : t(`docker.container.banner.${kind}.fix`)}
    </Notice>
  );
}

function Usage({ usage }: { usage: NonNullable<Detail["usage"]> }) {
  const t = useT();
  if (!usage.available) {
    const reason = usage.reason === "not_running" || usage.reason === "restarting" || usage.reason === "too_young" ? usage.reason : "no_reading";
    return <p className="text-13 text-fg-muted">{t(`docker.container.usage.reason.${reason}`)}</p>;
  }
  const memory = usage.memory_bytes ?? null;
  const limit = usage.memory_limit_bytes ?? null;
  const items: KeyValueItem[] = [
    ...(usage.cpu_percent !== null && usage.cpu_percent !== undefined ? [{ label: t("docker.container.usage.cpu"), value: `${usage.cpu_percent.toFixed(1)} %`, copy: false as const }] : []),
    ...(memory !== null ? [{ label: t("docker.container.usage.memory"), value: limit !== null ? `${formatBytes(memory, t.locale)} / ${formatBytes(limit, t.locale)}` : formatBytes(memory, t.locale), copy: false as const }] : []),
    ...(usage.net_rx_bytes !== null && usage.net_rx_bytes !== undefined && usage.net_tx_bytes !== null && usage.net_tx_bytes !== undefined
      ? [{ label: t("docker.container.usage.network"), value: t("docker.container.usage.networkValue", { rx: formatBytes(usage.net_rx_bytes, t.locale), tx: formatBytes(usage.net_tx_bytes, t.locale) }), copy: false as const, mono: false }]
      : []),
    ...(usage.block_read_bytes !== null && usage.block_read_bytes !== undefined && usage.block_write_bytes !== null && usage.block_write_bytes !== undefined
      ? [{ label: t("docker.container.usage.disk"), value: t("docker.container.usage.diskValue", { read: formatBytes(usage.block_read_bytes, t.locale), write: formatBytes(usage.block_write_bytes, t.locale) }), copy: false as const, mono: false }]
      : []),
    ...(usage.pids !== null && usage.pids !== undefined ? [{ label: t("docker.container.usage.processes"), value: String(usage.pids), copy: false as const }] : []),
  ];
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {usage.memory_percent !== null && usage.memory_percent !== undefined ? <Meter size="sm" label={t("docker.container.usage.memory")} value={usage.memory_percent} /> : null}
      <KeyValueList items={items} />
    </div>
  );
}

function LiveLogs({ name }: { name: string }) {
  const t = useT();
  const stream = useLogStream(name, { lines: BACKLOG, cap: LOG_CAP, path: (ref) => `/ws/docker/logs/${encodeURIComponent(ref)}` });
  const lines = useMemo(() => stream.lines.map(dockerLine), [stream.lines]);
  return (
    <SurfaceSection title={t("docker.container.logs.title")} icon={OBJECT_ICONS.log} description={t("docker.container.logs.description")}>
      <div className="flex min-w-0 flex-col gap-3">
        {stream.error !== null ? <ErrorBlock live compact error={{ detail: stream.error }} title={t("docker.container.logs.failed")} /> : null}
        <div className="h-editor">
          <LogViewer
            lines={lines}
            height="fill"
            searchable
            loading={stream.status === "connecting"}
            label={t("docker.container.logs.label", { name })}
            filename={`${name}.log`}
            // A container that is not running ends its output: the stream closes, and nothing written is the answer, not a connection to wait for.
            emptyMessage={stream.status === "open" || stream.status === "closed" ? t("docker.container.logs.emptyOpen") : t("docker.container.logs.emptyConnecting")}
          />
        </div>
      </div>
    </SurfaceSection>
  );
}

function Facts({ detail }: { detail: Detail }) {
  const t = useT();
  const container = detail.container;
  if (container === null || container === undefined) return null;
  const owner = detail.owner === null || detail.owner === undefined ? null : ownerOf(detail.owner);
  const target = detail.owner === null || detail.owner === undefined ? undefined : ownerTarget(detail.owner);
  const touches = detail.touches;
  const mounts = touches?.mounts ?? [];
  const policy = detail.restart_policy;
  const items: KeyValueItem[] = [
    { label: t("docker.container.facts.image"), value: <TextLink to="/server/docker/images" search={{ q: container.image }} size="ui">{container.image}</TextLink>, mono: true, copy: container.image },
    ...(container.project !== "" ? [{ label: t("docker.container.facts.stack"), value: <TextLink to="/server/docker/stacks/$name" params={{ name: container.project }} size="ui">{container.project}</TextLink>, mono: true, copy: container.project }] : []),
    ...(owner !== null ? [{ label: t("docker.columns.owner"), value: <OwnerMark kind={owner.kind} subject={owner.subject} {...(target !== undefined ? { target } : {})} />, mono: false, copy: false as const }] : []),
    { label: t("docker.container.facts.volumes"), value: mounts.length === 0 ? t("docker.container.facts.none") : mounts.map((mount) => (mount.name !== "" ? mount.name : mount.destination)).join(", "), mono: mounts.length > 0 },
    { label: t("docker.container.facts.networks"), value: (touches?.networks ?? []).length === 0 ? t("docker.container.facts.none") : (touches?.networks ?? []).join(", "), mono: (touches?.networks ?? []).length > 0 },
    ...(touches?.ports !== undefined && touches.ports !== "" ? [{ label: t("docker.container.facts.ports"), value: touches.ports, mono: true }] : []),
    ...(policy !== null && policy !== undefined ? [{ label: t("docker.container.facts.restartPolicy"), value: t(`docker.container.policy.${policy.name === "always" || policy.name === "unless-stopped" || policy.name === "on-failure" ? policy.name : "no"}`), mono: false, copy: false as const }] : []),
    ...(touches?.privileged === true ? [{ label: t("docker.container.facts.privileged"), value: t("docker.container.facts.privilegedYes"), mono: false, copy: false as const }] : []),
  ];
  return (
    <SurfaceSection title={t("docker.container.facts.title")} icon={OBJECT_ICONS.container}>
      <KeyValueList items={items} />
    </SurfaceSection>
  );
}

/** The shell of a container, offered where the server says it may be opened. It scrolls into view when a menu's "Open console" brought the person here for it. */
function Console({ name, app, requested }: { name: string; app: boolean; requested: boolean }) {
  const t = useT();
  const section = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (requested) section.current?.scrollIntoView({ block: "start" });
  }, [requested]);
  return (
    <div ref={section}>
      <SurfaceSection title={t("docker.container.console.title")} icon={OBJECT_ICONS.terminal} description={t("docker.container.console.description")}>
        <TerminalPanel target={{ kind: "container", ref: name, app }} />
      </SurfaceSection>
    </div>
  );
}

export function ContainerPage({ name, console: consoleRequested = false }: { name: string; console?: boolean }) {
  const t = useT();
  const { node } = useNode();
  const query = useQuery(dockerContainerQuery(name));
  const start = useContainerVerb("start");
  const stop = useContainerVerb("stop");
  const restart = useContainerVerb("restart");
  const [stopping, setStopping] = useState(false);
  const [removing, setRemoving] = useState(false);
  const detail = query.data;
  const container = detail?.container;
  const owner = detail?.owner === null || detail?.owner === undefined ? null : ownerOf(detail.owner);
  const target = detail?.owner === null || detail?.owner === undefined ? undefined : ownerTarget(detail.owner);
  const can = container?.can;
  const up = container !== null && container !== undefined && isUp(container.state);
  const kind = detail?.state?.kind ?? container?.state_kind ?? "other";

  // The way to the owner is the main action of a container that is changed from there: it is the one thing to do on this page.
  const ownerLink: ReactNode =
    target !== undefined && owner !== null ? (
      owner.kind === "app" ? (
        <Link to="/apps/$domain" params={{ domain: owner.subject }} className={buttonClassName("primary")}>
          {t("docker.container.openApp")}
        </Link>
      ) : (
        <Link to="/databases/engines" className={buttonClassName("primary")}>
          {t("docker.container.openEngine")}
        </Link>
      )
    ) : null;

  const primary: ReactNode =
    can === undefined ? undefined : can.restart && up ? (
      <Can permission="docker.manage">
        <Button variant="primary" icon={<VERB_ICONS.rebuild aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate(name)}>
          {t("common.words.restart")}
        </Button>
      </Can>
    ) : can.start && !up ? (
      <Can permission="docker.manage">
        <Button variant="primary" icon={<VERB_ICONS.turnOn aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate(name)}>
          {t("common.words.start")}
        </Button>
      </Can>
    ) : (
      (ownerLink ?? undefined)
    );

  const secondary: ReactNode =
    can?.stop === true && up ? (
      <Can permission="docker.manage">
        <Button variant="secondary" icon={<VERB_ICONS.turnOff aria-hidden="true" />} onClick={() => setStopping(true)}>
          {t("common.words.stop")}
        </Button>
      </Can>
    ) : undefined;

  const overflow: ReactNode =
    can?.remove === true ? (
      <MenuItem destructive icon={<ICONS.delete />} onClick={() => setRemoving(true)}>
        {t("docker.menu.remove")}
      </MenuItem>
    ) : undefined;

  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <DetailPage
        header={{
          title: name,
          mono: true,
          ...(container !== undefined ? { status: <ContainerPill kind={kind} appearance="pill" size="md" /> } : {}),
          ...(detail?.owner !== null && detail?.owner !== undefined && owner !== null ? { description: <OwnerMark variant="sentence" kind={owner.kind} subject={owner.subject} {...(target !== undefined ? { target } : {})} /> } : {}),
          ...(secondary !== undefined ? { secondaryActions: secondary } : {}),
          ...(primary !== undefined ? { primaryAction: primary } : {}),
          ...(overflow !== undefined ? { overflow } : {}),
        }}
        {...(detail?.available === true
          ? {
              aside: (
                <Sections surfaces>
                  {detail.usage !== null && detail.usage !== undefined ? (
                    <SurfaceSection title={t("docker.container.usage.title")} icon={OBJECT_ICONS.resources}>
                      <Usage usage={detail.usage} />
                    </SurfaceSection>
                  ) : null}
                  <Facts detail={detail} />
                </Sections>
              ),
            }
          : {})}
        {...(detail?.available === true ? { banner: <ProblemBanner detail={detail} onOwnerPage={owner?.kind === "app" && owner.subject !== "" ? <Link to="/apps/$domain/resources" params={{ domain: owner.subject }} className={buttonClassName("secondary", "sm")}>{t("docker.container.banner.openLimit")}</Link> : null} /> } : {})}
      >
        {query.isError && detail === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.container.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : detail === undefined ? (
          <LoadingRegion label={t("docker.container.loading")}>
            <Skeleton className="h-64 w-full" />
          </LoadingRegion>
        ) : !detail.available ? (
          <Unavailable engine={detail.engine} />
        ) : (
          <Sections surfaces>
            {detail.why !== null && detail.why !== undefined && whyHasSomethingToSay(t, detail.why, up) ? (
              <SurfaceSection title={t("docker.container.why.title")} icon={OBJECT_ICONS.diagnose}>
                <div className="flex min-w-0 flex-col gap-3">
                  {whySentence(t, detail.why) !== null ? <p className="max-w-measure text-14 text-pretty text-fg">{whySentence(t, detail.why)}</p> : null}
                  {detail.why.finished_at !== null && detail.why.finished_at !== undefined && !up ? (
                    <p className="text-13 text-fg-muted">
                      {t.rich("docker.container.why.finished", { when: <RelativeTime key="when" value={detail.why.finished_at} /> })}
                    </p>
                  ) : null}
                  {detail.why.health?.status === "unhealthy" && detail.why.health.last_output !== "" ? (
                    <div className="flex min-w-0 flex-col gap-1.5">
                      <p className="text-13 font-medium text-fg">{t("docker.container.why.healthSaid", { count: detail.why.health.failing_streak })}</p>
                      <SystemOutput label={t("docker.container.why.healthLabel", { name })}>{detail.why.health.last_output}</SystemOutput>
                    </div>
                  ) : null}
                  {detail.why.last_lines !== "" && stateView(kind).status !== "running" ? (
                    <div className="flex min-w-0 flex-col gap-1.5">
                      <p className="text-13 font-medium text-fg">{t("docker.container.why.lastWords")}</p>
                      <SystemOutput label={t("docker.container.why.lastLabel", { name })}>{detail.why.last_lines}</SystemOutput>
                    </div>
                  ) : null}
                </div>
              </SurfaceSection>
            ) : detail.why !== null && detail.why !== undefined && up ? (
              // Nothing is wrong: the page says so, and how long it has run and how often it started again, instead of a card with only a title.
              <SurfaceSection title={t("docker.container.healthy.title")} icon={OBJECT_ICONS.diagnose} description={t("docker.container.healthy.line")}>
                <KeyValueList
                  items={[
                    ...(detail.why.started_at !== null && detail.why.started_at !== undefined
                      ? [{ label: t("docker.container.healthy.since"), value: <RelativeTime value={detail.why.started_at} />, mono: false, copy: false as const }]
                      : []),
                    { label: t("docker.container.healthy.restarts"), value: String(detail.why.restart_count), mono: false, copy: false as const },
                  ]}
                />
              </SurfaceSection>
            ) : null}
            <LiveLogs name={name} />
            {can?.console === true && up ? <Console name={name} app={owner?.kind === "app"} requested={consoleRequested} /> : null}
          </Sections>
        )}
      </DetailPage>
      <Can permission="docker.manage">
        <ConfirmDialog
          friction="simple"
          open={stopping}
          onOpenChange={setStopping}
          title={t("docker.stop.title", { name })}
          description={t("docker.stop.description")}
          actionLabel={t("common.words.stop")}
          {...(node !== null ? { server: node } : {})}
          onConfirm={async () => {
            await stop.mutateAsync(name);
          }}
        />
      </Can>
      {removing && container !== undefined && detail?.owner !== undefined && detail.owner !== null ? (
        <RemoveDialog
          subject={{ target: { kind: "container", ref: name, force: up }, name, owner: detail.owner, running: up }}
          onClose={() => setRemoving(false)}
        />
      ) : null}
    </NodeCapabilityGate>
  );
}
