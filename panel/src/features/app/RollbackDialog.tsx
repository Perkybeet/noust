import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { releasesQuery, rollbackPointsQuery } from "../../api/queries/apps";
import type { Job } from "../../api/queries/jobs";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useAppActions } from "../apps/useAppActions";
import { versionTitle } from "./versionTitle";

interface Target {
  id: string;
  /** When it was made, as a person reads it; the id when the time is unknown. */
  title: string;
  commit: string | null;
  when: string | null;
  note: string | null;
}

function titleOf(t: T, when: string | null | undefined, fallback: string): string {
  return versionTitle(when, t.locale) ?? fallback;
}

/**
 * The versions the app can go back to, each a row with its own button: choosing one is going
 * back to it, the dialog having been the question. The id the disk and the command line use is
 * each row's second line.
 */
function Targets({
  label,
  targets,
  pending,
  onChoose,
  t,
}: {
  label: string;
  targets: readonly Target[];
  pending: string | null;
  onChoose: (id: string) => void;
  t: T;
}) {
  return (
    <ul aria-label={label} className="flex flex-col divide-y divide-border rounded-control border border-border">
      {targets.map((target) => (
        <li key={target.id} className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-2 px-3 py-2.5">
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex min-w-0 items-baseline gap-2 text-13 font-medium text-fg">
              <span className="truncate">{target.title}</span>
              {target.commit ? <Mono tone="muted">{target.commit.slice(0, 7)}</Mono> : null}
            </span>
            <span className="flex min-w-0 flex-wrap items-center gap-x-2 text-12 text-fg-muted">
              <Mono tone="faint" truncate title={target.id}>
                {target.id}
              </Mono>
              {target.when ? <RelativeTime value={target.when} /> : null}
              {target.note ? <span>{target.note}</span> : null}
            </span>
          </span>
          <Button
            size="sm"
            aria-label={t("appPages.rollback.goBackTo", { id: target.id })}
            loading={pending === target.id}
            disabled={pending !== null && pending !== target.id}
            onClick={() => onChoose(target.id)}
          >
            {t("appPages.rollback.goBack")}
          </Button>
        </li>
      ))}
    </ul>
  );
}

export interface RollbackDialogProps {
  domain: string;
  /** The app's deploy layout: `releases`, or `inplace` for an app kept in a single folder. */
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Follows the job a rollback from a backup queues; without it, a toast says it was queued. */
  onJobQueued?: (job: Job) => void;
}

/**
 * Puts an earlier version of the app back. An app with instant rollback switches to an earlier
 * version in seconds; an app kept in a single folder is restored from one of its backups, as a
 * job. Only what can be gone back to is listed: the version live now is not.
 */
export function RollbackDialog({ domain, layout, open, onOpenChange, onJobQueued }: RollbackDialogProps) {
  const t = useT();
  const [chosen, setChosen] = useState<string | null>(null);
  const onReleases = layout === "releases";
  const releases = useQuery({ ...releasesQuery(domain), enabled: open && onReleases });
  // The API also answers "no releases" for an app it finds in place, whatever it was listed as.
  const inPlace = !onReleases || releases.data === null;
  const points = useQuery({ ...rollbackPointsQuery(domain), enabled: open && inPlace });
  const { activateRelease, rollbackToBackup } = useAppActions(domain, onJobQueued ? { onJobQueued } : {});
  const action = inPlace ? rollbackToBackup : activateRelease;

  const targets: Target[] | undefined = inPlace
    ? points.data?.items.map((point) => ({
        id: point.id,
        title: titleOf(t, point.created_at, point.id),
        commit: point.git_commit ?? null,
        when: null,
        note: `${point.description}, ${formatBytes(point.size_bytes, t.locale)}`,
      }))
    : releases.data?.items
        .filter((release) => !release.active && release.on_disk)
        .map((release) => ({
          id: release.id,
          title: titleOf(t, release.created_at, release.id),
          commit: release.commit ?? null,
          when: release.activated_at ?? null,
          note: null,
        }));

  const loading = inPlace ? points.isPending : releases.isPending;
  const loadError = !inPlace && releases.isError ? releases.error : inPlace && points.isError ? points.error : null;

  const close = (next: boolean): void => {
    if (!next && action.isPending) return;
    onOpenChange(next);
    if (!next) {
      setChosen(null);
      action.reset();
    }
  };

  const choose = (id: string): void => {
    setChosen(id);
    action.mutate(id, {
      onSuccess: () => {
        close(false);
      },
      onSettled: () => {
        setChosen(null);
      },
    });
  };

  let body;
  if (loadError !== null) {
    body = <ErrorBlock compact error={loadError} title={t("appPages.rollback.listError")} />;
  } else if (loading || targets === undefined) {
    body = (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">{t("appPages.rollback.loading")}</span>
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-14 rounded-control" />
        ))}
      </div>
    );
  } else if (targets.length === 0) {
    body = <EmptyState variant="inline" title={inPlace ? t("appPages.rollback.noBackups") : t("appPages.rollback.noReleases")} />;
  } else {
    body = (
      <Targets
        label={inPlace ? t("appPages.rollback.backupsLabel") : t("appPages.rollback.releasesLabel")}
        targets={targets}
        pending={chosen}
        onChoose={choose}
        t={t}
      />
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t("appPages.rollback.title", { domain })}
      description={inPlace ? t("appPages.rollback.inPlaceDescription") : t("appPages.rollback.releasesDescription")}
      footer={
        <Button disabled={action.isPending} onClick={() => close(false)}>
          {t("appPages.common.cancel")}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        {body}
        {action.isError ? <ErrorBlock live compact error={action.error} title={t("appPages.rollback.notStarted")} /> : null}
      </div>
    </Dialog>
  );
}
