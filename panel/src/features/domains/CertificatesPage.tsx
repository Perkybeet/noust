import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ShieldX, Upload } from "lucide-react";
import { Suspense, lazy, useMemo, useState } from "react";

import { request } from "../../api/client";
import { certKeys, certsQuery } from "../../api/queries/certs";
import { networkKeys } from "../../api/queries/network";
import type { CertEntry } from "../../api/queries/certs";
import { activeJobsQuery, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatDate } from "../../lib/format";
import { useOnArrival } from "../../lib/useArrival";
import { reportActionError } from "../apps/useAppActions";
import { CERT_STATE, CertificateStatus } from "./CertificateStatus";
import { byUrgency, certificateJobFor, certificateView, issuerName } from "./certificates";
import type { CertTone } from "./certificates";
import { AuthorityDialog } from "./AuthorityDialog";
import { DomainsListPage } from "./DomainsListPage";
import { IssueCertificateDialog } from "./IssueCertificateDialog";
import { JobBanner } from "./JobBanner";
import type { JobWords } from "./JobBanner";
import { truncatedNames } from "./names";
import type { CertificatesSearch } from "./search";
import { UploadCertificateDialog } from "./UploadCertificateDialog";
import { useCertificateRefresh } from "./useCertificateJobs";

const ALL = "all";

/** The private authority is a dialog few open, so it is its own chunk instead of part of the page. */
const InternalAuthorityDialog = lazy(() => import("./InternalAuthorityDialog").then((module) => ({ default: module.InternalAuthorityDialog })));

type SearchPatch = { [K in keyof CertificatesSearch]?: CertificatesSearch[K] | undefined };

/** The state a row shows: the job working on it, or what its expiry means. */
function rowState(cert: CertEntry, job: Job | null, t: T): { tone: CertTone; label: string } {
  if (job !== null) return { tone: "busy", label: job.type === "cert_renew" ? t("domains.certificatesTab.renewing") : t("domains.certificatesTab.issuing") };
  return certificateView(cert, t.locale);
}

/** "Oct 7, 2026" for certbot's "2026-10-07", which it prints as a day with no time. */
function expiryDate(cert: CertEntry, t: T): string | null {
  if (cert.expires_on === null || cert.expires_on === undefined) return null;
  const date = new Date(`${cert.expires_on}T00:00:00`);
  return Number.isNaN(date.getTime()) ? cert.expires_on : formatDate(date, {}, t.locale);
}

/** Where a certificate comes from, in words: ordered over HTTP or DNS, or uploaded. */
function comesFrom(t: T, cert: CertEntry): string {
  if (cert.source === "custom") return t("network.certificates.uploaded");
  return cert.challenge === "dns-01" ? t("network.certificates.orderedDns") : t("network.certificates.orderedHttp");
}

/** The names a certificate covers besides the one it is named after. */
function otherNames(cert: CertEntry): string[] {
  return cert.domains.filter((name) => name !== cert.domain);
}

function Names({ cert }: { cert: CertEntry }) {
  const t = useT();
  const others = otherNames(cert);
  if (others.length === 0) return <span className="text-fg-muted">{t("domains.certificatesTab.onlyThisName")}</span>;
  const { shown, rest } = truncatedNames(others, 2);
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={others.join(", ")}>
      <Mono tone="muted" truncate>
        {shown}
      </Mono>
      {rest > 0 ? <span className="shrink-0 text-12 text-fg-muted">{t("domains.moreCount", { count: rest })}</span> : null}
    </span>
  );
}

