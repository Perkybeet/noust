import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { FileCode, Globe, Lock, Play, RotateCw, Square } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { sitesQuery } from "../../api/queries/sites";
import type { SiteEntry } from "../../api/queries/sites";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { CreateSiteDialog } from "./CreateSiteDialog";
import { DomainsListPage } from "./DomainsListPage";
import { truncatedNames } from "./names";
import type { SitesSearch } from "./search";
import { useSiteActions } from "./useSiteActions";

/** Whether a site serves HTTPS, as an attribute of it rather than a state: no colour. */
export function Tls({ secure }: { secure: boolean }) {
  const t = useT();
  return secure ? (
    <span className="inline-flex items-center gap-1.5 text-fg">
      <Lock aria-hidden="true" className="size-icon-sm text-fg-muted" />
      {t("domains.sitesTab.https")}
    </span>
  ) : (
    <span className="text-fg-muted">{t("domains.sitesTab.httpOnly")}</span>
  );
}

/** A site the web server answers with is serving; a disabled one is stopped on purpose. */
export function SiteState({ enabled, size = "sm" }: { enabled: boolean; size?: "sm" | "md" }) {
  const t = useT();
  return (
    <StatusPill
      state={enabled ? "running" : "stopped"}
      label={enabled ? t("domains.sitesTab.serving") : t("domains.sitesTab.disabled")}
      appearance={size === "sm" ? "inline" : "pill"}
      size={size}
    />
  );
}

/** The names a site's configuration answers on, mono and joined, truncated past three with a
 * "+N more" tail; the full list is always in the title. */
function ServedNames({ names }: { names: readonly string[] }) {
  const t = useT();
  if (names.length === 0) return <span className="text-fg-muted">{t("domains.unknown")}</span>;
  const { shown, rest } = truncatedNames(names);
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={names.join(", ")}>
      <Mono tone="muted" truncate>
        {shown}
      </Mono>
      {rest > 0 ? <span className="shrink-0 text-12 text-fg-muted">{t("domains.moreCount", { count: rest })}</span> : null}
    </span>
  );
}

export interface SitesPageProps {
  search: SitesSearch;
  onSearchChange: (search: SitesSearch, options?: { replace?: boolean }) => void;
}

/**
 * The Web server sites tab: every site the web server has, whether it serves and over HTTPS,
 * and the names it answers on. A site opens its configuration editor. Every application gets
 * one when it is deployed; the ones created here answer a name that is not an application.
 */
