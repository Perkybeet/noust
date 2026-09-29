import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FileCode, Globe, Lock, MoreHorizontal, Play, Plus, RotateCw, Search, Square, Trash2, X } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { sitesQuery } from "../../api/queries/sites";
import type { SiteEntry } from "../../api/queries/sites";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { CreateSiteDialog } from "./CreateSiteDialog";
import { truncatedNames } from "./names";
import { useSiteActions } from "./useSiteActions";

/** Whether a site serves HTTPS, as an attribute of it rather than a state: no colour. */
export function Tls({ secure }: { secure: boolean }) {
  const t = useT();
  return secure ? (
    <span className="inline-flex items-center gap-1.5 text-fg">
      <Lock aria-hidden="true" className="size-3.5 text-fg-muted" />
      {t("domains.sitesTab.https")}
    </span>
  ) : (
    <span className="text-fg-muted">{t("domains.sitesTab.httpOnly")}</span>
  );
}

export function SiteState({ enabled }: { enabled: boolean }) {
  const t = useT();
  return (
    <StatusPill
      state={enabled ? "running" : "stopped"}
      label={enabled ? t("domains.sitesTab.enabled") : t("domains.sitesTab.disabled")}
      appearance="inline"
      size="sm"
    />
  );
}

/** The names a site's configuration answers on, mono and joined, truncated past three with a
 * "+N more" tail; the full list is always in the title. */
function ServedNames({ names }: { names: readonly string[] }) {
  const t = useT();
  if (names.length === 0) return <span className="text-fg-faint">{t("domains.unknown")}</span>;
  const { shown, rest } = truncatedNames(names);
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={names.join(", ")}>
      <span translate="no" className="mono truncate text-12 text-fg-muted">
        {shown}
      </span>
      {rest > 0 ? <span className="shrink-0 text-12 text-fg-faint">{t("domains.moreCount", { count: rest })}</span> : null}
    </span>
  );
}

/**
 * Every web server site on this machine: which server serves it, whether it is enabled and
 * serves HTTPS, and where its file is. A site opens its configuration editor.
 */
