import { useQuery, useQueryClient } from "@tanstack/react-query";
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
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { versionTitle } from "../versionTitle";
import { useNode } from "../../../nodes/useNode";
import { useAppActions } from "../../apps/useAppActions";
import { schemaChangeRefusal } from "../schemaChange";
import { useSchemaChangeConfirmation } from "../SchemaChangeDialog";
import { releaseBadge, shortCommit } from "./words";

function ListSkeleton() {
  return (
    <Card padding="none">
      <div aria-busy="true" className="flex flex-col divide-y divide-border">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex flex-col gap-2 px-4 py-3">
            <Skeleton className="h-3 w-44" />
            <Skeleton className="h-3 w-28" />
          </div>
        ))}
      </div>
    </Card>
  );
}

/** When a version was made, as the title of its row: a date people read, not an id. */
function madeAt(t: T, when: string | null | undefined): string | null {
  return versionTitle(when, t.locale);
}

/**
 * One version: when it was built and from which commit, whether it is live, and going back to
 * it. The release id, which the command line and the disk use, is the row's second line.
 */
function ReleaseRow({ release, active, onActivate, t }: { release: Release; active: Release | undefined; onActivate: (release: Release) => void; t: T }) {
  const badge = releaseBadge(t, release.active ? "active" : release.status);
  const commit = shortCommit(release.commit);
  const older = active !== undefined && release.id < active.id;
  const title = madeAt(t, release.created_at) ?? release.id;
  let action: ReactNode = null;
  if (!release.active && release.on_disk) {
    action = (
      <Button size="sm" onClick={() => onActivate(release)}>
        {older ? t("appPages.deployments.releases.goBack") : t("appPages.deployments.releases.activate")}
        <span className="sr-only">{t("appPages.deployments.releases.srRelease", { id: release.id })}</span>
      </Button>
    );
  }
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span className="flex min-w-0 items-baseline gap-2 text-13 font-medium text-fg">
          <span className="truncate">{title}</span>
          {commit ? <Mono tone="muted">{commit}</Mono> : null}
        </span>
        <Badge tone={badge.tone} className="shrink-0">
          {badge.label}
        </Badge>
      </div>
      <span className="min-w-0 text-12">
        <Mono tone="faint" truncate title={release.id}>
          {release.id}
        </Mono>
      </span>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="text-12 text-fg-muted">
          {release.active && release.activated_at
            ? t.rich("appPages.deployments.releases.liveSince", { time: <RelativeTime value={release.activated_at} /> })
            : t.rich("appPages.deployments.releases.built", { time: <RelativeTime value={release.created_at} /> })}
        </span>
        {action ?? (!release.on_disk ? <span className="text-12 text-fg-muted">{t("appPages.deployments.releases.removedFromDisk")}</span> : null)}
      </div>
    </li>
  );
}

