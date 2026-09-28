import { History, MoreHorizontal, Pencil, Play, ToggleLeft, ToggleRight, Trash2 } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import type { CronJob } from "./data";
import { useCronActions } from "./useCronActions";

export interface CronJobRowActionsProps {
  job: CronJob;
  onEdit: (job: CronJob) => void;
  onViewRuns: (name: string) => void;
}

/** The menu at the end of a cron job's row: run it now, edit, enable/disable, its history, delete. */
export function CronJobRowActions({ job, onEdit, onViewRuns }: CronJobRowActionsProps) {
  const t = useT();
  const { run, enable, disable, remove } = useCronActions();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const name = job.name;

  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("cron.table.actionsFor", { name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}>
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
          <MenuItem icon={<ToggleLeft />} disabled={disable.isPending} onClick={() => disable.mutate(name)}>
            {t("cron.actions.disable")}
          </MenuItem>
        ) : (
          <MenuItem icon={<ToggleRight />} disabled={enable.isPending} onClick={() => enable.mutate(name)}>
            {t("cron.actions.enable")}
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem icon={<Trash2 />} destructive onClick={() => setDeleteOpen(true)}>
          {t("cron.actions.deleteJob")}
        </MenuItem>
      </Menu>

      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("cron.deleteDialog.title", { name })}
        description={t("cron.deleteDialog.description")}
        confirmText={name}
        actionLabel={t("cron.actions.deleteJob")}
        onConfirm={async () => {
          await remove.mutateAsync(name);
        }}
      />
    </>
  );
}