export function SitesTab() {
  const t = useT();
  const navigate = useNavigate();
  const sites = useQuery(sitesQuery());
  const { enable, disable, reload, refresh } = useSiteActions();
  const [filter, setFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<SiteEntry | null>(null);

  const all = sites.data?.sites ?? [];
  const webserver = sites.data?.webserver ?? "nginx";
  const needle = filter.trim().toLowerCase();
  const shown = needle === "" ? all : all.filter((site) => site.name.includes(needle));
  const open = (site: SiteEntry): void => {
    void navigate({ to: "/domains/sites/$site", params: { site: site.name } });
  };

  const columns: Column<SiteEntry>[] = [
    {
      id: "name",
      header: t("domains.sitesTab.siteColumn"),
      cell: (site) => (
        <span translate="no" className="whitespace-nowrap">
          {site.name}
        </span>
      ),
      sortValue: (site) => site.name,
    },
    { id: "server", header: t("domains.sitesTab.serverColumn"), mono: true, hideBelow: "sm", cell: (site) => site.webserver, sortValue: (site) => site.webserver },
    { id: "state", header: t("domains.sitesTab.stateColumn"), cell: (site) => <SiteState enabled={site.enabled} />, sortValue: (site) => (site.enabled ? 0 : 1) },
    { id: "tls", header: t("domains.sitesTab.servesColumn"), hideBelow: "md", cell: (site) => <Tls secure={site.has_ssl} />, sortValue: (site) => (site.has_ssl ? 0 : 1) },
    // The file each site lives in is on the site's own page: here the width goes to the names.
    { id: "names", header: t("domains.sitesTab.namesColumn"), hideBelow: "lg", cell: (site) => <ServedNames names={site.server_names} /> },
  ];

  const createButton = (
    <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setCreating(true)}>
      {t("domains.createSite")}
    </Button>
  );

  return (
    <div className="flex flex-col gap-4">
      {sites.isError && sites.data === undefined ? (
        <ErrorBlock error={sites.error} title={t("domains.sitesTab.couldNotListSites")} onRetry={() => void sites.refetch()} retrying={sites.isRefetching} />
      ) : sites.data !== undefined && all.length === 0 ? (
        <EmptyState
          level={3}
          icon={<Globe />}
          title={t("domains.sitesTab.noSitesYetTitle")}
          description={t("domains.sitesTab.noSitesYetDescription")}
          action={createButton}
          command="noust site create --domain example.com"
          className="py-16"
        />
      ) : (
        <>
          <div role="search" aria-label={t("domains.sitesTab.filterAriaLabel")} className="flex flex-wrap items-center gap-2">
            <Input
              type="search"
              aria-label={t("domains.sitesTab.filterByNameAriaLabel")}
              placeholder={t("domains.filterByNamePlaceholder")}
              value={filter}
              onValueChange={(value: string) => setFilter(value)}
              icon={<Search />}
              className="w-full sm:w-64"
              autoComplete="off"
              spellCheck={false}
            />
            {filter !== "" ? (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => setFilter("")}>
                {t("domains.clear")}
              </Button>
            ) : null}
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <Button icon={<RotateCw aria-hidden="true" />} loading={reload.isPending} onClick={() => reload.mutate()}>
                {t("domains.sitesTab.testAndReload", { webserver })}
              </Button>
              {createButton}
            </div>
          </div>
          <DataTable
            caption={needle === "" ? t("domains.sitesTab.tableCaption") : t("domains.sitesTab.tableCaptionFiltered")}
            columns={columns}
            rows={shown}
            getRowId={(site) => `${site.webserver}:${site.name}`}
            loading={sites.isPending}
            onRowActivate={open}
            empty={
              <EmptyState
                title={t("domains.sitesTab.noSiteMatchTitle")}
                description={t("domains.sitesTab.noSiteMatchDescription", { filter: filter.trim() })}
                className="border-0 py-8"
              />
            }
            rowActions={(site) => (
              <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: site.name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}>
                <MenuItem icon={<FileCode />} onClick={() => open(site)}>
                  {t("domains.sitesTab.editConfiguration")}
                </MenuItem>
                {site.enabled ? (
                  <MenuItem icon={<Square />} disabled={disable.isPending} onClick={() => disable.mutate(site.name)}>
                    {t("domains.sitesTab.disableAction")}
                  </MenuItem>
                ) : (
                  <MenuItem icon={<Play />} disabled={enable.isPending} onClick={() => enable.mutate(site.name)}>
                    {t("domains.sitesTab.enableAction")}
                  </MenuItem>
                )}
                <MenuSeparator />
                <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(site)}>
                  {t("domains.delete")}
                </MenuItem>
              </Menu>
            )}
          />
          {/* Drawn with the rows, not before: under a list of unknown length it would only be
              pushed down the page when they arrive. */}
          {sites.isPending ? null : <CommandHint command="noust site list" label={t("domains.fromTerminal")} />}
        </>
      )}

      <CreateSiteDialog
        open={creating}
        onOpenChange={setCreating}
        detected={webserver}
        onCreated={(site, message) => {
          refresh(site);
          if (message.includes("TLS was requested but not enabled")) {
            toast.warning(t("domains.sitesTab.createdToastWithoutHttps", { site }), { detail: message });
          } else {
            toast.success(t("domains.sitesTab.createdToast", { site }), { description: message });
          }
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        title={deleting ? t("domains.deleteNamedTitle", { name: deleting.name }) : t("domains.deleteSite")}
        description={t("domains.deleteSiteDescription")}
        confirmText={deleting?.name ?? ""}
        actionLabel={t("domains.deleteSite")}
        onConfirm={async () => {
          if (!deleting) return;
          await request("delete", "/api/sites/{domain}", { params: { domain: deleting.name } });
          refresh(deleting.name);
          toast.success(t("domains.sitesTab.deletedToast", { site: deleting.name }));
        }}
      />
    </div>
  );
}
