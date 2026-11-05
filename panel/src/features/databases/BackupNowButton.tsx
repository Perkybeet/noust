import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { jobKeys } from "../../api/queries/jobs";
import { GuardedButton } from "../../components/page/permissions";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useDatabaseJob } from "./jobs";

/**
 * "Back up now" of one database: the dump is a job, followed in the page's job slot whichever section asked for it. The one
 * place it is written, for the Overview's primary action and the Backups section's.
 */
export function BackupNowButton({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const job = useDatabaseJob();
  const backup = useMutation({
    mutationFn: () => request("post", "/api/databases/backup-policies/{engine}/{database}/run", { params: { engine, database: name } }),
    onSuccess: (accepted) => {
      job.track(accepted, "backup");
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    },
    onError: (error) => reportActionError(t("databases.list.backupFailed", { name }), error),
  });
  return (
    <GuardedButton
      permission="backups.run"
      variant="primary"
      icon={<OBJECT_ICONS.backup aria-hidden="true" />}
      loading={backup.isPending}
      // design-exception: submit-disabled-invalid a backup is already running: nothing to start, not a missing field
      disabled={job.busy}
      onClick={() => backup.mutate()}
    >
      {t("common.words.backUpNow")}
    </GuardedButton>
  );
}