function CertificateDrawer({ cert, job, onClose, onRenew }: { cert: CertEntry | null; job: Job | null; onClose: () => void; onRenew: (cert: CertEntry) => void }) {
  const t = useT();
  return (
    <Drawer
      open={cert !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={cert?.domain ?? t("domains.certificatesTab.drawerTitleDefault")}
      description={t("domains.certificatesTab.drawerDescription")}
      footer={
        cert && cert.source !== "custom" ? (
          <Button icon={<RefreshCw aria-hidden="true" />} disabled={job !== null} onClick={() => onRenew(cert)}>
            {t("domains.certificatesTab.renew")}
          </Button>
        ) : undefined
      }
    >
      {cert ? (
        <div className="flex flex-col gap-5">
          <CertificateStatus {...rowState(cert, job, t)} className="text-14" />
          <KeyValueList
            items={[
              {
                label: t("domains.certificatesTab.namesLabel"),
                value: cert.domains.join(", "),
                copy: cert.domains.join(" "),
                hint: t("domains.certificatesTab.namesHint", { count: cert.domains.length }),
              },
              { label: t("network.certificates.comesFrom"), value: comesFrom(t, cert), mono: false, copy: false },
              { label: t("domains.certificatesTab.issuerLabel"), value: cert.issuer ? issuerName(cert.issuer) : null, mono: false },
              { label: t("domains.certificatesTab.expiresLabel"), value: cert.expires_on ?? null },
              { label: t("domains.certificatesTab.certbotSaysLabel"), value: cert.valid_until ?? null },
              {
                label: t("domains.certificatesTab.renewalLabel"),
                value: cert.auto_renew ? t("domains.certificatesTab.renewalAutomatic") : t("domains.certificatesTab.renewalManual"),
                mono: false,
                copy: false,
              },
              { label: t("domains.certificatesTab.certificateFileLabel"), value: cert.path ?? null },
              { label: t("domains.certificatesTab.privateKeyLabel"), value: cert.key_path ?? null },
            ]}
          />
          <CommandHint command={`noust cert info ${cert.domain}`} label={t("domains.fromTerminal")} />
        </div>
      ) : null}
    </Drawer>
  );
}

export interface CertificatesPageProps {
  search: CertificatesSearch;
  onSearchChange: (search: CertificatesSearch, options?: { replace?: boolean }) => void;
}

/**
 * The Certificates tab: every certificate certbot holds on this machine, the most urgent
 * first, what it covers and when it expires; renewing, revoking and deleting it. Issuing and
 * renewing run as jobs, followed above the table.
 */
export function CertificatesPage({ search, onSearchChange }: CertificatesPageProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const certs = useQuery(certsQuery());
  const active = useQuery(activeJobsQuery());
  useCertificateRefresh();
  const followed = useFollowedJob();

  const [words, setWords] = useState<JobWords | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [opened, setOpened] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<CertEntry | null>(null);
  const [deleting, setDeleting] = useState<CertEntry | null>(null);
  const [uploading, setUploading] = useState(false);
  const [choosingAuthority, setChoosingAuthority] = useState(false);
  const [privateAuthority, setPrivateAuthority] = useState(false);
  const [deletingUpload, setDeletingUpload] = useState<CertEntry | null>(null);

  const all = useMemo(() => [...(certs.data?.certificates ?? [])].sort(byUrgency), [certs.data]);
  const needle = (search.q ?? "").toLowerCase();
  const shown = all.filter(
    (cert) =>
      (needle === "" || cert.domain.includes(needle) || cert.domains.some((name) => name.includes(needle))) &&
      (search.show !== "attention" || certificateView(cert, t.locale).attention),
  );
  const filtered = needle !== "" || search.show !== undefined;
  const jobs = active.data?.jobs;
  const openedCert = all.find((cert) => cert.domain === opened) ?? null;
  const empty = certs.data !== undefined && all.length === 0;

  const set = (patch: SearchPatch): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: CertificatesSearch = {};
    if (next.q) clean.q = next.q;
    if (next.show) clean.show = next.show;
    onSearchChange(clean, { replace: true });
  };

  // Arriving with `?new` (the palette's "Issue a certificate"): the dialog opens, and the link is spent.
  const opening = search.new === true;
  useOnArrival(opening, () => {
    setIssuing(true);
    set({});
  });

  const followJob = (jobId: string, next: JobWords): void => {
    setWords(next);
    followed.follow(jobId);
  };

  const renew = useMutation({
    mutationFn: ({ cert, force }: { cert: CertEntry; force: boolean }) =>
      request("post", "/api/certs/{domain}/renew", { params: { domain: cert.domain }, body: { force } }),
    onSuccess: (result, { cert, force }) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      followJob(result.job_id, {
        running: force
          ? t("domains.certificatesTab.renewJobRunning", { domain: cert.domain })
          : t("domains.certificatesTab.renewIfDueJobRunning", { domain: cert.domain }),
        done: force
          ? t("domains.certificatesTab.renewJobDoneForced", { domain: cert.domain })
          : t("domains.certificatesTab.renewJobDoneIfDue", { domain: cert.domain }),
        failed: t("domains.certificatesTab.renewJobFailed", { domain: cert.domain }),
      });
    },
    onError: (error, { cert }) => {
      reportActionError(t("domains.certificatesTab.couldNotRenew", { domain: cert.domain }), error);
    },
  });

  const renewAll = useMutation({
    mutationFn: () => request("post", "/api/certs/renew-all", { body: { force: false } }),
    onSuccess: (result) => {
      followJob(result.job_id, {
        running: t("domains.certificatesTab.renewAllRunning"),
        done: t("domains.certificatesTab.renewAllDone"),
        failed: t("domains.certificatesTab.renewAllFailed"),
      });
    },
    onError: (error) => {
      reportActionError(t("domains.certificatesTab.couldNotStartRenewal"), error);
    },
  });

  const columns: Column<CertEntry>[] = [
    {
      id: "name",
      header: t("domains.certificatesTab.certificateColumn"),
      cell: (cert) => <span translate="no">{cert.domain}</span>,
      sortValue: (cert) => cert.domain,
    },
    {
      id: "state",
      header: t("domains.certificatesTab.stateColumn"),
      width: "w-44",
      card: "status",
      cell: (cert) => {
        const state = rowState(cert, certificateJobFor(jobs, cert.domain), t);
        return <StatusPill state={CERT_STATE[state.tone]} label={state.label} appearance="inline" size="sm" />;
      },
      sortValue: (cert) => cert.days_remaining ?? null,
    },
    { id: "names", header: t("domains.certificatesTab.alsoCoversColumn"), hideBelow: "md", card: "hidden", cell: (cert) => <Names cert={cert} /> },
    {
      id: "expires",
      header: t("domains.certificatesTab.expiresLabel"),
      width: "w-36",
      hideBelow: "sm",
      cell: (cert) => {
        const date = expiryDate(cert, t);
        return date === null ? <EmptyCell reason={t("domains.certificates.expiryUnknown")} /> : <span className="tabular-nums">{date}</span>;
      },
      sortValue: (cert) => cert.days_remaining ?? null,
    },
  ];

  const issueButton = (variant: "primary" | "secondary") => (
    <Button variant={variant} icon={<ICONS.add aria-hidden="true" />} onClick={() => setIssuing(true)}>
      {t("domains.issueCertificate")}
    </Button>
  );

  let content;
  if (certs.isError && certs.data === undefined) {
    content = (
      <ErrorBlock
        error={certs.error}
        title={t("domains.certificatesTab.couldNotListCertificates")}
        hint={t("domains.certificatesTab.listHint")}
        onRetry={() => void certs.refetch()}
        retrying={certs.isRefetching}
      />
    );
  } else if (empty) {
    content = (
      <EmptyState
        variant="firstUse"
        icon={<ICONS.verified />}
        title={t("domains.certificatesTab.noCertsYetTitle")}
        description={t("domains.certificatesTab.noCertsYetDescription")}
        action={issueButton("secondary")}
        command="noust cert create -d example.com"
      />
    );
  } else {
    content = (
      <DataTable
        mobile="cards"
        pagination="certificates"
        caption={filtered ? t("domains.certificatesTab.tableCaptionFiltered") : t("domains.certificatesTab.tableCaption")}
        columns={columns}
        rows={shown}
        getRowId={(cert) => cert.domain}
        loading={certs.isPending}
        onRowActivate={(cert) => setOpened(cert.domain)}
        empty={
          <EmptyState
            variant="inline"
            title={t("domains.certificatesTab.noCertMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={() => onSearchChange({})}>
                {t("domains.clearFilters")}
              </Button>
            }
          />
        }
        rowActions={(cert) => {
          const busy = certificateJobFor(jobs, cert.domain) !== null;
          if (cert.source === "custom") {
            return (
              <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: cert.domain })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
                <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeletingUpload(cert)}>
                  {t("network.upload.delete")}
                </MenuItem>
              </Menu>
            );
          }
          return (
            <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: cert.domain })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
              <MenuItem icon={<RefreshCw />} disabled={busy} onClick={() => renew.mutate({ cert, force: false })}>
                {t("domains.certificatesTab.renewIfDue")}
              </MenuItem>
              <MenuItem icon={<RefreshCw />} disabled={busy} onClick={() => renew.mutate({ cert, force: true })}>
                {t("domains.certificatesTab.renewNow")}
              </MenuItem>
              <MenuSeparator />
              <MenuItem icon={<ShieldX />} destructive onClick={() => setRevoking(cert)}>
                {t("domains.certificatesTab.revoke")}
              </MenuItem>
              <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleting(cert)}>
                {t("domains.delete")}
              </MenuItem>
            </Menu>
          );
        }}
      />
    );
  }

  return (
    <DomainsListPage
      {...(empty
        ? {}
        : {
            secondaryActions: (
              <Button icon={<RefreshCw aria-hidden="true" />} loading={renewAll.isPending} onClick={() => renewAll.mutate()}>
                {t("domains.certificatesTab.renewDue")}
              </Button>
            ),
          })}
      overflow={
        <>
          <MenuItem icon={<Upload />} onClick={() => setUploading(true)}>
            {t("network.upload.action")}
          </MenuItem>
          <MenuItem icon={<ICONS.verified />} onClick={() => setChoosingAuthority(true)}>
            {t("network.authority.menu")}
          </MenuItem>
          <MenuItem icon={<ICONS.verified />} onClick={() => setPrivateAuthority(true)}>
            {t("internalCa.menu")}
          </MenuItem>
        </>
      }
      primaryAction={issueButton("primary")}
      {...(words !== null && followed.id !== null ? { notice: <JobBanner followed={followed} words={words} /> } : {})}
      {...(empty
        ? {}
        : {
            filters: (
              <FilterBar
                label={t("domains.certificatesTab.filterAriaLabel")}
                search={{
                  value: search.q ?? "",
                  onChange: (value) => set({ q: value === "" ? undefined : value }),
                  label: t("domains.certificatesTab.filterByNameAriaLabel"),
                  placeholder: t("domains.filterByNamePlaceholder"),
                }}
                filters={
                  <Select
                    aria-label={t("domains.certificatesTab.showLabel")}
                    value={search.show ?? ALL}
                    onValueChange={(value) => set({ show: value === "attention" ? "attention" : undefined })}
                    options={[
                      { value: ALL, label: t("domains.certificatesTab.showAll") },
                      { value: "attention", label: t("domains.certificatesTab.showAttention") },
                    ]}
                  />
                }
                {...(certs.data !== undefined
                  ? {
                      count: filtered
                        ? t("domains.certificatesTab.countFiltered", { shown: shown.length, total: all.length })
                        : t("domains.certificatesTab.count", { count: all.length }),
                    }
                  : {})}
              />
            ),
          })}
      {...(empty ? {} : { footer: <CommandHint command="noust cert list" label={t("domains.fromTerminal")} /> })}
    >
      {content}

      <IssueCertificateDialog
        open={issuing}
        onOpenChange={setIssuing}
        onQueued={(jobId, domain) =>
          followJob(jobId, {
            running: t("domains.certificatesTab.issueJobRunning", { domain }),
            done: t("domains.certificatesTab.issueJobDone", { domain }),
            failed: t("domains.certificatesTab.issueJobFailed", { domain }),
            hint: t("domains.certificatesTab.issueJobHint"),
          })
        }
      />
      <UploadCertificateDialog open={uploading} onClose={() => setUploading(false)} />
      <AuthorityDialog
        open={choosingAuthority}
        onClose={() => setChoosingAuthority(false)}
        onRegister={(jobId) =>
          followJob(jobId, {
            running: t("network.authority.registerRunning"),
            done: t("network.authority.registerDone"),
            failed: t("network.authority.registerFailed"),
          })
        }
      />
      {privateAuthority ? (
        <Suspense fallback={null}>
          <InternalAuthorityDialog open={privateAuthority} onClose={() => setPrivateAuthority(false)} />
        </Suspense>
      ) : null}
      <ConfirmDialog
        open={deletingUpload !== null}
        onOpenChange={(open) => {
          if (!open) setDeletingUpload(null);
        }}
        friction="simple"
        destructive
        title={t("network.upload.deleteTitle", { name: deletingUpload?.domain ?? "" })}
        description={t("network.upload.deleteDescription")}
        actionLabel={t("network.upload.delete")}
        onConfirm={async () => {
          if (deletingUpload === null) return;
          await request("delete", "/api/certs/uploaded/{name}", { params: { name: deletingUpload.domain } });
          void queryClient.invalidateQueries({ queryKey: certKeys.all });
          void queryClient.invalidateQueries({ queryKey: networkKeys.uploaded });
          toast.success(t("network.upload.deletedToast", { name: deletingUpload.domain }));
        }}
      />
      <CertificateDrawer
        cert={openedCert}
        job={openedCert ? certificateJobFor(jobs, openedCert.domain) : null}
        onClose={() => setOpened(null)}
        onRenew={(cert) => renew.mutate({ cert, force: true })}
      />
      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(open) => {
          if (!open) setRevoking(null);
        }}
        title={revoking ? t("domains.certificatesTab.revokeNamedTitle", { domain: revoking.domain }) : t("domains.certificatesTab.revokeCertificate")}
        description={t("domains.certificatesTab.revokeDialogDescription")}
        confirmText={revoking?.domain ?? ""}
        actionLabel={t("domains.certificatesTab.revokeCertificate")}
        onConfirm={async () => {
          if (!revoking) return;
          await request("post", "/api/certs/{domain}/revoke", { params: { domain: revoking.domain } });
          void queryClient.invalidateQueries({ queryKey: certKeys.all });
          toast.success(t("domains.certificatesTab.revokedToast", { domain: revoking.domain }));
        }}
      />
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title={deleting ? t("domains.deleteNamedTitle", { name: deleting.domain }) : t("domains.certificatesTab.deleteCertificate")}
        description={t("domains.certificatesTab.deleteDialogDescription")}
        confirmText={deleting?.domain ?? ""}
        actionLabel={t("domains.certificatesTab.deleteCertificate")}
        onConfirm={async () => {
          if (!deleting) return;
          await request("delete", "/api/certs/{domain}", { params: { domain: deleting.domain } });
          void queryClient.invalidateQueries({ queryKey: certKeys.all });
          toast.success(t("domains.certificatesTab.deletedToast", { domain: deleting.domain }));
        }}
      />
    </DomainsListPage>
  );
}
