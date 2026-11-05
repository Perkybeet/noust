import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Lock, Play, RotateCw, Square } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { sitesQuery } from "../../api/queries/sites";
import type { SiteEntry } from "../../api/queries/sites";
import { FilterBar } from "../../components/page/FilterBar";
import { GuardedButton, ReadOnlyLine, useCan } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useOnArrival } from "../../lib/useArrival";
import { ownerColumns, useOwnerScope } from "../teams/ownership";
import { useOrganization } from "../teams/useOrganization";
import { AddDomainDialog } from "./AddDomainDialog";
import { DomainsListPage } from "./DomainsListPage";
import { spendNew } from "./search";
import type { SitesSearch } from "./search";
import { UnknownHostsCard } from "./UnknownHostsCard";
import { useSiteActions, useSiteMade } from "./useSiteActions";
import { VERB_ICONS } from "../../components/ui/objectIcons";

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

export interface SitesPageProps {
  search: SitesSearch;
  onSearchChange: (search: SitesSearch, options?: { replace?: boolean }) => void;
}

/**
 * The Sites section: the web server's file for each name, whether it serves and over HTTPS, the other names it answers
 * on and who wrote it. A site opens its configuration. Every application gets one when it is deployed; a site of its
 * own answers a name that is not an application, and is made from the dialog that adds a name, where the sites stay
 * (they are a resource a team sees, and the Server area is not theirs).
 */
