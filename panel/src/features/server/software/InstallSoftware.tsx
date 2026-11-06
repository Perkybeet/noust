/**
 * Installing a piece of the server's software from its row (item 118). One press starts the task, which the Server
 * area's job slot and the task centre both follow; the one piece of the question that exists is for what comes from
 * EPEL on a RHEL rebuild, a repository that is not the distribution's, which is the operator's yes and never Noust's
 * own initiative.
 */

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { useCan } from "../../../components/page/permissions";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { explainServerError, useServerConfirm } from "../errors";
import { useServerJob } from "../serverJob";
import { installSoftware } from "./queries";
import type { SoftwareItem } from "./queries";
import { comesFromEpel, installPermissions } from "./registry";
import { componentName } from "./words";

/** The question EPEL asks before an install: what it is, and the box that says yes to it. */
function EpelQuestion({ item, onClose }: { item: SoftwareItem; onClose: () => void }) {
  const t = useT();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const [epel, setEpel] = useState(false);
  const name = componentName(t, item.id);
  return (
    <ConfirmDialog
      {...confirmation}
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      title={t("software.install.epelTitle", { name })}
      description={t("software.install.epelDescription", { name })}
      actionLabel={t("software.install.action", { name })}
      destructive={false}
      onConfirm={async () => {
        const accepted = await installSoftware(item.id, epel);
        jobs.track(accepted.job_id, "software");
      }}
    >
      <Checkbox checked={epel} onCheckedChange={setEpel} label={t("software.install.epelLabel")} description={t("software.install.epelHelp")} />
    </ConfirmDialog>
  );
}

/**
 * What pressing "Install" on a row does. Most pieces start the task at once; one that comes from EPEL here asks
 * first. `dialog` is what the page draws, `install` is the press.
 */
export function useInstall(family: string): { install: (item: SoftwareItem) => void; dialog: ReactNode } {
  const t = useT();
  const { node } = useNode();
  const jobs = useServerJob();
  const can = useCan();
  const [asking, setAsking] = useState<SoftwareItem | null>(null);
  const start = useMutation({
    mutationFn: (item: SoftwareItem) => installSoftware(item.id, false),
    onSuccess: (accepted) => jobs.track(accepted.job_id, "software"),
    onError: (error, item) => reportActionError(t("software.install.failed", { name: componentName(t, item.id) }), explainServerError(t, error, node)),
  });
  return {
    install: (item) => {
      // The page draws the button for whoever may; the press asks again, since this is where the install is made.
      if (!installPermissions(item.id).every(can)) return;
      if (comesFromEpel(item.id, family)) setAsking(item);
      else start.mutate(item);
    },
    dialog: asking === null ? null : <EpelQuestion item={asking} onClose={() => setAsking(null)} />,
  };
}
