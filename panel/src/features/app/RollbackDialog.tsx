import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { releasesQuery, rollbackPointsQuery } from "../../api/queries/apps";
import type { Job } from "../../api/queries/jobs";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatBytes } from "../../lib/format";
import { useAppActions } from "../apps/useAppActions";

interface Target {
  id: string;
  title: string;
  commit: string | null;
  when: string | null;
  note: string | null;
}

function Options({
  name,
  targets,
  value,
  onChange,
  legend,
}: {
  name: string;
  targets: readonly Target[];
  value: string | null;
  onChange: (id: string) => void;
  legend: string;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-2 text-13 text-fg-muted">{legend}</legend>
      {targets.map((target) => (
        <label
          key={target.id}
          className={cx(
            "grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-0.5 rounded-control border px-3 py-2.5",
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
            value === target.id ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-hover",
          )}
        >
          <input
            type="radio"
            name={name}
            value={target.id}
            checked={value === target.id}
            onChange={() => onChange(target.id)}
            className="row-span-2 mt-0.5 size-4 shrink-0 accent-accent"
          />
          <span translate="no" className="mono truncate text-12 text-fg">
            {target.title}
          </span>
          <span className="col-start-2 flex flex-wrap items-center gap-x-2 text-12 text-fg-muted">
            {target.commit ? <span className="mono">{target.commit.slice(0, 7)}</span> : null}
            {target.when ? <RelativeTime value={target.when} /> : null}
            {target.note ? <span>{target.note}</span> : null}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

export interface RollbackDialogProps {
  domain: string;
  /** The app's deploy layout: `releases`, or `inplace` for an app not migrated yet. */
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Follows the job a rollback from a backup queues; without it, a toast says it was queued. */
  onJobQueued?: (job: Job) => void;
  /** The target chosen when the dialog opens: a release id, or a backup id. */
  preselect?: string | null;
}

/**
 * Puts an earlier version of the app back. An app on releases switches to a previous release
 * in seconds; an app deployed in place is restored from one of its backups, as a job.
 */
export function RollbackDialog({ domain, layout, open, onOpenChange, onJobQueued, preselect = null }: RollbackDialogProps) {
  const t = useT();
  const name = useId();
  const [choice, setChoice] = useState<string | null>(preselect);
  // Opening again starts from the preselected target, adjusted while rendering.
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) setChoice(preselect);
  }
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
        title: point.id,
        commit: point.git_commit ?? null,
        when: point.created_at,
        note: `${point.description}, ${formatBytes(point.size_bytes, t.locale)}`,
      }))
    : releases.data?.items
        .filter((release) => !release.active && release.on_disk)
        .map((release) => ({
          id: release.id,
          title: release.id,
          commit: release.commit ?? null,
          when: release.activated_at ?? release.created_at,
          note: release.status,
        }));

  const loading = inPlace ? points.isPending : releases.isPending;
  const loadError = !inPlace && releases.isError ? releases.error : inPlace && points.isError ? points.error : null;

  const close = (next: boolean): void => {
    if (!next && action.isPending) return;
    onOpenChange(next);
    if (!next) {
      setChoice(null);
      action.reset();
    }
  };

  const confirm = (): void => {
    if (choice === null) return;
    action.mutate(choice, {
      onSuccess: () => {
        close(false);
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
    body = <p className="text-14 text-fg-muted">{inPlace ? t("appPages.rollback.noBackups") : t("appPages.rollback.noReleases")}</p>;
  } else {
    body = (
      <Options
        name={name}
        targets={targets}
        value={choice}
        onChange={setChoice}
        legend={inPlace ? t("appPages.rollback.restoreFromBackupLegend") : t("appPages.rollback.switchToReleaseLegend")}
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
        <>
          <Button disabled={action.isPending} onClick={() => close(false)}>
            {t("appPages.common.cancel")}
          </Button>
          <Button variant="primary" disabled={choice === null} loading={action.isPending} onClick={confirm}>
            {t("appPages.common.rollBack")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {body}
        {action.isError ? <ErrorBlock live compact error={action.error} title={t("appPages.rollback.notStarted")} /> : null}
      </div>
    </Dialog>
  );
}