export function SitesPage({ search, onSearchChange }: SitesPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sites = useQuery(sitesQuery());
  const apps = useQuery(appsQuery());
  // What answers an unknown name is the server's own default, so it is the server's to read.
  const server = useOrganization().can("server.read");
  const mayManage = useCan("apps.manage");
  const mayOperate = useCan("apps.operate");
  const { enable, disable, reload, refresh } = useSiteActions();
  const siteMade = useSiteMade();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<SiteEntry | null>(null);

  // Arriving with `?new` (the palette's "New site"): the dialog opens, and the link is spent.
  const opening = search.new === true;
  useOnArrival(opening, () => {
    setCreating(true);
    onSearchChange(spendNew(search), { replace: true });
  });

  const everything = sites.data?.sites ?? [];
  // The team the operator works in narrows the list, and the project and environment filters narrow it further.
  const owner = useOwnerScope(everything, search);
  const all = owner.inTeam;
  const webserver = sites.data?.webserver ?? "nginx";
  const needle = (search.q ?? "").toLowerCase();
  const narrowed = owner.shown;
  const shown = needle === "" ? narrowed : narrowed.filter((site) => site.name.includes(needle) || site.server_names.some((name) => name.includes(needle)));
  const filtered = needle !== "" || owner.filtered;
  const empty = sites.data !== undefined && everything.length === 0;
  // The list is still being drawn (the sites, or the team they are narrowed to): what goes under it is drawn once it has its height.
  const listing = sites.isPending || owner.loading;
  const open = (site: SiteEntry): void => {
    void navigate({ to: "/domains/sites/$site", params: { site: site.name } });
  };
  const written = all.filter((site) => !site.noust_managed).length;

  const columns: Column<SiteEntry>[] = [
    {
      id: "name",
      header: t("domains.sitesTab.siteColumn"),
      card: "title",
      // The other names it answers on follow the name on its line, with "+N" for the rest: the row is one height.
      // The name is the link that opens the site: the row is not itself a button, because "+N" is a button of its own.
      cell: (site) => (
        <NameWithTags
          name={
            <TextLink to="/domains/sites/$site" params={{ site: site.name }} variant="row" translate="no">
              {site.name}
            </TextLink>
          }
          tags={site.server_names.filter((name) => name !== site.name)}
          shown={1}
        />
      ),
      sortValue: (site) => site.name,
    },
    {
      id: "app",
      header: t("domains.sitesTab.isOfColumn"),
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
    ...ownerColumns<SiteEntry>(t, owner.scope, owner.labels),
    {
      id: "writtenBy",
      header: t("domains.sitesTab.writtenByColumn"),
      width: "w-44",
      hideBelow: "sm",
      card: "meta",
      // An attribute, not a state: neutral. The file's name, when it is not the domain, follows on the same line.
      cell: (site) => (
        <span className="flex min-w-0 items-center gap-2">
          <span className={site.noust_managed ? "text-fg" : "font-medium text-fg"}>{site.noust_managed ? t("domains.sitesTab.writtenByNoust") : t("domains.sitesTab.writtenByHand")}</span>
          {site.site_name !== site.name ? (
            <Mono tone="faint" truncate title={site.config_path}>
              {site.site_name}
            </Mono>
          ) : null}
        </span>
      ),
      sortValue: (site) => (site.noust_managed ? 0 : 1),
    },
    { id: "tls", header: t("domains.sitesTab.servesColumn"), width: "w-32", hideBelow: "lg", card: "hidden", cell: (site) => <Tls secure={site.has_ssl} />, sortValue: (site) => (site.has_ssl ? 0 : 1) },
    {
      id: "state",
      header: t("domains.sitesTab.stateColumn"),
      width: "w-48",
      card: "status",
      cell: (site) => (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <SiteState enabled={site.enabled} />
          {site.pending_reload ? <StatusPill state="warning" size="sm" appearance="inline" label={t("domains.siteHistory.pendingBadge")} /> : null}
          {site.edited_outside ? <StatusPill state="warning" size="sm" appearance="inline" label={t("domains.siteHistory.editedBadge")} /> : null}
        </span>
      ),
      sortValue: (site) => (site.enabled ? 1 : 0),
    },
  ];

  const createButton = (
    <GuardedButton permission="apps.manage" variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreating(true)}>
      {t("domains.createSite")}
    </GuardedButton>
  );

  let content;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (sites.isError && sites.data === undefined) {
    content = <ErrorBlock error={sites.error} title={t("domains.sitesTab.couldNotListSites")} onRetry={() => void sites.refetch()} retrying={sites.isRefetching} />;
  } else if (empty) {
    content = <FirstUse>{t("domains.sitesTab.noSitesYetDescription")}</FirstUse>;
  } else {
    content = (
      <DataTable
        pagination="sites"
        caption={filtered ? t("domains.sitesTab.tableCaptionFiltered") : t("domains.sitesTab.tableCaption")}
        columns={columns}
        rows={shown}
        getRowId={(site) => `${site.webserver}:${site.name}`}
        loading={sites.isPending || owner.loading}
        empty={
          <EmptyState
            variant="inline"
            title={t("domains.sitesTab.noSiteMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                {t("common.words.clearFilters")}
              </Button>
            }
          />
        }
        rowActions={(site) => (
          <RowMenu name={site.name}>
            <MenuItem icon={<VERB_ICONS.edit />} onClick={() => open(site)}>
              {t("domains.sitesTab.editConfiguration")}
            </MenuItem>
            {mayManage ? (
              <>
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
                  {t("common.words.delete")}
                </MenuItem>
              </>
            ) : null}
          </RowMenu>
        )}
      />
    );
  }

  return (
    <DomainsListPage
      section="sites"
      description={sites.data !== undefined && !empty ? t(written > 0 ? "domains.sitesTab.summaryByHand" : "domains.sitesTab.summary", { count: all.length, written }) : t("domains.sections.sites.description")}
      {...(empty || !mayOperate
        ? {}
        : {
            secondaryActions: (
              <Button icon={<RotateCw aria-hidden="true" />} loading={reload.isPending} onClick={() => reload.mutate()}>
                {t("domains.sitesTab.testAndReload", { webserver })}
              </Button>
            ),
          })}
      primaryAction={createButton}
      {...(empty
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("domains.sitesTab.filterAriaLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => onSearchChange({ ...(value === "" ? {} : { q: value }), ...(search.project !== undefined ? { project: search.project } : {}), ...(search.environment !== undefined ? { environment: search.environment } : {}) }, { replace: true }),
                  label: t("domains.sitesTab.filterByNameAriaLabel"),
                  placeholder: t("domains.filterByNamePlaceholder"),
                }}
                filters={owner.filters(search, (patch) => onSearchChange({ ...(search.q !== undefined ? { q: search.q } : {}), ...(patch.project !== undefined ? { project: patch.project } : {}), ...(patch.environment !== undefined ? { environment: patch.environment } : {}) }))}
                {...(sites.data !== undefined
                  ? {
                      count: !filtered
                        ? t("domains.sitesTab.count", { count: all.length, webserver })
                        : t("domains.sitesTab.countFiltered", { shown: shown.length, total: all.length }),
                    }
                  : {})}
              />
            ),
          })}
    >
      <ReadOnlyLine permission="apps.manage" />
      {content}
      {/* After the list: what answers an unknown name is read once in a while, and must not push the sites down. */}
      {empty || !server || listing ? null : <UnknownHostsCard />}

      <AddDomainDialog
        app={null}
        apps={(apps.data?.apps ?? []).map((entry) => entry.domain)}
        open={creating && mayManage}
        onOpenChange={setCreating}
        initialPurpose="site"
        webserver={webserver}
        onAdded={() => void queryClient.invalidateQueries({ queryKey: sitesQuery().queryKey })}
        onSiteMade={siteMade}
      />
      <ConfirmDialog
        friction="type"
        open={deleting !== null}
        onOpenChange={(next) => {
          if (!next) setDeleting(null);
        }}
        title={deleting ? t("common.words.deleteNamed", { name: deleting.name }) : t("domains.deleteSite")}
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
