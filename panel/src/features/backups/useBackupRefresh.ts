import { useQueryClient } from "@tanstack/react-query";

import { backupKeys } from "../../api/queries/backups";
import { isJobFinished } from "../../api/queries/jobs";
import { useServerEvent } from "../../realtime/events";

/**
 * The backup jobs the backend runs (its JobType values): a local archive (`backup`), putting
 * one back (`restore`, whether from local storage or downloaded from a destination first) and
 * uploading one to a destination (`push`).
 */
export const BACKUP_JOB_TYPES: ReadonlySet<string> = new Set(["backup", "restore", "push"]);

/**
 * Refreshes the backups list and the storage figure whenever a backup, restore or push job
 * ends, whoever queued it: `create`, `restore` and `push` in `useBackupActions` only queue the
 * job and toast that it started, so without this the table and the storage bar stay exactly as
 * they were until something else happens to refetch them - the same fix `useCertificateRefresh`
 * makes for certificate jobs, and for the same reason: `isJobFinished` is the one place "has
 * this job ended" is decided, the same test `useFollowedJob`'s own polling relies on.
 */
export function useBackupRefresh(): void {
  const queryClient = useQueryClient();
  useServerEvent("job", (job) => {
    if (!BACKUP_JOB_TYPES.has(job.type) || !isJobFinished(job)) return;
    void queryClient.invalidateQueries({ queryKey: backupKeys.all });
    void queryClient.invalidateQueries({ queryKey: backupKeys.storage });
  });
}