function Releases({ domain, releases, t }: { domain: string; releases: Release[]; t: T }) {
  const queryClient = useQueryClient();
  const { node } = useNode();
  const { activateRelease } = useAppActions(domain);
  const schema = useSchemaChangeConfirmation(domain);
  const [chosen, setChosen] = useState<Release | null>(null);
  const active = releases.find((release) => release.active);
  const older = chosen !== null && active !== undefined && chosen.id < active.id;
  const commit = shortCommit(chosen?.commit);
  return (
    <>
      {releases.length === 0 ? (
        <EmptyState variant="inline" title={t("appPages.deployments.releases.noReleasesYet")} />
      ) : (
        <Card padding="none">
          <ul aria-label={t("appPages.deployments.releases.ariaLabel", { domain })} className="flex flex-col divide-y divide-border">
            {releases.map((release) => (
              <ReleaseRow key={release.id} release={release} active={active} onActivate={setChosen} t={t} />
            ))}
          </ul>
        </Card>
      )}
      <ConfirmDialog
        open={chosen !== null}
        onOpenChange={(open) => {
          if (!open) setChosen(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={older ? t("appPages.deployments.releases.rollbackTitle") : t("appPages.deployments.releases.activateTitle")}
        description={
          commit
            ? t("appPages.deployments.releases.activateDescriptionWithCommit", { domain, id: chosen?.id ?? "", commit })
            : t("appPages.deployments.releases.activateDescriptionNoCommit", { domain, id: chosen?.id ?? "" })
        }
        actionLabel={older ? t("appPages.deployments.releases.goBack") : t("appPages.deployments.releases.activate")}
        onConfirm={async () => {
          if (chosen === null) return;
          const id = chosen.id;
          const settle = (): void => {
            // The switch is recorded as a deploy of its own, whether it held or was put back.
            void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
          };
          try {
            await activateRelease.mutateAsync({ id });
          } catch (error) {
            // Past a schema change this question closes and the one that names them opens.
            if (!schema.intercept(schemaChangeRefusal(error), () => activateRelease.mutateAsync({ id, schemaChangedOk: true }).finally(settle))) throw error;
          } finally {
            settle();
          }
        }}
      />
      {schema.dialog}
    </>
  );
}

function PointRow({ point, onRestore, t }: { point: RollbackPoint; onRestore: (point: RollbackPoint) => void; t: T }) {
  const commit = shortCommit(point.git_commit);
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3">
      <span className="flex min-w-0 items-baseline gap-2 text-13 font-medium text-fg">
        <span className="truncate">{madeAt(t, point.created_at) ?? point.id}</span>
        {commit ? <Mono tone="muted">{commit}</Mono> : null}
      </span>
      <span className="min-w-0 text-12">
        <Mono tone="faint" truncate title={point.id}>
          {point.id}
        </Mono>
      </span>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="text-12 text-fg-muted">{`${point.description}, ${formatBytes(point.size_bytes, t.locale)}`}</span>
        <Button size="sm" onClick={() => onRestore(point)}>
          {t("appPages.deployments.releases.goBack")}
          <span className="sr-only">{t("appPages.deployments.releases.srBackup", { id: point.id })}</span>
        </Button>
      </div>
    </li>
  );
}

function RollbackPoints({ domain, t }: { domain: string; t: T }) {
  const { node } = useNode();
  const points = useQuery(rollbackPointsQuery(domain));
  const { rollbackToBackup } = useAppActions(domain);
  const schema = useSchemaChangeConfirmation(domain);
  const [chosen, setChosen] = useState<RollbackPoint | null>(null);
  return (
    <Section
      title={t("appPages.deployments.releases.rollbackPointsTitle")}
      description={t.rich("appPages.deployments.releases.rollbackPointsDescription", {
        enableReleases: (
          <TextLink to="/apps/$domain/settings/deploys" params={{ domain }}>
            {t("appPages.deployments.releases.enableReleases")}
          </TextLink>
        ),
      })}
    >
      {points.isError && points.data === undefined ? (
        <ErrorBlock compact error={points.error} title={t("appPages.deployments.releases.backupsLoadError")} onRetry={() => void points.refetch()} retrying={points.isRefetching} />
      ) : points.data === undefined ? (
        <ListSkeleton />
      ) : points.data.items.length === 0 ? (
        <EmptyState
          variant="inline"
          title={t("appPages.deployments.releases.noBackupsYet")}
          action={
            <TextLink to="/backups" size="ui">
              {t("appPages.deployments.releases.openBackups")}
            </TextLink>
          }
        />
      ) : (
        <Card padding="none">
          <ul aria-label={t("appPages.deployments.releases.backupsAriaLabel", { domain })} className="flex flex-col divide-y divide-border">
            {points.data.items.map((point) => (
              <PointRow key={point.id} point={point} onRestore={setChosen} t={t} />
            ))}
          </ul>
        </Card>
      )}
      <ConfirmDialog
        open={chosen !== null}
        onOpenChange={(open) => {
          if (!open) setChosen(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={t("appPages.deployments.releases.restoreTitle")}
        description={t("appPages.deployments.releases.restoreDescription", { domain, id: chosen?.id ?? "" })}
        actionLabel={t("appPages.deployments.releases.goBack")}
        onConfirm={async () => {
          if (chosen === null) return;
          const id = chosen.id;
          try {
            await rollbackToBackup.mutateAsync({ id });
          } catch (error) {
            if (!schema.intercept(schemaChangeRefusal(error), () => rollbackToBackup.mutateAsync({ id, schemaChangedOk: true }))) throw error;
          }
        }}
      />
      {schema.dialog}
    </Section>
  );
}

/**
 * What the app can go back to. An app with instant rollback switches to an earlier version in
 * seconds; an app kept in a single folder (or one the API finds that way, whatever it is listed
 * as) restores a backup, as a job.
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
