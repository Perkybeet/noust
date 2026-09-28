import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArchiveRestore } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { releasesQuery, rollbackPointsQuery } from "../../../api/queries/apps";
import type { Release, RollbackPoint } from "../../../api/queries/apps";
import { deploymentKeys } from "../../../api/queries/deployments";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useAppActions } from "../../apps/useAppActions";
import { releaseBadge, shortCommit } from "./words";

const LINK =
  "rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

function Panel({ children }: { children: ReactNode }) {
  return <div className="min-w-0 rounded-card border border-border bg-surface shadow-raised">{children}</div>;
}

function ListSkeleton() {
  return (
    <Panel>
      <div aria-hidden="true" className="flex flex-col divide-y divide-border">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-2 px-4 py-3">
            <Skeleton className="h-3 w-44" />
            <Skeleton className="h-3 w-28" />
          </div>
        ))}
      </div>
    </Panel>
  );
}

/** One release: its id, its commit, when it was built or began serving, and what can be done with it. */
function ReleaseRow({ release, active, onActivate, t }: { release: Release; active: Release | undefined; onActivate: (release: Release) => void; t: T }) {
  const badge = releaseBadge(t, release.active ? "active" : release.status);
  const commit = shortCommit(release.commit);
  const older = active !== undefined && release.id < active.id;
  let action: ReactNode = null;
  if (!release.active && release.on_disk) {
    action = (
      <Button size="sm" onClick={() => onActivate(release)}>
        {older ? t("appPages.common.rollBackToThis") : t("appPages.deployments.releases.activate")}
        <span className="sr-only">{t("appPages.deployments.releases.srRelease", { id: release.id })}</span>
      </Button>
    );
  }
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span translate="no" title={release.id} className="mono min-w-0 truncate text-12 text-fg">
          {release.id}
        </span>
        <Badge tone={badge.tone} className="shrink-0">
          {badge.label}
        </Badge>
      </div>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="flex min-w-0 items-baseline gap-2 text-12 text-fg-muted">
          {commit ? (
            <span translate="no" className="mono text-fg">
              {commit}
            </span>
          ) : null}
          {release.active && release.activated_at
            ? t.rich("appPages.deployments.releases.servingSince", { time: <RelativeTime value={release.activated_at} /> })
            : t.rich("appPages.deployments.releases.built", { time: <RelativeTime value={release.created_at} /> })}
        </span>
        {action ?? (!release.on_disk ? <span className="text-12 text-fg-faint">{t("appPages.deployments.releases.removedFromDisk")}</span> : null)}
      </div>
    </li>
  );
}

/** Confirms switching to a release, then does it: seconds, not a job. */
function ActivateDialog({ domain, release, older, onClose, t }: { domain: string; release: Release | null; older: boolean; onClose: () => void; t: T }) {
  const queryClient = useQueryClient();
  const { activateRelease } = useAppActions(domain);
  const close = (): void => {
    if (activateRelease.isPending) return;
    activateRelease.reset();
    onClose();
  };
  const commit = shortCommit(release?.commit);
  return (
    <Dialog
      open={release !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      size="sm"
      title={older ? t("appPages.deployments.releases.rollbackTitle") : t("appPages.deployments.releases.activateTitle")}
      description={
        commit
          ? t("appPages.deployments.releases.activateDescriptionWithCommit", { domain, id: release?.id ?? "", commit })
          : t("appPages.deployments.releases.activateDescriptionNoCommit", { domain, id: release?.id ?? "" })
      }
      footer={
        <>
          <Button disabled={activateRelease.isPending} onClick={close}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={activateRelease.isPending}
            onClick={() => {
              if (release === null) return;
              activateRelease.mutate(release.id, {
                onSuccess: () => {
                  onClose();
                  activateRelease.reset();
                },
                onSettled: () => {
                  // The switch is recorded as a deploy of its own.
                  void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
                },
              });
            }}
          >
            {older ? t("appPages.common.rollBack") : t("appPages.deployments.releases.activate")}
          </Button>
        </>
      }
    >
      {activateRelease.isError ? (
        <ErrorBlock
          live
          compact
          error={activateRelease.error}
          title={older ? t("appPages.deployments.releases.rollbackFailedTitle") : t("appPages.deployments.releases.activateFailedTitle")}
        />
      ) : null}
    </Dialog>
  );
}

function Releases({ domain, releases, t }: { domain: string; releases: Release[]; t: T }) {
  const [chosen, setChosen] = useState<Release | null>(null);
  const active = releases.find((release) => release.active);
  return (
    <>
      {releases.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("appPages.deployments.releases.noReleasesYet")}</p>
      ) : (
        <Panel>
          <ul aria-label={t("appPages.deployments.releases.ariaLabel", { domain })} className="flex flex-col divide-y divide-border">
            {releases.map((release) => (
              <ReleaseRow key={release.id} release={release} active={active} onActivate={setChosen} t={t} />
            ))}
          </ul>
        </Panel>
      )}
      <ActivateDialog
        domain={domain}
        release={chosen}
        older={chosen !== null && active !== undefined && chosen.id < active.id}
        onClose={() => {
          setChosen(null);
        }}
        t={t}
      />
    </>
  );
}

