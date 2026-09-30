import { CirclePause, CirclePlay, History, Pencil, Play } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import type { CronJob } from "./data";
import { useCronActions } from "./useCronActions";

export interface CronJobRowActionsProps {
  job: CronJob;
  onEdit: (job: CronJob) => void;
  onViewRuns: (name: string) => void;
}

/** The menu at the end of a cron job's row: run it now, its history, edit, pause or resume, delete. */
export function CronJobRowActions({ job, onEdit, onViewRuns }: CronJobRowActionsProps) {
  const t = useT();
  const { run, enable, disable, remove } = useCronActions();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const name = job.name;

  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("cron.table.actionsFor", { name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
        <MenuItem icon={<Play />} disabled={run.isPending} onClick={() => run.mutate(name)}>
          {t("cron.actions.runNow")}
        </MenuItem>
        <MenuItem icon={<History />} onClick={() => onViewRuns(name)}>
          {t("cron.actions.viewRuns")}
        </MenuItem>
        <MenuItem icon={<Pencil />} onClick={() => onEdit(job)}>
          {t("cron.common.edit")}
        </MenuItem>
        <MenuSeparator />
        {job.enabled ? (
          <MenuItem icon={<CirclePause />} disabled={disable.isPending} onClick={() => disable.mutate(name)}>
            {t("cron.actions.disable")}
          </MenuItem>
        ) : (
          <MenuItem icon={<CirclePlay />} disabled={enable.isPending} onClick={() => enable.mutate(name)}>
            {t("cron.actions.enable")}
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem icon={<ICONS.delete />} destructive onClick={() => setDeleteOpen(true)}>
          {t("cron.actions.deleteJob")}
        </MenuItem>
      </Menu>

      {/* One question: a job can be made again, and the runs it already made are in the journal. */}
      <ConfirmDialog
        friction="simple"
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("cron.deleteDialog.title", { name })}
        description={t("cron.deleteDialog.description")}
        actionLabel={t("cron.actions.deleteJob")}
        onConfirm={async () => {
          await remove.mutateAsync(name);
        }}
      />
    </>
  );
}
