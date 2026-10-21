/**
 * The probes, each with where it stands: up, down since when, what it last answered and how it
 * has done over the last day, week, month and quarter. A probe can be asked now, switched off,
 * edited and removed; its incidents are one click away.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ChevronDown, ChevronRight, Pencil, RefreshCw } from "lucide-react";
import { useState } from "react";

import { checkProbe, probeHistoryQuery, probeKeys, probesQuery, removeProbe, saveProbe } from "../../../api/queries/probes";
import type { Probe, ProbeCheck, ProbeList as ProbeListData } from "../../../api/queries/probes";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { ICONS } from "../../../components/ui/icons";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { RowList } from "../../../components/ui/RowList";
import { Skeleton, SkeletonRows } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { UPTIME_WINDOWS, lastAnswer, probeBody, probeFormFrom, standing, stretch, uptimeText } from "./data";
import { ProbeDrawer } from "./ProbeDrawer";

/** What asking a probe now got, in one line under the row. */
function CheckOutcome({ result }: { result: ProbeCheck }) {
  const t = useT();
  const detail = [result.status === null || result.status === undefined ? null : String(result.status), result.latency_ms === null || result.latency_ms === undefined ? null : t("probes.answer.inMs", { ms: result.latency_ms })]
    .filter((part) => part !== null)
    .join(" ");
  return (
    <InlineMessage tone={result.ok ? "success" : "error"} live>
      {result.ok ? t("probes.check.good", { detail }) : t("probes.check.bad", { detail })}
      {result.error !== null && result.error !== undefined && result.error !== "" ? (
        <>
          {" "}
          <Mono>{result.error}</Mono>
        </>
      ) : null}
    </InlineMessage>
  );
}

/** A probe's incidents: when each began, how long it lasted and what the first bad answer said. */
function Incidents({ name }: { name: string }) {
  const t = useT();
  const history = useQuery(probeHistoryQuery(name));
  // When it was read: an incident that is still going is measured to this moment.
  const [now] = useState(() => Math.floor(Date.now() / 1000));
  return (
    <QueryState query={history} label={t("probes.history.loading")} skeleton={<Skeleton className="h-4 w-60" />}>
      {(data) =>
        data.incidents.length === 0 ? (
          <p className="text-12 text-fg-muted">{t("probes.history.none")}</p>
        ) : (
          <ul aria-label={t("probes.history.label", { name })} className="flex min-w-0 flex-col gap-1 text-12">
            {data.incidents.map((incident) => (
              <li key={incident.started_at} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                <RelativeTime value={incident.started_at} />
                <span className="text-fg">
                  {incident.ended_at === null || incident.ended_at === undefined
                    ? t("probes.history.stillDown", { duration: stretch(incident.started_at, null, now, t.locale) })
                    : t("probes.history.lasted", { duration: stretch(incident.started_at, incident.ended_at, now, t.locale) })}
                </span>
                {incident.reason !== "" ? (
                  <Mono className="min-w-0 truncate text-fg-muted" title={incident.reason}>
                    {incident.reason}
                  </Mono>
                ) : null}
              </li>
            ))}
          </ul>
        )
      }
    </QueryState>
  );
}

