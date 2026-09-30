/**
 * One service (T2): its header with its state and actions, a banner when it is broken or belongs
 * to an application, and two tabs, Overview and Logs. Its unit file is a page of its own (T6),
 * never on the same page as the logs or the delete action, which lives behind More actions.
 */

import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Cog, FileCode, Play, RotateCw, ScrollText, Square } from "lucide-react";
import { useState } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import type { LinkTab } from "../../app/LinkTabs";
import { DetailPage } from "../../components/page/DetailPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { serviceState } from "./data";
import { useServiceActions } from "./useServiceActions";
import { useServiceRecord } from "./useServiceRecord";

/** Where a service page sits: under the server, in its Services tab. */
export function serviceBreadcrumbs(t: T): { label: string; to: string }[] {
  return [
    { label: t("services.breadcrumbServer"), to: "/server" },
    { label: t("services.breadcrumbServices"), to: "/server/services" },
  ];
}

function NotFound({ name }: { name: string }) {
  const t = useT();
  const { node } = useNode();
  return (
    <DetailPage header={{ title: name, mono: true, breadcrumbs: serviceBreadcrumbs(t), server: node }}>
      <EmptyState
        variant="firstUse"
        level={2}
        icon={<Cog />}
        title={t("services.detail.notFoundTitle")}
        description={t("services.detail.notFoundDescription")}
        action={
          <Link to="/server/services" className={buttonClassName("secondary")}>
            {t("services.detail.allServices")}
          </Link>
        }
        command="noust service list"
      />
    </DetailPage>
  );
}

export function ServiceLayout({ name }: { name: string }) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const record = useServiceRecord(name);
  const { start, stop, restart, enable, disable, remove } = useServiceActions(name);
  const [confirmStop, setConfirmStop] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (record.notFound) return <NotFound name={name} />;

  const service = record.service;
  const view = service ? serviceState(service) : null;
  const managed = service !== undefined && !record.foreign;
  const tabs: readonly LinkTab[] = [
    { label: "services.tabs.overview", to: "/server/services/$name", params: { name }, exact: true },
    { label: "services.tabs.logs", to: "/server/services/$name/logs", params: { name } },
  ];

  const banner =
    record.app !== null ? (
      <Notice
        variant="banner"
        title={t("services.detail.appTitle", { domain: record.app })}
        action={
          <Link to="/apps/$domain" params={{ domain: record.app }} className={buttonClassName("secondary", "sm")}>
            {t("services.detail.openApp")}
          </Link>
        }
      >
        {t("services.detail.appDescription")}
      </Notice>
    ) : view?.state === "failed" ? (
      <Notice
        variant="banner"
        tone="error"
        title={t("services.detail.failedTitle")}
        action={
          <Link to="/server/services/$name/logs" params={{ name }} className={buttonClassName("secondary", "sm")}>
            {t("services.detail.readLogs")}
          </Link>
        }
      >
        {view.detail !== undefined ? t.rich("services.detail.failedResult", { result: <Mono>{view.detail}</Mono> }) : t("services.detail.failedNoResult")}
      </Notice>
    ) : record.foreign ? (
      <Notice variant="banner" title={t("services.detail.foreignTitle")}>
        {t("services.detail.foreignDescription")}
      </Notice>
    ) : record.error !== null ? (
      <ErrorBlock error={record.error} title={t("services.detail.loadFailed", { name })} onRetry={record.refetch} retrying={record.refetching} />
    ) : undefined;

  return (
    <>
      <DetailPage
        header={{
          title: name,
          mono: true,
          server: node,
          breadcrumbs: serviceBreadcrumbs(t),
          status: view !== null ? <StatusPill state={view.state} label={t(view.label)} /> : record.loading ? <Skeleton className="h-6 w-24 rounded-pill" /> : undefined,
          meta: service?.description ? (
            <Mono tone="muted" truncate>
              {service.description}
            </Mono>
          ) : record.loading ? (
            <Skeleton className="h-4 w-64 max-w-full" />
          ) : undefined,
          ...(managed
            ? {
                secondaryActions: (
                  <>
                    <Link to="/server/services/$name/unit" params={{ name }} className={buttonClassName("secondary")}>
                      <FileCode aria-hidden="true" className="size-icon-md" />
                      {t("services.actions.unitFile")}
                    </Link>
                    {service.active ? (
                      <Button icon={<Square aria-hidden="true" />} disabled={stop.isPending} onClick={() => setConfirmStop(true)}>
                        {t("services.actions.stop")}
                      </Button>
                    ) : (
                      <Button icon={<Play aria-hidden="true" />} loading={start.isPending} onClick={() => start.mutate()}>
                        {t("services.actions.start")}
                      </Button>
                    )}
                  </>
                ),
                primaryAction: (
                  <Button variant="primary" icon={<RotateCw aria-hidden="true" />} loading={restart.isPending} onClick={() => restart.mutate()}>
                    {t("services.actions.restart")}
                  </Button>
                ),
                overflow: (
                  <>
                    {service.enabled ? (
                      <MenuItem disabled={disable.isPending} onClick={() => disable.mutate()}>
                        {t("services.actions.disableAtBoot")}
                      </MenuItem>
                    ) : (
                      <MenuItem disabled={enable.isPending} onClick={() => enable.mutate()}>
                        {t("services.actions.enableAtBoot")}
                      </MenuItem>
                    )}
                    <MenuItem icon={<ScrollText />} onClick={() => void navigate({ to: "/server/logs", search: { unit: name } })}>
                      {t("services.actions.openInLogs")}
                    </MenuItem>
                    {record.app === null ? (
                      <>
                        <MenuSeparator />
                        <MenuItem destructive icon={<ICONS.delete />} onClick={() => setConfirmDelete(true)}>
                          {t("services.detail.deleteService")}
                        </MenuItem>
                      </>
                    ) : null}
                  </>
                ),
              }
            : {}),
        }}
        banner={banner}
        tabs={<LinkTabs label={t("services.tabs.label")} tabs={tabs} />}
      >
        <Outlet />
      </DetailPage>
      <ConfirmDialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        friction="simple"
        server={node}
        title={t("services.detail.stopTitle", { name })}
        description={t("services.detail.stopDescription")}
        actionLabel={t("services.detail.stopService")}
        onConfirm={() => {
          stop.mutate();
          return Promise.resolve();
        }}
      />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        server={node}
        title={t("services.detail.deleteDialogTitle", { name })}
        description={t("services.detail.deleteDescription")}
        confirmText={name}
        actionLabel={t("services.detail.deleteService")}
        onConfirm={async () => {
          await remove.mutateAsync();
          void navigate({ to: "/server/services" });
        }}
      />
    </>
  );
}