function PointRow({ point, onRestore, t }: { point: RollbackPoint; onRestore: (point: RollbackPoint) => void; t: T }) {
  const commit = shortCommit(point.git_commit);
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <span translate="no" title={point.id} className="mono min-w-0 truncate text-12 text-fg">
        {point.id}
      </span>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-12 text-fg-muted">
          {commit ? (
            <span translate="no" className="mono text-fg">
              {commit}
            </span>
          ) : null}
          <RelativeTime value={point.created_at} />
          <span className="text-fg-faint">{`${point.description}, ${formatBytes(point.size_bytes, t.locale)}`}</span>
        </span>
        <Button size="sm" onClick={() => onRestore(point)}>
          {t("appPages.common.rollBackToThis")}
          <span className="sr-only">{t("appPages.deployments.releases.srBackup", { id: point.id })}</span>
        </Button>
      </div>
    </li>
  );
}

function RestoreDialog({ domain, point, onClose, t }: { domain: string; point: RollbackPoint | null; onClose: () => void; t: T }) {
  const { rollbackToBackup } = useAppActions(domain);
  const close = (): void => {
    if (rollbackToBackup.isPending) return;
    rollbackToBackup.reset();
    onClose();
  };
  return (
    <Dialog
      open={point !== null}
      onOpenChange={(open) => {
        if (!open) close();
      }}
      size="sm"
      title={t("appPages.deployments.releases.restoreTitle")}
      description={t("appPages.deployments.releases.restoreDescription", { domain, id: point?.id ?? "" })}
      footer={
        <>
          <Button disabled={rollbackToBackup.isPending} onClick={close}>
            {t("appPages.common.cancel")}
          </Button>
          <Button
            variant="primary"
            loading={rollbackToBackup.isPending}
            onClick={() => {
              if (point === null) return;
              rollbackToBackup.mutate(point.id, {
                onSuccess: () => {
                  onClose();
                  rollbackToBackup.reset();
                },
              });
            }}
          >
            {t("appPages.common.rollBack")}
          </Button>
        </>
      }
    >
      {rollbackToBackup.isError ? <ErrorBlock live compact error={rollbackToBackup.error} title={t("appPages.rollback.notStarted")} /> : null}
    </Dialog>
  );
}

function RollbackPoints({ domain, t }: { domain: string; t: T }) {
  const points = useQuery(rollbackPointsQuery(domain));
  const [chosen, setChosen] = useState<RollbackPoint | null>(null);
  return (
    <Section
      title={t("appPages.deployments.releases.rollbackPointsTitle")}
      description={t.rich("appPages.deployments.releases.rollbackPointsDescription", {
        enableReleases: (
          <Link to="/apps/$domain/settings" params={{ domain }} className={LINK}>
            {t("appPages.deployments.releases.enableReleases")}
          </Link>
        ),
      })}
    >
      {points.isError && points.data === undefined ? (
        <ErrorBlock compact error={points.error} title={t("appPages.deployments.releases.backupsLoadError")} onRetry={() => void points.refetch()} retrying={points.isRefetching} />
      ) : points.data === undefined ? (
        <ListSkeleton />
      ) : points.data.items.length === 0 ? (
        <div className="flex flex-col items-start gap-2 rounded-card border border-dashed border-border px-4 py-4">
          <ArchiveRestore aria-hidden="true" className="size-4 text-fg-faint" />
          <p className="text-13 text-pretty text-fg-muted">
            {t.rich("appPages.deployments.releases.noBackupsYet", {
              backupsLink: (
                <Link to="/backups" className={LINK}>
                  {t("nav.backups.label")}
                </Link>
              ),
            })}
          </p>
        </div>
      ) : (
        <Panel>
          <ul aria-label={t("appPages.deployments.releases.backupsAriaLabel", { domain })} className="flex flex-col divide-y divide-border">
            {points.data.items.map((point) => (
              <PointRow key={point.id} point={point} onRestore={setChosen} t={t} />
            ))}
          </ul>
        </Panel>
      )}
      <RestoreDialog
        domain={domain}
        point={chosen}
        onClose={() => {
          setChosen(null);
        }}
        t={t}
      />
    </Section>
  );
}

/**
 * What the app can go back to. An app on releases switches to an earlier build in seconds; an
 * app deployed in place (or one the API finds in place, whatever it is listed as) restores a
 * backup, as a job.
 */
export function ReleasesSection({ domain, layout }: { domain: string; layout: string }) {
  const t = useT();
  const releases = useQuery({ ...releasesQuery(domain), enabled: layout === "releases" });
  if (layout !== "releases" || releases.data === null) return <RollbackPoints domain={domain} t={t} />;
  return (
    <Section title={t("appPages.deployments.releases.title")} description={t("appPages.deployments.releases.description")}>
      {releases.isError && releases.data === undefined ? (
        <ErrorBlock compact error={releases.error} title={t("appPages.deployments.releases.releasesLoadError")} onRetry={() => void releases.refetch()} retrying={releases.isRefetching} />
      ) : releases.data === undefined ? (
        <ListSkeleton />
      ) : (
        <Releases domain={domain} releases={releases.data.items} t={t} />
      )}
    </Section>
  );
}