function ProbeRow({ probe }: { probe: Probe }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [outcome, setOutcome] = useState<ProbeCheck | null>(null);
  const [history, setHistory] = useState(false);
  const state = standing(probe);
  const answer = lastAnswer(t, probe.status);

  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: probeKeys.all });
  const toggle = useMutation({
    mutationFn: (next: boolean) => saveProbe(probe.name, probeBody(probeFormFrom(probe), next)),
    onSuccess: refresh,
    onError: (error) => {
      reportActionError(t("probes.switchFailed"), error);
    },
  });
  const check = useMutation({
    mutationFn: () => checkProbe(probe.name),
    onSuccess: async (result) => {
      setOutcome(result);
      await refresh();
    },
    onError: (error) => {
      reportActionError(t("probes.check.failed"), error);
    },
  });
  // While the write is in flight the switch shows where it is going.
  const checked = toggle.isPending ? toggle.variables : probe.enabled;

  return (
    <RowList.Item
      leading={
        <Switch
          aria-label={t("probes.switchLabel", { name: probe.name })}
          checked={checked}
          busy={toggle.isPending}
          onCheckedChange={(next) => {
            toggle.mutate(next);
          }}
        />
      }
      actions={
        <RowMenu name={probe.name}>
          <MenuItem
            icon={<RefreshCw />}
            disabled={!probe.enabled || check.isPending}
            onClick={() => {
              check.mutate();
            }}
          >
            {t("probes.check.button")}
          </MenuItem>
          <MenuItem
            icon={<Pencil />}
            onClick={() => {
              setEditing(true);
            }}
          >
            {t("probes.edit")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem
            icon={<ICONS.delete />}
            destructive
            onClick={() => {
              setRemoving(true);
            }}
          >
            {t("probes.remove")}
          </MenuItem>
        </RowMenu>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Mono tone="default" className="text-13 font-medium">
              {probe.name}
            </Mono>
            <StatusPill state={state.state} label={t(`probes.state.${state.key}`)} size="sm" />
            {probe.public ? <span className="text-12 text-fg-muted">{t("probes.public")}</span> : null}
          </div>
          <Mono className="min-w-0 truncate text-12 text-fg-muted" title={probe.address}>
            {probe.address}
          </Mono>
          <p className="flex min-w-0 flex-wrap items-center gap-x-3 text-12 text-fg-muted">
            {probe.status.last_checked !== null && probe.status.last_checked !== undefined ? (
              <>
                <span>
                  {t.rich("probes.lastAsked", { when: <RelativeTime key="when" value={probe.status.last_checked} /> })}
                </span>
                {answer !== null ? (
                  <Mono className={probe.status.state === "down" ? "text-fail" : "text-fg-muted"}>
                    {answer}
                  </Mono>
                ) : null}
              </>
            ) : (
              <span>{t("probes.neverAsked")}</span>
            )}
            {probe.status.state === "down" ? (
              <span>{t.rich("probes.downSince", { when: <RelativeTime key="when" value={probe.status.since} /> })}</span>
            ) : null}
          </p>
          <ul aria-label={t("probes.uptime.label", { name: probe.name })} className="flex min-w-0 flex-wrap gap-x-4 gap-y-0.5 text-12 text-fg-muted">
            {UPTIME_WINDOWS.map((window) => {
              const entry = probe.uptime[window];
              return (
                <li key={window}>
                  {t("probes.uptime.entry", { window: t(`probes.uptime.window.${window}`), value: uptimeText(entry?.percent, t.locale) })}
                </li>
              );
            })}
          </ul>
        </div>
        {outcome !== null ? <CheckOutcome result={outcome} /> : null}
        <div className="flex min-w-0 flex-col gap-2">
          <div>
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={history}
              icon={history ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
              onClick={() => {
                setHistory((open) => !open);
              }}
              className="-ml-2.5"
            >
              {t("probes.history.title")}
            </Button>
          </div>
          {/* Read only when opened: a page of probes does not ask the server for every one's incidents. */}
          {history ? <Incidents name={probe.name} /> : null}
        </div>
      </div>
      <ProbeDrawer probe={probe} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        friction="simple"
        open={removing}
        onOpenChange={setRemoving}
        title={t("probes.removeTitle", { name: probe.name })}
        description={t("probes.removeDescription")}
        actionLabel={t("probes.remove")}
        destructive
        onConfirm={async () => {
          await removeProbe(probe.name);
          queryClient.removeQueries({ queryKey: probeKeys.history(probe.name) });
          await refresh();
        }}
      />
    </RowList.Item>
  );
}

function Probes({ data, onCreate }: { data: ProbeListData; onCreate: () => void }) {
  const t = useT();
  return (
    <>
      {data.items.length === 0 ? null : (
        <p role="status" className="text-13 text-fg-muted">
          {data.down === 0 ? t("probes.allUp") : t("probes.downCount", { count: data.down })}
        </p>
      )}
      {data.items.length === 0 ? (
        <EmptyState
          variant="firstUse"
          level={3}
          icon={<Activity />}
          title={t("probes.emptyTitle")}
          description={t("probes.emptyDescription")}
          // With no probe the header has no button: the empty state's is the one way to add the first.
          action={
            <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={onCreate}>
              {t("probes.new")}
            </Button>
          }
        />
      ) : (
        <RowList aria-label={t("probes.listLabel")}>
          {data.items.map((probe) => (
            <ProbeRow key={probe.name} probe={probe} />
          ))}
        </RowList>
      )}
    </>
  );
}

/** The probes section: its title, the way to add one, and the list. */
export function ProbeList() {
  const t = useT();
  const query = useQuery(probesQuery());
  const [creating, setCreating] = useState(false);
  return (
    <Section
      title={t("probes.title")}
      description={t("probes.description")}
      actions={
        query.data === undefined || query.data.items.length === 0 ? undefined : (
          <Button
            variant="primary"
            icon={<ICONS.add aria-hidden="true" />}
            onClick={() => {
              setCreating(true);
            }}
          >
            {t("probes.new")}
          </Button>
        )
      }
    >
      <QueryState query={query} label={t("probes.loadingLabel")} skeleton={<SkeletonRows rows={2} />}>
        {(data) => (
          <>
            <Probes
              data={data}
              onCreate={() => {
                setCreating(true);
              }}
            />
            <ProbeDrawer open={creating} onOpenChange={setCreating} />
          </>
        )}
      </QueryState>
    </Section>
  );
}
