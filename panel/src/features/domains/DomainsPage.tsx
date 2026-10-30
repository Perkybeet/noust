import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import type { AppDomainChange } from "../../api/queries/domains";
import { useFollowedJob } from "../../api/queries/jobs";
import { sitesQuery } from "../../api/queries/sites";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button } from "../../components/ui/Button";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { useOnArrival } from "../../lib/useArrival";
import { AddDomainDialog } from "./AddDomainDialog";
import { CertificateStatus } from "./CertificateStatus";
import { certificateView } from "./certificates";
import { domainRows, matchesDomain } from "./domainRows";
import type { DomainRow } from "./domainRows";
import { DomainsListPage } from "./DomainsListPage";
import { JobBanner } from "./JobBanner";
import { spendNew } from "./search";
import type { SitesSearch } from "./search";
import { useCertificateRefresh } from "./useCertificateJobs";

/** What a name's HTTPS is, in the words of its certificate or, with none of the list, of its site. */
function Https({ row }: { row: DomainRow }) {
  const t = useT();
  if (row.cert !== null) {
    const view = certificateView(row.cert, t.locale);
    return <CertificateStatus tone={view.tone} label={view.label} />;
  }
  if (row.secure) return <CertificateStatus tone="https" label={t("domains.domainsTab.httpsElsewhere")} />;
  return <span className="text-fg-muted">{t("domains.domainsTab.noHttps")}</span>;
}

export interface DomainsPageProps {
  search: Pick<SitesSearch, "q" | "new">;
  onSearchChange: (search: Pick<SitesSearch, "q" | "new">, options?: { replace?: boolean }) => void;
}

/**
 * The Domains section: every name the server answers on, whose it is and whether it has HTTPS. It
 * is the place to put a name on an application and get HTTPS for it in one go: Add domain asks which
 * application and the name, checks where the name points, and the certificate follows. What is behind
 * a name is a click away: the application's own Domains section, or the site's configuration.
 */
