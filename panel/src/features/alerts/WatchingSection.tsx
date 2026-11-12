import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { patchConfig } from "../../api/queries/config";
import { watchingKeys, watchingQuery, WATCHING_CAPABILITY } from "../../api/queries/watching";
import type { Watch, Watching } from "../../api/queries/watching";
import { Can } from "../../components/page/permissions";
import { QueryState } from "../../components/page/QueryState";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Button } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDateTime, parseTimestamp } from "../../lib/format";
import { useNodeCapability } from "../../nodes/capability";
import { reportActionError } from "../apps/useAppActions";
import { limitedChannels, reachMissing, reachOf, splitWatching, watchWords } from "./watching";

const STATES: Readonly<Record<string, { state: Status; word: "on" | "off" | "muted" }>> = {
  on: { state: "running", word: "on" },
  off: { state: "stopped", word: "off" },
  muted: { state: "warning", word: "muted" },
};

/** Each name of a list of channels, in the language of the console ("Slack, Telegram and email"). */
function listOf(names: readonly string[], locale: string): string {
  return new Intl.ListFormat(locale, { style: "long", type: "conjunction" }).format(names);
}

/** What a row says of where it does not reach: the channels that do not take it, or that none does. */
function exception(t: T, watch: Watch, data: Watching): string | null {
  const missing = reachMissing(watch, data);
  if (missing === null) return null;
  return missing.length === (data.notifications.channels ?? []).length ? t("alerts.watching.reach.toNobody") : t("alerts.watching.reach.notTo", { channels: listOf(missing, t.locale) });
}

/** Where what Noust finds can go, said once above the list: the same for every row, but for the exceptions each row names. Nothing when the channels take it. */
function ReachNotice({ t, data }: { t: T; data: Watching }) {
  const reach = reachOf(data);
  if (reach.kind === "channels") return null;
  const until = reach.kind === "paused" ? parseTimestamp(reach.until) : null;
  const words =
    reach.kind === "off"
      ? { tone: "warning" as const, title: t("alerts.watching.reach.unsentTitle"), body: t("alerts.watching.reach.unsentBody") }
      : reach.kind === "nobody"
        ? { tone: "warning" as const, title: t("alerts.watching.reach.noChannelTitle"), body: t("alerts.watching.reach.noChannelBody") }
        : {
            tone: "info" as const,
            title: t("alerts.watching.reach.pausedTitle", { time: until === null ? reach.until : formatDateTime(until, t.locale) }),
            body: t("alerts.watching.reach.pausedBody"),
          };
  return (
    <Notice
      tone={words.tone}
      title={words.title}
      action={
        <TextLink to="/settings/notifications" size="ui">
          {t("alerts.watching.reach.action", { where: t("nav.settingsTabs.notifications.label") })}
        </TextLink>
      }
    >
      {words.body}
    </Notice>
  );
}

/** The monitor does the watching: when it does not run, nothing below is looked at, and that is the first thing to say. */
function WatcherNotice({ t, data }: { t: T; data: Watching }) {
  const { state } = data.watcher;
  if (state === "running" || state === "unknown") return null;
  return (
    <Notice
      tone="warning"
      title={t("alerts.watching.watcher.title")}
      action={
        <TextLink to="/server/monitor" size="ui">
          {t("alerts.watching.watcher.action")}
        </TextLink>
      }
    >
      {t("alerts.watching.watcher.body")}
    </Notice>
  );
}

/**
 * One thing Noust watches: what it is and what it looks at, and whether it is on at the end of the line. Where it reaches
 * is said once above the list; a row only says it when it is not what the rest say (a channel that switched the message
 * off), so that a list of twelve does not repeat the same two names twelve times.
 */
function WatchRow({ t, watch, reach }: { t: T; watch: Watch; reach: string | null }) {
  const words = watchWords(t, watch);
  const view = STATES[watch.state] ?? STATES["on"];
  return (
    <li className="flex min-w-0 items-start justify-between gap-4 px-5 py-2.5">
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-13 font-medium text-fg">{words.name}</span>
        {words.sentence !== "" ? <span className="max-w-measure text-12 text-pretty text-fg-muted">{words.sentence}</span> : null}
      </span>
      <span className="flex shrink-0 flex-col items-end gap-0.5">
        <StatusPill state={view?.state ?? "unknown"} label={t(`alerts.watching.state.${view?.word ?? "on"}`)} appearance="inline" size="sm" />
        {reach !== null ? (
          <span className="max-w-44 text-right text-12 text-pretty text-fg-faint">
            {reach}
          </span>
        ) : null}
      </span>
    </li>
  );
}

