import { useQuery, useQueries, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import { appDomainsQuery, bareDnsCheckQuery } from "../../api/queries/domains";
import type { AppDomainChange } from "../../api/queries/domains";
import { useFollowedJob } from "../../api/queries/jobs";
import { sitesQuery } from "../../api/queries/sites";
import { FilterBar } from "../../components/page/FilterBar";
import { GuardedButton, ReadOnlyLine, useCan } from "../../components/page/permissions";
import { ErrorBlock } from "../../components/page/QueryState";
import { RowMenu } from "../../components/page/RowMenu";
import { Button } from "../../components/ui/Button";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { FirstUse } from "../../components/ui/FirstUse";
import { ICONS } from "../../components/ui/icons";
import { MenuItem } from "../../components/ui/Menu";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useOnArrival } from "../../lib/useArrival";
import { AddDomainDialog } from "./AddDomainDialog";
import { CertificateStatus } from "./CertificateStatus";
import { certificateView } from "./certificates";
import { dnsCellOf } from "./dns";
import { domainRows, matchesDomain, roleOf } from "./domainRows";
import type { DomainRole, DomainRow } from "./domainRows";
import { DomainsListPage } from "./DomainsListPage";
import { JobBanner } from "./JobBanner";
import { isWildcard } from "./names";
import { spendNew } from "./search";
import type { SitesSearch } from "./search";
import { useCertificateRefresh } from "./useCertificateJobs";
import { useSiteMade } from "./useSiteActions";

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

/**
 * Where a name points, asked of DNS when its row is drawn and not before: a page of rows asks about its own names only,
 * so a server with hundreds does not send hundreds of questions at once. A name that is a wildcard has no address to ask.
 */
function Dns({ name }: { name: string }) {
  const t = useT();
  const wildcard = isWildcard(name);
  const check = useQuery({ ...bareDnsCheckQuery(name), staleTime: 120_000, retry: false, enabled: !wildcard });
  if (wildcard) return <EmptyCell reason={t("domains.domainsTab.dns.wildcard")} />;
  if (check.isError) return <span className="text-fg-muted">{t("domains.domainsTab.dns.failed")}</span>;
  if (check.data === undefined) return <Skeleton className="h-4 w-24" />;
  const cell = dnsCellOf(check.data);
  switch (cell.verdict) {
    case "here":
      return <StatusPill state="running" label={t("domains.domainsTab.dns.here")} appearance="inline" size="sm" />;
    case "cdn":
      return <StatusPill state="running" label={t("domains.domainsTab.dns.cdn", { cdn: cell.cdn })} appearance="inline" size="sm" />;
    case "unknown":
      return <span className="text-fg-muted">{t("domains.domainsTab.dns.unknown")}</span>;
    case "elsewhere":
      return <StatusPill state="warning" label={t("domains.domainsTab.dns.elsewhere")} appearance="inline" size="sm" />;
    case "missing":
      return <StatusPill state="warning" label={t("domains.domainsTab.dns.missing")} appearance="inline" size="sm" />;
  }
}

function roleWord(t: T, role: DomainRole): string {
  switch (role) {
    case "app":
      return t("domains.domainsTab.role.app");
    case "redirect":
      return t("domains.domainsTab.role.redirect");
    case "site":
      return t("domains.domainsTab.role.site");
  }
}

export interface DomainsPageProps {
  search: Pick<SitesSearch, "q" | "new">;
  onSearchChange: (search: Pick<SitesSearch, "q" | "new">, options?: { replace?: boolean }) => void;
}

/**
 * The Domains section: every name the server answers on, what each is (an application's, a redirect, a site of its own),
 * where it leads, whether it has HTTPS and where DNS points it. It is the one place to add a name: the dialog asks what it
 * is for, checks where it points and says what to change at the DNS provider. A name is changed where it leads, in its
 * application or in its site, which is one click from its row, so there is a single place where each is edited.
 */
