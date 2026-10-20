import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, Globe, MoreHorizontal, Plug, Plus, Power, RotateCw, Search, Trash2 } from "lucide-react";
import { useState } from "react";

import { request } from "../../api/client";
import { certsQuery } from "../../api/queries/certs";
import { appDomainsQuery, dnsCheckQuery, domainKeys } from "../../api/queries/domains";
import type { AppDomain, AppDomainChange, AppDomainList } from "../../api/queries/domains";
import { activeJobsQuery, isJobFinished, useFollowedJob } from "../../api/queries/jobs";
import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { findCertificate } from "../app/lookups";
import { reportActionError } from "../apps/useAppActions";
import { AddDomainDialog } from "./AddDomainDialog";
import { AppDnsSection } from "./AppDnsSection";
import { CanonicalSection } from "./CanonicalSection";
import { CertificateSourceCard } from "./CertificateSourceCard";
import { CertificateStatus } from "./CertificateStatus";
import { DomainPortDialog } from "./DomainPortDialog";
import { DnsVerdict } from "./DnsVerdict";
import { certificateJobFor, certificateOf, certificateView, coverageOf, covers, issuerName } from "./certificates";
import { JobBanner } from "./JobBanner";
import { useCertificateRefresh } from "./useCertificateJobs";

function kindLabel(t: T, kind: string): string {
  if (kind === "primary") return t("domains.kindPrimary");
  if (kind === "alias") return t("domains.kindAlias");
  if (kind === "redirect") return t("domains.kindRedirect");
  return kind;
}

function Role({ entry, app }: { entry: AppDomain; app: string }) {
  const t = useT();
  if (entry.kind !== "redirect") {
    return (
      <span className="inline-flex min-w-0 items-center gap-1.5">
        <Badge>{kindLabel(t, entry.kind)}</Badge>
        {entry.temporary ? <Badge tone="warn">{t("domains.temporary.badge")}</Badge> : null}
        {!entry.enabled ? <Badge>{t("domains.appTab.switchedOffBadge")}</Badge> : null}
        <OwnCertificateBadge entry={entry} />
        {entry.port ? (
          <Mono tone="muted">
            <span className="sr-only">{t("network.port.goesToSr", { port: entry.port })}</span>
            <span aria-hidden="true">{`:${String(entry.port)}`}</span>
          </Mono>
        ) : null}
      </span>
    );
  }
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <Badge>{t("domains.kindRedirect")}</Badge>
      {!entry.enabled ? <Badge>{t("domains.appTab.switchedOffBadge")}</Badge> : null}
      <OwnCertificateBadge entry={entry} />
      <ArrowRight aria-hidden="true" className="size-3.5 shrink-0 text-fg-faint" />
      <span className="sr-only">{t("domains.appTab.roleToSr")}</span>
      <span translate="no" className="truncate text-13 text-fg-muted">
        {app}
      </span>
    </span>
  );
}

/** Says that a name holds a certificate of its own, and that it is not issued yet when it is not. */
function OwnCertificateBadge({ entry }: { entry: AppDomain }) {
  const t = useT();
  if (entry.certificate !== "own") return null;
  // An attribute of the name, in words; the state (served without HTTPS until it is issued) is the
  // certificate column's.
  return <Badge>{t(entry.certificate_pending ? "domains.appTab.ownCertificatePendingBadge" : "domains.appTab.ownCertificateBadge")}</Badge>;
}

