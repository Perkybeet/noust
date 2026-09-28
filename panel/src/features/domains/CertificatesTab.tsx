import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Plus, RefreshCw, Search, ShieldCheck, ShieldX, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

import { request } from "../../api/client";
import { certKeys, certsQuery } from "../../api/queries/certs";
import type { CertEntry } from "../../api/queries/certs";
import { activeJobsQuery, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import type { Column } from "../../components/ui/DataTable";
import { DataTable } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { CertificateStatus } from "./CertificateStatus";
import { IssueCertificateDialog } from "./IssueCertificateDialog";
import { JobBanner } from "./JobBanner";
import type { JobWords } from "./JobBanner";
import { byUrgency, certificateJobFor, certificateView, issuerName } from "./certificates";
import { truncatedNames } from "./names";
import { useCertificateRefresh } from "./useCertificateJobs";

/** The state a row shows: the job working on it, or what its expiry means. */
function rowState(cert: CertEntry, job: Job | null, t: T): { tone: "ok" | "warn" | "fail" | "idle" | "busy"; label: string } {
  if (job !== null) return { tone: "busy", label: job.type === "cert_renew" ? t("domains.certificatesTab.renewing") : t("domains.certificatesTab.issuing") };
  return certificateView(cert, t.locale);
}

/** The names a certificate covers besides the one it is named after. */
function otherNames(cert: CertEntry): string[] {
  return cert.domains.filter((name) => name !== cert.domain);
}

function Names({ cert }: { cert: CertEntry }) {
  const t = useT();
  const others = otherNames(cert);
  if (others.length === 0) return <span className="text-fg-faint">{t("domains.certificatesTab.onlyThisName")}</span>;
  const { shown, rest } = truncatedNames(others, 2);
  return (
    <span className="flex min-w-0 items-center gap-1.5" title={others.join(", ")}>
      <span translate="no" className="mono truncate text-12 text-fg-muted">
        {shown}
      </span>
      {rest > 0 ? <span className="shrink-0 text-12 text-fg-faint">{t("domains.moreCount", { count: rest })}</span> : null}
    </span>
  );
}

function CertificateDrawer({
  cert,
  job,
  onClose,
  onRenew,
}: {
  cert: CertEntry | null;
  job: Job | null;
  onClose: () => void;
  onRenew: (cert: CertEntry) => void;
}) {
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
        cert ? (
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
          <CommandHint command={`wasm cert info ${cert.domain}`} label={t("domains.fromTerminal")} />
        </div>
      ) : null}
    </Drawer>
  );
}

/**
 * Every certificate certbot holds on this machine, the most urgent first: what it covers, when
 * it expires, and renewing, revoking and deleting it. Issuing and renewing run as jobs, followed
 * above the table.
 */
export function CertificatesTab({ initialFilter = "" }: { initialFilter?: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const certs = useQuery(certsQuery());
  const active = useQuery(activeJobsQuery());
  useCertificateRefresh();
  const followed = useFollowedJob();

  const [words, setWords] = useState<JobWords | null>(null);
  const [filter, setFilter] = useState(initialFilter);
  const [issuing, setIssuing] = useState(false);
  const [opened, setOpened] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<CertEntry | null>(null);
  const [deleting, setDeleting] = useState<CertEntry | null>(null);

  const all = useMemo(() => [...(certs.data?.certificates ?? [])].sort(byUrgency), [certs.data]);
  const needle = filter.trim().toLowerCase();
  const shown = needle === "" ? all : all.filter((cert) => cert.domains.some((name) => name.includes(needle)) || cert.domain.includes(needle));
  const jobs = active.data?.jobs;
  const openedCert = all.find((cert) => cert.domain === opened) ?? null;

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
    { id: "names", header: t("domains.certificatesTab.alsoCoversColumn"), hideBelow: "md", cell: (cert) => <Names cert={cert} /> },
    {
      id: "expires",
      header: t("domains.certificatesTab.expiresLabel"),
      mono: true,
      hideBelow: "sm",
      cell: (cert) => cert.expires_on ?? <span className="text-fg-faint">{t("domains.unknown")}</span>,
      sortValue: (cert) => cert.days_remaining ?? null,
    },
    {
      id: "state",
      header: t("domains.certificatesTab.stateColumn"),
      cell: (cert) => <CertificateStatus {...rowState(cert, certificateJobFor(jobs, cert.domain), t)} />,
      sortValue: (cert) => cert.days_remaining ?? null,
    },
    {
      id: "issuer",
      header: t("domains.certificatesTab.issuerLabel"),
      hideBelow: "lg",
      cell: (cert) => <span className="text-fg-muted">{cert.issuer ? issuerName(cert.issuer) : t("domains.unknown")}</span>,
    },
    {
      id: "renewal",
      header: t("domains.certificatesTab.renewalLabel"),
      hideBelow: "lg",
      cell: (cert) => <span className="text-fg-muted">{cert.auto_renew ? t("domains.certificatesTab.renewalAutomatic") : t("domains.certificatesTab.renewalManual")}</span>,
    },
  ];

  const issueButton = (
    <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setIssuing(true)}>
      {t("domains.issueCertificate")}
    </Button>
  );

  return (
    <div className="flex flex-col gap-4">
      {words !== null ? <JobBanner followed={followed} words={words} /> : null}

      {certs.isError && certs.data === undefined ? (
        <ErrorBlock
          error={certs.error}
          title={t("domains.certificatesTab.couldNotListCertificates")}
          hint={t("domains.certificatesTab.listHint")}
          onRetry={() => void certs.refetch()}
          retrying={certs.isRefetching}
        />
      ) : certs.data !== undefined && all.length === 0 ? (
        <EmptyState
          level={3}
          icon={<ShieldCheck />}
          title={t("domains.certificatesTab.noCertsYetTitle")}
          description={t("domains.certificatesTab.noCertsYetDescription")}
          action={issueButton}
          command="wasm cert create -d example.com"
          className="py-16"
        />
      ) : (
        <>
          <div role="search" aria-label={t("domains.certificatesTab.filterAriaLabel")} className="flex flex-wrap items-center gap-2">
            <Input
              type="search"
              aria-label={t("domains.certificatesTab.filterByNameAriaLabel")}
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
              <Button icon={<RefreshCw aria-hidden="true" />} loading={renewAll.isPending} onClick={() => renewAll.mutate()}>
                {t("domains.certificatesTab.renewDue")}
              </Button>
              {issueButton}
            </div>
          </div>
          <DataTable
            caption={needle === "" ? t("domains.certificatesTab.tableCaption") : t("domains.certificatesTab.tableCaptionFiltered")}
            columns={columns}
            rows={shown}
            getRowId={(cert) => cert.domain}
            loading={certs.isPending}
            onRowActivate={(cert) => setOpened(cert.domain)}
            empty={
              <EmptyState
                title={t("domains.certificatesTab.noCertMatchTitle")}
                description={t("domains.certificatesTab.noCertMatchDescription", { filter: filter.trim() })}
                className="border-0 py-8"
              />
            }
            rowActions={(cert) => {
              const busy = certificateJobFor(jobs, cert.domain) !== null;
              return (
                <Menu align="end" trigger={<IconButton label={t("domains.actionsFor", { name: cert.domain })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}>
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
                  <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleting(cert)}>
                    {t("domains.delete")}
                  </MenuItem>
                </Menu>
              );
            }}
          />
          <CommandHint command="wasm cert list" label={t("domains.fromTerminal")} />
        </>
      )}

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
    </div>
  );
}
