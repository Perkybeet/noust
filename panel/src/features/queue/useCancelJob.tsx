import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { deploymentKeys } from "../../api/queries/deployments";
import { jobKeys } from "../../api/queries/jobs";
import { queueKeys } from "../../api/queries/queue";
import { announce } from "../../app/Announcer";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

/** The job a Cancel button was pressed on: what to name it, and what stopping it comes to. */
export interface CancelTarget {
  id: string;
  /** What the job is, as a sentence: "Deploying shop.example.com". */
  name: string;
  /** The application it acts on, when it acts on one. */
  domain: string | null;
  /** `queued` takes it out of the line; `running` stops a build. */
  stage: "queued" | "running";
}

export interface CancelJob {
  /** Opens the question for a job. */
  ask: (target: CancelTarget) => void;
  /** The question itself; render it once, anywhere on the page. */
  dialog: ReactNode;
}

/**
 * Cancelling a deploy, an update or a job that waits, with the one question that says what it
 * does. Stopping a job loses no data (a build runs beside what serves and is removed; a job
 * that waits has done nothing), so the question is a single, plain one that opens on Cancel,
 * and a refusal (its release started to be activated meanwhile) is shown in the dialog in the
 * server's own words, which stays open.
 */
export function useCancelJob(): CancelJob {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState<CancelTarget | null>(null);

  const dialog =
    target === null ? null : (
      <ConfirmDialog
        open
        onOpenChange={(open) => {
          if (!open) setTarget(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={target.stage === "queued" ? t("queue.cancel.queued.title", { name: target.name }) : t("queue.cancel.running.title", { domain: target.domain ?? target.name })}
        description={target.stage === "queued" ? t("queue.cancel.queued.description") : t("queue.cancel.running.description")}
        actionLabel={target.stage === "queued" ? t("queue.cancel.queued.action") : t("queue.cancel.running.action")}
        onConfirm={async () => {
          const outcome = await request("post", "/api/jobs/{job_id}/cancel", { params: { job_id: target.id } });
          // What it changed: the line moved, the job (and the deployment it was making) ended.
          void queryClient.invalidateQueries({ queryKey: queueKeys.all });
          void queryClient.invalidateQueries({ queryKey: jobKeys.all });
          void queryClient.invalidateQueries({ queryKey: deploymentKeys.all });
          if (target.domain !== null) void queryClient.invalidateQueries({ queryKey: appKeys.detail(target.domain) });
          announce(outcome.state === "cancelling" ? t("queue.cancel.done.stopping", { name: target.name }) : t("queue.cancel.done.queued", { name: target.name }));
        }}
      />
    );

  return { ask: setTarget, dialog };
}