/** One thing that ships off and is worth turning on, apart from the table: what it would ask, of whom, and the button. */
function Offer({ t, watch, busy, onTurnOn }: { t: T; watch: Watch; busy: boolean; onTurnOn: (watch: Watch) => void }) {
  const words = watchWords(t, watch);
  return (
    <div className="flex min-w-0 flex-col gap-3 px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <OBJECT_ICONS.domain aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-13 font-medium text-fg">{words.name}</span>
            <StatusPill state="stopped" label={t("alerts.watching.state.off")} appearance="inline" size="sm" />
          </p>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{words.sentence}</p>
          {watch.asks_outside ? <p className="max-w-measure text-12 text-pretty text-fg-faint">{t("alerts.watching.offer.asksOutside")}</p> : null}
        </div>
      </div>
      <Can permission="settings.manage">
        <Button variant="secondary" loading={busy} aria-label={t("alerts.watching.offer.turnOnAria", { name: words.name })} onClick={() => onTurnOn(watch)}>
          {t("alerts.watching.offer.turnOn")}
        </Button>
      </Can>
    </div>
  );
}

export interface WatchingViewProps {
  data: Watching;
  busy: boolean;
  onTurnOn: (watch: Watch) => void;
}

/**
 * The opening of Alerts: what Noust watches without a rule of yours, whether each is on, and by which channels what it finds
 * would reach you - the server's own answer, drawn. A short table of what is watched; apart from it, what ships off and is worth
 * turning on, with what it asks and of whom. Rules, probes and channels stay on their pages: this joins them (item 159).
 */
export function WatchingView({ data, busy, onTurnOn }: WatchingViewProps) {
  const t = useT();
  const { rows, offers } = splitWatching(data);
  const reach = reachOf(data);
  const limited = limitedChannels(data);
  return (
    <SurfaceSection
      title={t("alerts.watching.title")}
      icon={OBJECT_ICONS.alert}
      description={
        reach.kind === "channels" ? (
          <>
            {t("alerts.watching.reach.via", { channels: listOf(reach.names, t.locale) })}
            {limited.map((name) => (
              <span key={name} className="block text-fg-faint">
                {t("alerts.watching.reach.limited", { name })}
              </span>
            ))}
          </>
        ) : (
          t("alerts.watching.description")
        )
      }
      actions={
        <TextLink to="/alerts/checks" size="ui">
          {t("alerts.watching.limits")}
        </TextLink>
      }
      flush
    >
      <div className="flex flex-col">
        {reach.kind !== "channels" || data.watcher.state !== "running" ? (
          <div className="flex flex-col gap-3 px-5 pb-4">
            <WatcherNotice t={t} data={data} />
            <ReachNotice t={t} data={data} />
          </div>
        ) : null}
        <ul aria-label={t("alerts.watching.caption")} className="divide-y divide-border border-t border-border">
          {rows.map((watch) => (
            <WatchRow key={watch.id} t={t} watch={watch} reach={exception(t, watch, data)} />
          ))}
        </ul>
        {offers.length > 0 ? (
          <div className="flex flex-col divide-y divide-border border-t border-border">
            {offers.map((watch) => (
              <Offer key={watch.id} t={t} watch={watch} busy={busy} onTurnOn={onTurnOn} />
            ))}
          </div>
        ) : null}
      </div>
    </SurfaceSection>
  );
}

/**
 * The section of the server on screen: its own answer, read again after a switch is turned on. A server before 4.3 has no such
 * route, and says so instead of a failure.
 */
export function Watching() {
  const t = useT();
  const queryClient = useQueryClient();
  const capability = useNodeCapability(WATCHING_CAPABILITY);
  const watching = useQuery({ ...watchingQuery(), enabled: capability.status !== "missing" });
  const turnOn = useMutation({
    mutationFn: (watch: Watch) => patchConfig(watch.offer?.key ?? "", watch.offer?.value),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: watchingKeys.all }),
    onError: (error) => {
      reportActionError(t("alerts.watching.offer.failed"), error);
    },
  });
  if (capability.status === "missing") {
    return (
      <SurfaceSection title={t("alerts.watching.title")} icon={OBJECT_ICONS.alert} description={t("alerts.watching.older")} />
    );
  }
  return (
    <QueryState
      query={watching}
      sentences={{ loading: t("alerts.watching.loading"), failed: t("alerts.watching.loadFailed"), refreshFailed: t("alerts.watching.refreshFailed") }}
      skeleton={<SkeletonCard lines={5} />}
    >
      {(data) => <WatchingView data={data} busy={turnOn.isPending} onTurnOn={(watch) => turnOn.mutate(watch)} />}
    </QueryState>
  );
}
