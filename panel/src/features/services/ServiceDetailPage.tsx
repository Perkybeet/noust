import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Cog, MoreHorizontal, Play, RotateCw, Square, Trash2 } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import { serviceQuery, servicesQuery } from "../../api/queries/services";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { DangerAction, DangerZone } from "../../components/page/DangerZone";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { LogViewer } from "../../components/ui/LogViewer";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useLogStream } from "../../realtime/sockets";
import { UnitEditor } from "./UnitEditor";
import type { ServiceInfo } from "./data";
import { serviceState } from "./data";
import { useServiceActions } from "./useServiceActions";

/** The one crumb every state of this page shares: back to the services list. */
function breadcrumbsFor(t: T): { label: string; to: "/services" }[] {
  return [{ label: t("nav.services.label"), to: "/services" }];
}

function NotFound({ name }: { name: string }) {
  const t = useT();
  return (
    <>
      <PageHeader title={name} breadcrumbs={breadcrumbsFor(t)} />
      <EmptyState
        level={2}
        icon={<Cog />}
        title={t("services.detail.notFoundTitle")}
        description={t("services.detail.notFoundDescription")}
        action={
          <Link to="/services" className={buttonClassName("secondary")}>
            {t("services.detail.allServices")}
          </Link>
        }
        command="wasm service list"
        className="py-16"
      />
    </>
  );
}

/**
 * A unit that exists on this machine but that WASM did not create: found only through the
 * show-all-units listing (`GET /api/services?wasm_only=false`), since the per-name read
 * (`GET /api/services/{name}`) only ever answers what the store tracks and 404s for it.
 * Read-only - no editor, no actions, nothing destructive - the way a foreign row's own menu
 * is already withheld in the list (`ServiceRowActions`).
 */
function ForeignUnit({ name, service }: { name: string; service: ServiceInfo }) {
  const t = useT();
  const view = serviceState(service);
  return (
    <>
      <PageHeader
        title={name}
        breadcrumbs={breadcrumbsFor(t)}
        description={
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <StatusPill state={view.state} label={t(view.label)} />
            {view.detail !== undefined ? <span className="mono text-13 text-fg-muted">{view.detail}</span> : null}
          </span>
        }
      />
      <EmptyState
        level={2}
        icon={<Cog />}
        title={t("services.detail.foreignTitle")}
        description={t("services.detail.foreignDescription")}
        command={`systemctl status ${name}`}
        className="py-16"
      />
    </>
  );
}

/** The live journal of the unit, followed while the page is open. */
function ServiceLogs({ name }: { name: string }) {
  const t = useT();
  const stream = useLogStream(name);
  const reconnecting = stream.status === "reconnecting";
  return (
    <Section
      title={t("services.detail.logsTitle")}
      description={t("services.detail.logsDescription")}
      actions={
        <span role="status" className="flex items-center gap-1.5 text-12 text-fg-muted">
          <span
            aria-hidden="true"
            className={`size-1.5 rounded-pill ${stream.status === "open" ? "bg-ok" : reconnecting ? "bg-warn" : "bg-fg-faint"}`}
          />
          {stream.status === "open" ? t("services.detail.live") : reconnecting ? t("services.detail.reconnecting") : t("services.detail.connecting")}
        </span>
      }
    >
      {stream.error !== null ? <ErrorBlock compact error={{ detail: stream.error }} title={t("services.detail.logStreamFailed")} /> : null}
      <LogViewer lines={stream.lines} label={t("services.detail.logsLabel", { name })} filename={`${name}.log`} height={360} pageSearch />
      {stream.truncated ? <p className="text-12 text-fg-faint">{t("services.detail.truncated")}</p> : null}
    </Section>
  );
}

/**
 * One systemd unit: its state, the actions that act on it directly (no job queue - these are
 * synchronous systemctl calls), its live journal, its raw unit file, and deleting it.
 */