export function DomainsPage({ search, onSearchChange }: DomainsPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const sites = useQuery(sitesQuery());
  const certs = useQuery(certsQuery());
  const apps = useQuery(appsQuery());
  const followed = useFollowedJob();
  useCertificateRefresh();
  const [adding, setAdding] = useState(false);

  // Arriving with `?new` (the palette's "Add a domain"): the dialog opens, and the link is spent.
  useOnArrival(search.new === true, () => {
    setAdding(true);
    onSearchChange(spendNew(search), { replace: true });
  });

  const rows = useMemo(() => (sites.data === undefined ? [] : domainRows(sites.data.sites, certs.data?.certificates ?? [])), [sites.data, certs.data]);
  const shown = rows.filter((row) => matchesDomain(row, search.q ?? ""));
  const filtered = (search.q ?? "") !== "";
  // The certificates only add each name's HTTPS: a list that cannot read them still lists the names, and says so.
  const loaded = sites.data !== undefined && (certs.data !== undefined || certs.isError);
  const empty = loaded && rows.length === 0;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  const failed = sites.isError && sites.data === undefined;

  const open = (row: DomainRow): void => {
    if (row.app !== null) void navigate({ to: "/apps/$domain/domains", params: { domain: row.app } });
    else void navigate({ to: "/domains/sites/$site", params: { site: row.site } });
  };

  const onAdded = (change: AppDomainChange, name: string, app: string): void => {
    if (change.certificate_job_id) followed.follow(change.certificate_job_id);
    // The row appears and the certificate job is on the page: only a name served without HTTPS has something to say.
    if (!change.certificate_job_id && !change.tls) toast.warning(t("domains.domainsTab.addedPlain", { name, app }), { description: t("domains.appTab.plainHttpNoCertificate") });
  };

  const columns: Column<DomainRow>[] = [
    { id: "name", header: t("domains.domainsTab.nameColumn"), cell: (row) => <span translate="no">{row.name}</span>, sortValue: (row) => row.name },
    {
      id: "https",
      header: t("domains.domainsTab.httpsColumn"),
      card: "status",
      width: "w-56",
      cell: (row) => <Https row={row} />,
      sortValue: (row) => (row.cert === null ? Number.POSITIVE_INFINITY : (row.cert.days_remaining ?? Number.POSITIVE_INFINITY)),
    },
    {
      id: "app",
      header: t("domains.domainsTab.appColumn"),
      card: "meta",
      hideBelow: "sm",
      cell: (row) =>
        row.app !== null ? (
          <TextLink to="/apps/$domain" params={{ domain: row.app }} onClick={(event) => event.stopPropagation()} translate="no">
            {row.app}
          </TextLink>
        ) : (
          <EmptyCell reason={t("domains.sitesTab.noApp")} />
        ),
      sortValue: (row) => row.app ?? "",
    },
    {
      id: "site",
      header: t("domains.domainsTab.siteColumn"),
      card: "hidden",
      hideBelow: "lg",
      cell: (row) => <TextLink to="/domains/sites/$site" params={{ site: row.site }} onClick={(event) => event.stopPropagation()} translate="no">{row.site}</TextLink>,
      sortValue: (row) => row.site,
    },
  ];

  const addButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
      {t("domains.domainsTab.add")}
    </Button>
  );

  let content;
  if (failed) {
    content = <ErrorBlock error={sites.error} title={t("domains.domainsTab.couldNotList")} onRetry={() => void sites.refetch()} retrying={sites.isRefetching} />;
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<OBJECT_ICONS.domain />}
        title={t("domains.domainsTab.emptyTitle")}
        description={t("domains.domainsTab.emptyDescription")}
        action={addButton("primary")}
        command="noust domain add shop.example.com www.shop.example.com"
      />
    );
  } else {
    content = (
      <>
        {certs.isError ? <ErrorBlock compact error={certs.error} title={t("domains.domainsTab.certificatesFailed")} onRetry={() => void certs.refetch()} retrying={certs.isRefetching} /> : null}
      <DataTable
        pagination="domains"
        caption={filtered ? t("domains.domainsTab.tableCaptionFiltered") : t("domains.domainsTab.tableCaption")}
        columns={columns}
        rows={shown}
        getRowId={(row) => row.name}
        loading={!loaded}
        onRowActivate={open}
        empty={
          <EmptyState
            variant="inline"
            title={t("domains.domainsTab.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                {t("common.words.clearFilters")}
              </Button>
            }
          />
        }
        rowActions={(row) => (
          <RowMenu name={row.name}>
            {row.app !== null ? (
              <MenuItem icon={<OBJECT_ICONS.app />} onClick={() => void navigate({ to: "/apps/$domain/domains", params: { domain: row.app ?? "" } })}>
                {t("domains.domainsTab.openApp")}
              </MenuItem>
            ) : null}
            <MenuItem icon={<OBJECT_ICONS.site />} onClick={() => void navigate({ to: "/domains/sites/$site", params: { site: row.site } })}>
              {t("domains.domainsTab.openSite")}
            </MenuItem>
          </RowMenu>
        )}
      />
      </>
    );
  }

  return (
    <DomainsListPage
      section="domains"
      // While there is no name the empty state carries the action, so the page has it once.
      {...(empty || failed ? {} : { primaryAction: addButton("primary") })}
      {...(followed.id !== null
        ? { notice: <JobBanner followed={followed} words={{ running: t("domains.domainsTab.job.running"), done: t("domains.domainsTab.job.done"), failed: t("domains.domainsTab.job.failed") }} /> }
        : {})}
      {...(empty || failed
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("domains.domainsTab.filterAriaLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => onSearchChange(value === "" ? {} : { q: value }, { replace: true }),
                  label: t("domains.domainsTab.filterByNameAriaLabel"),
                  placeholder: t("domains.filterByNamePlaceholder"),
                }}
                {...(loaded ? { count: filtered ? t("domains.domainsTab.countFiltered", { shown: shown.length, total: rows.length }) : t("domains.domainsTab.count", { count: rows.length }) } : {})}
              />
            ),
          })}
      {...(empty || failed ? {} : { footer: <CommandHint command="noust domain list shop.example.com" label={t("common.words.fromTerminal")} /> })}
    >
      {content}
      <AddDomainDialog app={null} apps={(apps.data?.apps ?? []).map((entry) => entry.domain)} open={adding} onOpenChange={setAdding} onAdded={onAdded} />
    </DomainsListPage>
  );
}
