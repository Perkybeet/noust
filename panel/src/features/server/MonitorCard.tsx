import { useQuery } from "@tanstack/react-query";
import { Mail } from "lucide-react";

import { monitorConfigQuery, monitorStatusQuery, observationsQuery } from "../../api/queries/monitor";
import type { MonitorStatus } from "../../api/queries/monitor";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useMonitorActions } from "./useMonitorActions";

/** One open finding: what stood out, and the button that dismisses it. */
function ObservationRow({
  t,
  process,
  pid,
  severity,
  signal,
  detail,
  when,
  onAcknowledge,
  pending,
}: {
  t: T;
  process: string;
  pid: number;
  severity: string;
  signal: string;
  detail: string | null | undefined;
  when: string;
  onAcknowledge: () => void;
  pending: boolean;
}) {
  const state = severity === "warning" ? "warning" : "unknown";
  return (
    <li className="flex items-start justify-between gap-3 py-2.5">
      <div className="flex min-w-0 items-start gap-2.5">
        <StatusGlyph state={state} size={12} className={`mt-1 shrink-0 ${severity === "warning" ? "text-warn" : "text-fg-faint"}`} />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-13 text-fg">
            <Mono>{process}</Mono>
            <span className="text-fg-faint">{t("server.monitor.pidSignal", { pid, signal })}</span>
          </p>
          {detail ? <p className="text-12 text-fg-muted">{detail}</p> : null}
          <RelativeTime value={when} className="text-12 text-fg-faint" />
        </div>
      </div>
      <IconButton label={t("server.monitor.acknowledgeAria", { process })} icon={<ICONS.dismiss />} size="sm" disabled={pending} onClick={onAcknowledge} />
    </li>
  );
}

/** The unit's state and the buttons for whatever it needs next: install, enable, start. */
function UnitRow({ t, status }: { t: T; status: MonitorStatus }) {
  const { install, enable, disable, start, stop } = useMonitorActions();
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2.5">
        {status.installed ? (
          <StatusPill
            state={status.active ? "running" : "stopped"}
            label={
              status.active
                ? t("server.monitor.running")
                : status.enabled
                  ? t("server.monitor.stopped")
                  : t("server.monitor.installedNotRunning")
            }
          />
        ) : (
          <Badge>{t("server.monitor.notInstalled")}</Badge>
        )}
        {status.uptime ? <span className="text-12 text-fg-faint">{t("server.monitor.since", { uptime: status.uptime })}</span> : null}
      </div>
      <div className="flex items-center gap-2">
        {!status.installed ? (
          <Button size="sm" loading={install.isPending} onClick={() => install.mutate()}>
            {t("server.monitor.install")}
          </Button>
        ) : (
          <>
            {status.enabled ? (
              <Button size="sm" disabled={disable.isPending} onClick={() => disable.mutate()}>
                {t("server.monitor.disable")}
              </Button>
            ) : (
              <Button size="sm" loading={enable.isPending} onClick={() => enable.mutate()}>
                {t("server.monitor.enable")}
              </Button>
            )}
            {status.active ? (
              <Button size="sm" disabled={stop.isPending} onClick={() => stop.mutate()}>
                {t("server.monitor.stop")}
              </Button>
            ) : (
              <Button size="sm" loading={start.isPending} onClick={() => start.mutate()}>
                {t("server.monitor.start")}
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The resource monitor: whether its unit is installed and running, its open findings with a
 * way to dismiss them, and a button to prove the email channel works.
 */
export function MonitorCard() {
  const t = useT();
  const status = useQuery(monitorStatusQuery());
  const config = useQuery(monitorConfigQuery());
  const observations = useQuery(observationsQuery(false));
  const { testEmail, acknowledge } = useMonitorActions();

  return (
    <Card level={2}
      title={t("server.monitor.title")}
      description={t("server.monitor.description")}
      actions={
        status.data?.installed ? (
          <Button size="sm" icon={<Mail aria-hidden="true" />} loading={testEmail.isPending} onClick={() => testEmail.mutate()}>
            {t("server.monitor.sendTestEmail")}
          </Button>
        ) : undefined
      }
      padding="sm"
    >
      {status.isError && status.data === undefined ? (
        <ErrorBlock compact error={status.error} title={t("server.monitor.couldNotReadStatus")} onRetry={() => void status.refetch()} />
      ) : status.data === undefined ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : (
        <div className="flex flex-col gap-4">
          <UnitRow t={t} status={status.data} />

          {config.data ? (
            <p className="text-12 text-fg-muted">
              {`${t("server.monitor.config", { interval: config.data.scan_interval, cpu: config.data.cpu_threshold, memory: config.data.memory_threshold })} `}
              {config.data.notify ? t("server.monitor.findingsEmailed") : t("server.monitor.findingsNotEmailed")}
            </p>
          ) : null}

          <Subsection title={t("server.monitor.openFindings")}>
            {observations.isError && observations.data === undefined ? (
              <ErrorBlock compact error={observations.error} title={t("server.monitor.couldNotLoadFindings")} onRetry={() => void observations.refetch()} />
            ) : observations.data === undefined ? (
              <Skeleton className="h-12 w-full" />
            ) : observations.data.observations.length === 0 ? (
              <p className="flex items-center gap-2 text-13 text-fg-muted">
                <ICONS.success aria-hidden="true" className="size-icon-sm text-ok" />
                {t("server.monitor.nothingOpen")}
              </p>
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {observations.data.observations.map((observation) => (
                  <ObservationRow
                    key={observation.id ?? `${observation.process_name}-${String(observation.pid)}`}
                    t={t}
                    process={observation.process_name}
                    pid={observation.pid}
                    severity={observation.severity}
                    signal={observation.signal}
                    detail={observation.detail}
                    when={observation.observed_at}
                    pending={acknowledge.isPending}
                    onAcknowledge={() => {
                      if (observation.id !== null && observation.id !== undefined) acknowledge.mutate(observation.id);
                    }}
                  />
                ))}
              </ul>
            )}
          </Subsection>
        </div>
      )}
    </Card>
  );
}