export function ServiceDetailPage({ name }: { name: string }) {
  const t = useT();
  const service = useQuery(serviceQuery(name));
  const { start, stop, restart, enable, disable, remove } = useServiceActions(name);
  const [confirmStop, setConfirmStop] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const navigate = useNavigate();

  // `GET /api/services/{name}` only ever answers what the store tracks and 404s for a unit
  // WASM did not create; the all-units listing is the only way to tell that unit apart from
  // one that never existed at all, so it is fetched only once the plain read has 404d.
  const notFound = service.isError && isApiError(service.error) && service.error.status === 404;
  const allServices = useQuery({ ...servicesQuery(false), enabled: notFound });
  const foreign = notFound ? allServices.data?.services.find((candidate) => candidate.name === name) : undefined;

  if (notFound) {
    if (foreign) return <ForeignUnit name={name} service={foreign} />;
    if (allServices.isPending)
      return <PageHeader title={name} breadcrumbs={breadcrumbsFor(t)} description={<Skeleton className="h-6 w-20 rounded-pill" />} />;
    return <NotFound name={name} />;
  }

  const data = service.data;
  const state = data ? serviceState(data) : null;

  const items: KeyValueItem[] = data
    ? [
        { label: t("services.detail.commandLabel"), value: data.description ?? null },
        { label: t("services.detail.mainPid"), value: data.active && data.pid ? data.pid : null },
        { label: t("services.detail.memoryLabel"), value: data.memory ? formatBytes(Number(data.memory)) : null, mono: false, copy: false },
        {
          label: t("services.detail.sinceLabel"),
          value: data.active && data.uptime ? <RelativeTime value={data.uptime} /> : t("services.table.notRunning"),
          mono: false,
          copy: false,
        },
        { label: t("services.detail.startsAtBoot"), value: data.enabled ? t("services.detail.yes") : t("services.detail.no"), mono: false, copy: false },
        { label: t("services.detail.managedBy"), value: "WASM", mono: false, copy: false },
      ]
    : [];

  return (
    <>
      <PageHeader
        title={name}
        breadcrumbs={breadcrumbsFor(t)}
        description={
          data && state ? (
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <StatusPill state={state.state} label={t(state.label)} />
              {state.detail !== undefined ? <span className="mono text-13 text-fg-muted">{state.detail}</span> : null}
            </span>
          ) : (
            <Skeleton className="h-6 w-20 rounded-pill" />
          )
        }
        actions={
          data ? (
            <div className="flex items-center gap-2">
              {data.active ? (
                <Button icon={<Square aria-hidden="true" />} disabled={stop.isPending} onClick={() => setConfirmStop(true)}>
                  {t("services.actions.stop")}
                </Button>
              ) : (
                <Button icon={<Play aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate()}>
                  {t("services.actions.start")}
                </Button>
              )}
              <Button variant="primary" icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()}>
                {t("services.actions.restart")}
              </Button>
              <Menu
                align="end"
                trigger={<IconButton variant="secondary" label={t("services.actions.moreActions")} icon={<MoreHorizontal />} tooltip={false} />}
              >
                {data.enabled ? (
                  <MenuItem disabled={disable.isPending} onClick={() => disable.mutate()}>
                    {t("services.actions.disableAtBoot")}
                  </MenuItem>
                ) : (
                  <MenuItem disabled={enable.isPending} onClick={() => enable.mutate()}>
                    {t("services.actions.enableAtBoot")}
                  </MenuItem>
                )}
              </Menu>
            </div>
          ) : undefined
        }
      />

      <div className="-mt-4 mb-8 flex flex-col gap-4">
        {service.isError && service.data === undefined ? (
          <ErrorBlock
            error={service.error}
            title={t("services.detail.loadFailed", { name })}
            onRetry={() => void service.refetch()}
            retrying={service.isRefetching}
          />
        ) : null}
      </div>

      {data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-8">
          <span className="sr-only">{t("services.detail.loading")}</span>
          <div aria-hidden="true" className="rounded-card border border-border bg-surface px-4 py-1">
            <KeyValueListSkeleton rows={4} />
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-8">
          <Section title={t("services.detail.overview")}>
            <div className="rounded-card border border-border bg-surface px-4 py-1">
              <KeyValueList items={items} />
            </div>
            <CommandHint command={`wasm service status ${name}`} label={t("services.fromTerminal")} />
          </Section>

          <ServiceLogs name={name} />

          <UnitEditor name={name} />

          <DangerZone>
            <DangerAction
              title={t("services.detail.deleteTitle")}
              description={t("services.detail.deleteDescription")}
              action={
                <Button variant="danger" icon={<Trash2 aria-hidden="true" />} onClick={() => setDeleteOpen(true)}>
                  {t("services.detail.deleteService")}
                </Button>
              }
            />
          </DangerZone>
        </div>
      )}

      <Dialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        size="sm"
        title={t("services.detail.stopTitle", { name })}
        description={t("services.detail.stopDescription")}
        footer={
          <>
            <Button onClick={() => setConfirmStop(false)}>{t("services.cancel")}</Button>
            <Button
              variant="danger"
              loading={stop.isPending}
              onClick={() =>
                stop.mutate(undefined, {
                  onSettled: () => {
                    setConfirmStop(false);
                  },
                })
              }
            >
              {t("services.detail.stopService")}
            </Button>
          </>
        }
      />

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("services.detail.deleteDialogTitle", { name })}
        description={t("services.detail.deleteDescription")}
        confirmText={name}
        actionLabel={t("services.detail.deleteService")}
        onConfirm={async () => {
          await remove.mutateAsync();
          void navigate({ to: "/services" });
        }}
      />
    </>
  );
}
