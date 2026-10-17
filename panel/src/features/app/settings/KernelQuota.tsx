/**
 * The disk limit of an app, given to the kernel as a project quota (checklist B-63). Noust's own
 * limit is checked before a deploy and never stops the app from writing; on a filesystem the
 * operator mounted with project quotas, the kernel can hold the same limit and refuse the writes
 * themselves. Setting it walks every file of the app's folder, so it runs as a job, behind sudo
 * mode; Noust never mounts or tunes a filesystem, so the offer is only made on one that has
 * project quotas already.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { diskKey } from "../../../api/queries/disk";
import type { AppDisk } from "../../../api/queries/disk";
import type { Job } from "../../../api/queries/jobs";
import { announce } from "../../../app/Announcer";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { useSudoFirst } from "./formParts";
import { JobFailedError, waitForJob } from "./queries";

/** What the dialog is asking: to give the limit to the kernel, or to take it back. */
type Asking = "give" | "take" | null;

export function KernelQuota({ disk, domain }: { disk: AppDisk; domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const sudoFirst = useSudoFirst();
  const [asking, setAsking] = useState<Asking>(null);
  const kernel = disk.kernel;
  const possible = disk.filesystem.project_quota === "enabled";

  // Nothing to offer on a filesystem without project quotas, and nothing to take back when none was given.
  if (!kernel.applied && !possible) return null;

  const giving = asking === "give";
  const limit = disk.soft_mb;
  const change = async (enabled: boolean): Promise<void> => {
    const queued = await request("put", "/api/apps/{domain}/disk/kernel", { params: { domain }, body: { enabled } });
    const job = await waitForJob(queryClient, queued.job as unknown as Job);
    void queryClient.invalidateQueries({ queryKey: diskKey(domain) });
    if (job.status !== "completed") throw new JobFailedError(job.error ?? t("appSettings.jobFailedSilently"));
    announce(enabled ? t("runtime.disk.kernel.given") : t("runtime.disk.kernel.taken"));
  };

  return (
    <Subsection title={t("runtime.disk.kernel.title")} className="border-t border-border pt-5">
      {kernel.applied ? (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {t("runtime.disk.kernel.held", {
            project: String(kernel.project ?? ""),
            mount: kernel.mount ?? "/",
            limit: String(kernel.hard_mb ?? ""),
            used: kernel.used_bytes === null ? "?" : formatBytes(kernel.used_bytes, t.locale),
          })}
        </p>
      ) : (
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{limit === null ? t("runtime.disk.kernel.needLimit") : t("runtime.disk.kernel.offer", { limit: String(limit) })}</p>
      )}
      <div>
        {kernel.applied ? (
          <Button variant="secondary" onClick={() => sudoFirst(() => setAsking("take"), t("runtime.disk.kernel.failed"))}>
            {t("runtime.disk.kernel.takeAction")}
          </Button>
        ) : (
          <Button variant="secondary" disabled={limit === null} onClick={() => sudoFirst(() => setAsking("give"), t("runtime.disk.kernel.failed"))}>
            {t("runtime.disk.kernel.giveAction")}
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={asking !== null}
        onOpenChange={(open) => {
          if (!open) setAsking(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={giving ? t("runtime.disk.kernel.giveTitle", { domain }) : t("runtime.disk.kernel.takeTitle", { domain })}
        description={giving ? t("runtime.disk.kernel.giveDescription", { limit: String(limit ?? "") }) : t("runtime.disk.kernel.takeDescription")}
        actionLabel={giving ? t("runtime.disk.kernel.giveAction") : t("runtime.disk.kernel.takeAction")}
        onConfirm={() => change(giving)}
      />
    </Subsection>
  );
}