export function DomainsPage({ search, onSearchChange }: DomainsPageProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const mayManage = useCan("apps.manage");
  const sites = useQuery(sitesQuery());
  const certs = useQuery(certsQuery());
  const apps = useQuery(appsQuery());
  const followed = useFollowedJob();
  useCertificateRefresh();
  const siteMade = useSiteMade();
  const [adding, setAdding] = useState(false);

  // Arriving with `?new` (the palette's "Add a name", Home's shortcut): the dialog opens, and the link is spent.
  useOnArrival(search.new === true, () => {
    setAdding(true);
    onSearchChange(spendNew(search), { replace: true });
  });

  const rows = useMemo(() => (sites.data === undefined ? [] : domainRows(sites.data.sites, certs.data?.certificates ?? [])), [sites.data, certs.data]);
  const shown = rows.filter((row) => matchesDomain(row, search.q ?? ""));
  const filtered = (search.q ?? "") !== "";

  // Which names are redirects is each application's own list to say: read once for every application that has a name here.
  const owners = useMemo(() => [...new Set(rows.flatMap((row) => (row.app === null ? [] : [row.app])))].sort(), [rows]);
  const lists = useQueries({ queries: owners.map((owner) => ({ ...appDomainsQuery(owner), staleTime: 60_000, retry: false })) });
  const redirects = useMemo(() => new Set(lists.flatMap((list) => (list.data?.domains ?? []).filter((entry) => entry.kind === "redirect").map((entry) => entry.domain))), [lists]);
  const canonical = useMemo(() => new Map(lists.flatMap((list) => (list.data === undefined ? [] : [[list.data.app, list.data.canonical] as const]))), [lists]);

  // The certificates only add each name's HTTPS: a list that cannot read them still lists the names, and says so.
  const loaded = sites.data !== undefined && (certs.data !== undefined || certs.isError);
  const empty = loaded && rows.length === 0;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  const failed = sites.isError && sites.data === undefined;
  const attention = rows.filter((row) => row.cert !== null && certificateView(row.cert, t.locale).attention).length;

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
    {
      id: "name",
      header: t("common.words.name"),
      card: "title",
      cell: (row) => <NameWithTags name={row.name} tags={[roleWord(t, roleOf(row, redirects))]} shown={1} />,
      sortValue: (row) => row.name,
    },
    {
      id: "leads",
      header: t("domains.domainsTab.leadsColumn"),
      card: "meta",
      hideBelow: "sm",
      cell: (row) =>
        row.app !== null ? (
          <TextLink to="/apps/$domain/domains" params={{ domain: row.app }} onClick={(event) => event.stopPropagation()} translate="no">
            {roleOf(row, redirects) === "redirect" ? (canonical.get(row.app) ?? row.app) : row.app}
          </TextLink>
        ) : (
          <TextLink to="/domains/sites/$site" params={{ site: row.site }} onClick={(event) => event.stopPropagation()} translate="no">
            {row.site}
          </TextLink>
        ),
      sortValue: (row) => row.app ?? row.site,
    },
    {
      id: "https",
      header: t("domains.domainsTab.httpsColumn"),
      card: "meta",
      width: "w-44",
      cell: (row) => <Https row={row} />,
      sortValue: (row) => (row.cert === null ? Number.POSITIVE_INFINITY : (row.cert.days_remaining ?? Number.POSITIVE_INFINITY)),
    },
    { id: "dns", header: t("domains.domainsTab.dnsColumn"), card: "status", width: "w-40", hideBelow: "sm", cell: (row) => <Dns name={row.name} /> },
  ];

  const addButton = (
    <GuardedButton permission="apps.manage" variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setAdding(true)}>
      {t("domains.domainsTab.add")}
    </GuardedButton>
  );

  let content;
  if (failed) {
    content = <ErrorBlock error={sites.error} title={t("domains.domainsTab.couldNotList")} onRetry={() => void sites.refetch()} retrying={sites.isRefetching} />;
  } else if (empty) {
    content = <FirstUse>{t("domains.domainsTab.emptyDescription")}</FirstUse>;
  } else {
    content = (
      <>
        {certs.isError ? <ErrorBlock compact error={certs.error} title={t("domains.domainsTab.certificatesFailed")} onRetry={() => void certs.refetch()} retrying={certs.isRefetching} /> : null}
        <DataTable
          pagination="domains"
          // The names have no bound (a preview's is as long as the application's and more): they share what the other columns leave and are cut, so DNS is always in view.
          layout="fixed"
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
              <MenuItem
                icon={<VERB_ICONS.refresh />}
                disabled={isWildcard(row.name)}
                onClick={() => void queryClient.invalidateQueries({ queryKey: bareDnsCheckQuery(row.name).queryKey })}
              >
                {t("domains.domainsTab.checkDns")}
              </MenuItem>
              <MenuItem icon={row.app !== null ? <OBJECT_ICONS.app /> : <OBJECT_ICONS.site />} onClick={() => open(row)}>
                {row.app !== null ? t("domains.domainsTab.openApp") : t("domains.domainsTab.openSite")}
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
      description={loaded && rows.length > 0 ? t(attention > 0 ? "domains.domainsTab.summaryAttention" : "domains.domainsTab.summary", { count: rows.length, attention }) : t("domains.sections.domains.description")}
      primaryAction={addButton}
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
      footer={
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {t.rich("domains.domainsTab.footer", {
            link: (
              <TextLink key="link" to="/network" size="inline">
                {t("domains.domainsTab.footerLink")}
              </TextLink>
            ),
          })}
        </p>
      }
    >
      <ReadOnlyLine permission="apps.manage" />
      {content}
      <AddDomainDialog
        app={null}
        apps={(apps.data?.apps ?? []).map((entry) => entry.domain)}
        open={adding && mayManage}
        onOpenChange={setAdding}
        onAdded={onAdded}
        webserver={sites.data?.webserver ?? "nginx"}
        onSiteMade={siteMade}
      />
    </DomainsListPage>
  );
}