function DnsDialog({ app, name, onClose }: { app: string; name: string | null; onClose: () => void }) {
  const t = useT();
  const dns = useQuery({ ...dnsCheckQuery(app, name ?? ""), enabled: name !== null });
  return (
    <Dialog
      open={name !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={name === null ? t("domains.appTab.dnsDialogTitleDefault") : t("domains.appTab.dnsDialogTitle", { name })}
      description={t("domains.appTab.dnsDialogDescription")}
      footer={
        <>
          <Button icon={<RotateCw aria-hidden="true" />} loading={dns.isFetching} onClick={() => void dns.refetch()}>
            {t("domains.checkAgain")}
          </Button>
          <Button variant="primary" onClick={onClose}>
            {t("domains.appTab.done")}
          </Button>
        </>
      }
    >
      <div aria-live="polite">
        {dns.data ? (
          <DnsVerdict check={dns.data} />
        ) : dns.isError ? (
          <ErrorBlock compact error={dns.error} title={t("domains.couldNotResolve", { name: name ?? "" })} />
        ) : (
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">{t("domains.checkingDns")}</span>
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-12" />
          </div>
        )}
      </div>
    </Dialog>
  );
}

function TableSkeleton() {
  return (
    <div aria-hidden="true">
      <Card as="div" padding="none">
        <div className="flex flex-col divide-y divide-border">
          {[0, 1].map((i) => (
            <div key={i} className="flex h-11 items-center gap-6 px-4">
              <Skeleton className="h-3 w-40" />
              <Skeleton className="h-4 w-16" />
              <Skeleton className="h-3 w-24" />
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

/**
 * The names an application answers on: its primary domain, the aliases that serve it and the
 * redirects that send visitors to it, with what the certificate does for each. Adding a name
 * asks DNS first; removing one needs the operator to confirm it's them.
 */
export function AppDomainsTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("domains.appTab.documentTitle", { domain }));
  const queryClient = useQueryClient();
  const list = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const active = useQuery(activeJobsQuery());
  useCertificateRefresh();
  const followed = useFollowedJob();

  const [adding, setAdding] = useState(false);
  const [lastAdded, setLastAdded] = useState<string | null>(null);
  const [lastChange, setLastChange] = useState<"own" | "shared" | null>(null);
  const [dnsFor, setDnsFor] = useState<string | null>(null);
  const [removing, setRemoving] = useState<AppDomain | null>(null);
  const [portFor, setPortFor] = useState<AppDomain | null>(null);

  const lineage = certs.isError && certs.data === undefined ? null : findCertificate(certs.data, domain);
  const running = certificateJobFor(active.data?.jobs, domain);
  const extending = running !== null || (followed.job !== null && !isJobFinished(followed.job)) || (followed.id !== null && followed.job === null);

  const applyChange = (change: AppDomainChange): void => {
    queryClient.setQueryData<AppDomainList>(domainKeys.list(domain), { app: change.app, domains: change.domains, canonical: change.canonical });
    void queryClient.invalidateQueries({ queryKey: domainKeys.list(domain) });
  };

  const onAdded = (change: AppDomainChange, name: string): void => {
    applyChange(change);
    setLastAdded(name);
    setLastChange(null);
    const kept = change.adopted ?? [];
    const adopted = kept.length > 0 ? ` ${t("domains.appTab.keptAsAlias", { count: kept.length, names: kept.join(", ") })}` : "";
    const made = (change.dns ?? []).filter((entry) => ["created", "adopted", "replaced"].includes(String(entry["outcome"]))).length;
    const addedToast = t("domains.appTab.addedToast", { name, domain });
    if (change.certificate_job_id) {
      followed.follow(change.certificate_job_id);
      toast.success(addedToast, { description: `${t("domains.appTab.extendingCertDescription")}${adopted}${made > 0 ? ` ${t("network.addDomain.dnsMade")}` : ""}` });
    } else {
      const description = `${change.tls ? "" : t("domains.appTab.plainHttpNoCertificate")}${adopted}${made > 0 ? ` ${t("network.addDomain.dnsMade")}` : ""}`.trim();
      toast.success(addedToast, description === "" ? {} : { description });
    }
  };

  const retry = useMutation({
    mutationFn: (entry: AppDomain) =>
      request("post", "/api/apps/{domain}/domains", { params: { domain }, body: { domain: entry.domain, kind: entry.kind, force: false, dns: false } }),
    onSuccess: (change, entry) => {
      applyChange(change);
      setLastAdded(entry.domain);
      setLastChange(null);
      if (change.certificate_job_id) followed.follow(change.certificate_job_id);
      else toast.info(t("domains.appTab.servesPlainHttpToast", { domain }), { description: t("domains.appTab.noCertToExtendDescription") });
    },
    onError: (error, entry) => {
      reportActionError(t("domains.appTab.retryFailedError", { name: entry.domain }), error);
    },
  });

  const switchName = useMutation({
    mutationFn: ({ entry, enabled }: { entry: AppDomain; enabled: boolean }) =>
      request("put", "/api/apps/{domain}/domains/{name}/enabled", { params: { domain, name: entry.domain }, body: { enabled } }),
    onSuccess: (change, { entry, enabled }) => {
      applyChange(change);
      if (change.certificate_job_id) followed.follow(change.certificate_job_id);
      toast.success(t(enabled ? "domains.appTab.switchedOnToast" : "domains.appTab.switchedOffToast", { name: entry.domain, domain }));
    },
    onError: (error, { entry, enabled }) => {
      reportActionError(t(enabled ? "domains.appTab.switchOnFailedError" : "domains.appTab.switchOffFailedError", { name: entry.domain }), error);
    },
  });

  const holdOwn = useMutation({
    mutationFn: ({ entry, certificate }: { entry: AppDomain; certificate: "own" | "shared" }) =>
      request("put", "/api/apps/{domain}/domains/{name}/certificate", { params: { domain, name: entry.domain }, body: { certificate } }),
    onSuccess: (change, { entry, certificate }) => {
      applyChange(change);
      setLastAdded(entry.domain);
      setLastChange(certificate);
      if (change.certificate_job_id) followed.follow(change.certificate_job_id);
    },
    onError: (error, { entry }) => {
      reportActionError(t("domains.appTab.certificateChangeFailedError", { name: entry.domain }), error);
    },
  });

  const presentedBy = (entry: AppDomain) => certificateOf(entry, lineage, (name) => findCertificate(certs.data, name));

  const columns: Column<AppDomain>[] = [
    {
      id: "domain",
      header: t("domains.appTab.domainColumn"),
      cell: (entry) => (
        <a
          href={`${presentedBy(entry) && covers(presentedBy(entry), entry.domain) ? "https" : "http"}://${entry.domain}`}
          target="_blank"
          rel="noreferrer"
          translate="no"
          className="-mx-1 rounded-chip px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2"
        >
          {entry.domain}
          <span className="sr-only">{t("domains.appTab.openInNewTabSr")}</span>
        </a>
      ),
      sortValue: (entry) => entry.domain,
    },
    { id: "role", header: t("domains.appTab.roleColumn"), cell: (entry) => <Role entry={entry} app={domain} /> },
    {
      id: "certificate",
      header: t("domains.appTab.certificateColumn"),
      cell: (entry) => {
        const coverage = coverageOf(entry.domain, presentedBy(entry), extending, t.locale);
        return <CertificateStatus tone={coverage.tone} label={coverage.label} />;
      },
    },
    {
      id: "added",
      header: t("domains.appTab.addedColumn"),
      hideBelow: "md",
      cell: (entry) =>
        entry.created_at ? <RelativeTime value={entry.created_at} className="text-fg-muted" /> : <span className="text-fg-faint">{t("domains.unknown")}</span>,
    },
  ];

  return (
    <Sections>
      <Section
        title={t("domains.appTab.domainsSectionTitle")}
        description={t("domains.appTab.domainsSectionDescription", { domain })}
        actions={
          <Button icon={<Plus aria-hidden="true" />} onClick={() => setAdding(true)}>
            {t("domains.addDomain")}
          </Button>
        }
      >
        <JobBanner
          followed={followed}
          words={{
            running:
              lastChange === "own" && lastAdded !== null
                ? t("domains.appTab.orderingOwnCertificate", { name: lastAdded })
                : lastChange === "shared" && lastAdded !== null
                  ? t("domains.appTab.coveringWithSharedCertificate", { name: lastAdded, domain })
                  : lastAdded === null
                    ? t("domains.appTab.extendingCertOf", { domain })
                    : t("domains.appTab.extendingCertTo", { name: lastAdded }),
            done: lastChange === null ? t("domains.appTab.certCoversEvery", { domain }) : t("domains.appTab.certificateChanged", { name: lastAdded ?? domain }),
            failed: lastChange === null ? t("domains.appTab.certNotExtended") : t("domains.appTab.certificateNotChanged", { name: lastAdded ?? domain }),
            hint:
              lastChange === null
                ? t("domains.appTab.extendHint", { name: lastAdded ?? t("domains.appTab.theNewName") })
                : t("domains.appTab.certificateNotChangedHint", { name: lastAdded ?? domain }),
          }}
        />
        {list.isError && list.data === undefined ? (
          <ErrorBlock error={list.error} title={t("domains.appTab.couldNotLoadDomains", { domain })} onRetry={() => void list.refetch()} retrying={list.isRefetching} />
        ) : list.data === undefined ? (
          <div aria-busy="true">
            <span className="sr-only">{t("domains.appTab.loadingDomainsSr")}</span>
            <TableSkeleton />
          </div>
        ) : (
          <DataTable
            caption={t("domains.appTab.tableCaption", { domain })}
            columns={columns}
            rows={list.data.domains}
            getRowId={(entry) => entry.domain}
            empty={
              <EmptyState
                icon={<Globe />}
                title={t("domains.appTab.noDomainsTitle")}
                description={t("domains.appTab.noDomainsDescription")}
                className="border-0 py-8"
              />
            }
            rowActions={(entry) => (
              <Menu
                align="end"
                trigger={<IconButton label={t("domains.actionsFor", { name: entry.domain })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
              >
                <MenuItem icon={<Search />} onClick={() => setDnsFor(entry.domain)}>
                  {t("domains.checkDns")}
                </MenuItem>
                {entry.kind === "alias" ? (
                  <MenuItem icon={<Plug />} onClick={() => setPortFor(entry)}>
                    {t("network.port.menu")}
                  </MenuItem>
                ) : null}
                {entry.kind !== "primary" && lineage && !covers(presentedBy(entry), entry.domain) ? (
                  <MenuItem icon={<RotateCw />} disabled={retry.isPending || extending} onClick={() => retry.mutate(entry)}>
                    {t("domains.appTab.retryCertificate")}
                  </MenuItem>
                ) : null}
                {entry.kind !== "primary" && lineage !== undefined ? (
                  <MenuItem
                    icon={<BadgeCheck />}
                    disabled={holdOwn.isPending || extending}
                    onClick={() => holdOwn.mutate({ entry, certificate: entry.certificate === "own" ? "shared" : "own" })}
                  >
                    {entry.certificate === "own" ? t("domains.appTab.useSharedCertificate") : t("domains.appTab.giveOwnCertificate")}
                  </MenuItem>
                ) : null}
                {entry.kind !== "primary" ? (
                  <MenuItem
                    icon={<Power />}
                    disabled={switchName.isPending}
                    onClick={() => switchName.mutate({ entry, enabled: !entry.enabled })}
                  >
                    {!entry.enabled ? t("domains.appTab.switchOn") : t("domains.appTab.switchOff")}
                  </MenuItem>
                ) : null}
                {entry.kind !== "primary" ? (
                  <>
                    <MenuSeparator />
                    <MenuItem icon={<Trash2 />} destructive onClick={() => setRemoving(entry)}>
                      {t("domains.appTab.remove")}
                    </MenuItem>
                  </>
                ) : null}
              </Menu>
            )}
          />
        )}
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("domains.appTab.primaryDomainNote", { domain })}</p>
      </Section>

      {list.data !== undefined ? (
        <CanonicalSection
          domain={domain}
          canonical={list.data.canonical}
          onChanged={(change) => {
            applyChange(change);
            if (change.certificate_job_id) followed.follow(change.certificate_job_id);
          }}
        />
      ) : null}

      <Section
        title={t("domains.appTab.certificateSectionTitle")}
        description={t("domains.appTab.certificateSectionDescription")}
        loading={lineage === undefined}
        actions={
          <TextLink to="/domains" size="ui">
            {t("domains.appTab.allCertificates")}
          </TextLink>
        }
      >
        {lineage === undefined ? (
          <Skeleton className="h-16 rounded-card" />
        ) : lineage === null ? (
          <p className="text-13 text-fg-muted">
            {certs.isError ? t("domains.appTab.certificatesCouldNotBeListed") : t("domains.appTab.noCertificateCoversNote", { domain })}
          </p>
        ) : (
          <Card as="div" padding="sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex min-w-0 flex-col gap-1">
                <CertificateStatus {...certificateView(lineage, t.locale)} className="text-14" />
                <p className="text-13 text-fg-muted">
                  {[
                    lineage.issuer ? t("domains.appTab.issuedBy", { issuer: issuerName(lineage.issuer) }) : null,
                    lineage.expires_on ? t("domains.appTab.validUntil", { date: lineage.expires_on }) : null,
                    lineage.auto_renew ? t("domains.appTab.renewsAutomatically") : t("domains.appTab.doesNotRenewAutomatically"),
                  ]
                    .filter((part): part is string => part !== null)
                    .join(" ")}
                </p>
              </div>
              <ul aria-label={t("domains.appTab.namesOnCertificateAriaLabel")} className="flex min-w-0 flex-wrap gap-1.5 sm:justify-end">
                {lineage.domains.map((name) => (
                  <li key={name}>
                    <Badge mono>{name}</Badge>
                  </li>
                ))}
              </ul>
            </div>
          </Card>
        )}
        <CertificateSourceCard
          domain={domain}
          onChosen={(jobId) => {
            if (jobId !== null) followed.follow(jobId);
            void queryClient.invalidateQueries({ queryKey: ["certs"] });
          }}
        />
      </Section>

      <AppDnsSection domain={domain} />

      <CommandHint command={`noust domain list ${domain}`} label={t("domains.fromTerminal")} />

      <AddDomainDialog app={domain} open={adding} onOpenChange={setAdding} onAdded={onAdded} />
      <DnsDialog app={domain} name={dnsFor} onClose={() => setDnsFor(null)} />
      <DomainPortDialog
        app={domain}
        name={portFor?.domain ?? null}
        port={portFor?.port ?? null}
        onClose={() => setPortFor(null)}
        onSaved={(change, name, port) => {
          applyChange(change);
          toast.success(port === null ? t("network.port.reset", { name }) : t("network.port.saved", { name, port }));
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={removing ? t("domains.appTab.removeDialogTitle", { domain: removing.domain }) : t("domains.appTab.removeDomain")}
        description={removing ? t("domains.appTab.removeDialogDescription", { app: domain, removed: removing.domain }) : ""}
        confirmText={removing?.domain ?? ""}
        actionLabel={t("domains.appTab.removeDomain")}
        onConfirm={async () => {
          if (!removing) return;
          const change = await request("delete", "/api/apps/{domain}/domains/{name}", {
            params: { domain, name: removing.domain },
          });
          applyChange(change);
          if (change.certificate_job_id) followed.follow(change.certificate_job_id);
          toast.success(t("domains.appTab.removedToast", { removed: removing.domain, domain }));
        }}
      />
    </Sections>
  );
}