export function SitesPage({ search, onSearchChange }: SitesPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const sites = useQuery(sitesQuery());
  const { enable, disable, reload, refresh } = useSiteActions();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<SiteEntry | null>(null);

  const all = sites.data?.sites ?? [];
  const webserver = sites.data?.webserver ?? "nginx";
  const needle = (search.q ?? "").toLowerCase();
  const shown = needle === "" ? all : all.filter((site) => site.name.includes(needle) || site.server_names.some((name) => name.includes(needle)));
  const empty = sites.data !== undefined && all.length === 0;
  const open = (site: SiteEntry): void => {
    void navigate({ to: "/domains/sites/$site", params: { site: site.name } });
  };

  const columns: Column<SiteEntry>[] = [
    { id: "name", header: t("domains.sitesTab.siteColumn"), cell: (site) => <span translate="no">{site.name}</span>, sortValue: (site) => site.name },
    {
      id: "state",
      header: t("domains.sitesTab.stateColumn"),
      width: "w-32",
      card: "status",
      cell: (site) => <SiteState enabled={site.enabled} />,
      sortValue: (site) => (site.enabled ? 1 : 0),
    },
    { id: "tls", header: t("domains.sitesTab.servesColumn"), width: "w-32", cell: (site) => <Tls secure={site.has_ssl} />, sortValue: (site) => (site.has_ssl ? 0 : 1) },
    { id: "names", header: t("domains.sitesTab.namesColumn"), hideBelow: "md", card: "hidden", cell: (site) => <ServedNames names={site.server_names} /> },
    {
      id: "writtenBy",
      header: t("domains.sitesTab.writtenByColumn"),
      width: "w-32",
      hideBelow: "sm",
      card: "meta",
      // An attribute, not a state: neutral, with the file's name when it is not the domain.
      cell: (site) => (
        <span className="flex min-w-0 flex-col">
          <span className={site.noust_managed ? "text-fg" : "text-fg-muted"}>{site.noust_managed ? t("domains.sitesTab.writtenByNoust") : t("domains.sitesTab.writtenByHand")}</span>
          {site.site_name !== site.name ? (
            <Mono tone="faint" truncate title={site.config_path}>
              {site.site_name}
            </Mono>
          ) : null}
        </span>
      ),
      sortValue: (site) => (site.noust_managed ? 0 : 1),
    },
    {
      id: "app",
      header: t("domains.sitesTab.appColumn"),
      hideBelow: "md",
      card: "meta",
      cell: (site) =>
        site.app ? (
          <TextLink to="/apps/$domain" params={{ domain: site.app }} onClick={(event) => event.stopPropagation()}>
            <Mono>{site.app}</Mono>
          </TextLink>
        ) : (
          <EmptyCell reason={t("domains.sitesTab.noApp")} />
        ),
      sortValue: (site) => site.app ?? "",
    },
  ];

  const createButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreating(true)}>
      {t("domains.createSite")}
    </Button>
  );

  let content;
  if (sites.isError && sites.data === undefined) {
    content = <ErrorBlock error={sites.error} title={t("domains.sitesTab.couldNotListSites")} onRetry={() => void sites.refetch()} retrying={sites.isRefetching} />;
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<Globe />}
        title={t("domains.sitesTab.noSitesYetTitle")}
        description={t("domains.sitesTab.noSitesYetDescription")}
        action={createButton("secondary")}
        command="noust site create --domain example.com"
      />
    );
  } else {
    content = (
      <DataTable
        mobile="cards"
        caption={needle === "" ? t("domains.sitesTab.tableCaption") : t("domains.sitesTab.tableCaptionFiltered")}
        columns={columns}
        rows={shown}
        getRowId={(site) => `${site.webserver}:${site.name}`}
        loading={sites.isPending}
        onRowActivate={open}
        empty={
          <EmptyState
            variant="inline"
            title={t("domains.sitesTab.noSiteMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                {t("domains.clearFilters")}
              </Button>
            }
          />
        }
        rowActions={(site) => (
          <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: site.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
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
            <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(site)}>
              {t("domains.delete")}
            </MenuItem>
          </Menu>
        )}
      />
    );
  }

  return (
    <DomainsListPage
      {...(empty
        ? {}
        : {
            secondaryActions: (
              <Button icon={<RotateCw aria-hidden="true" />} loading={reload.isPending} onClick={() => reload.mutate()}>
                {t("domains.sitesTab.testAndReload", { webserver })}
              </Button>
            ),
          })}
      primaryAction={createButton("primary")}
      {...(empty
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("domains.sitesTab.filterAriaLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => onSearchChange(value === "" ? {} : { q: value }, { replace: true }),
                  label: t("domains.sitesTab.filterByNameAriaLabel"),
                  placeholder: t("domains.filterByNamePlaceholder"),
                }}
                {...(sites.data !== undefined
                  ? {
                      count:
                        needle === ""
                          ? t("domains.sitesTab.count", { count: all.length, webserver })
                          : t("domains.sitesTab.countFiltered", { shown: shown.length, total: all.length }),
                    }
                  : {})}
              />
            ),
          })}
      {...(empty ? {} : { footer: <CommandHint command="noust site list" label={t("domains.fromTerminal")} /> })}
    >
      {content}

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
    </DomainsListPage>
  );
}
