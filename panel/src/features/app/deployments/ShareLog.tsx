import { useMutation } from "@tanstack/react-query";
import { Check, Share2 } from "lucide-react";
import { useId } from "react";

import { request } from "../../../api/client";
import { Button } from "../../../components/ui/Button";
import { useCopyState } from "../../../components/ui/useCopyState";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";

/** Bytes asked for when the copy is of the whole log: more than any build writes. */
const WHOLE_LOG = 64 * 1024 * 1024;

/**
 * A copy of a deploy's whole log that is safe to share. It is asked of the server, which masks
 * anything that looks like a credential and strips the colour codes, so what leaves the console
 * is decided in one place. The level and the times are the log viewer's own (its chips and its
 * toggle); this copies the whole log.
 */
export function ShareLog({ deploymentId }: { deploymentId: number }) {
  const t = useT();
  const { state, copy } = useCopyState(2000);
  const describedBy = useId();
  const share = useMutation({
    mutationFn: () =>
      request("get", "/api/deployments/{deployment_id}/log", {
        params: { deployment_id: deploymentId },
        query: { tail: WHOLE_LOG, level: "all", timestamps: true, sanitize: true },
      }),
    onSuccess: (log) => copy(log.content),
    onError: (error) => {
      reportActionError(t("runtime.deployLog.shareFailed"), error);
    },
  });
  return (
    <div>
      <Button
        size="sm"
        variant="ghost"
        loading={share.isPending}
        icon={state === "copied" ? <Check aria-hidden="true" className="text-ok" /> : <Share2 aria-hidden="true" />}
        aria-describedby={describedBy}
        onClick={() => share.mutate()}
      >
        {t("runtime.deployLog.share")}
      </Button>
      <span id={describedBy} className="sr-only">
        {t("runtime.deployLog.shareDescription")}
      </span>
      <span role="status" className="sr-only">
        {state === "copied" ? t("runtime.deployLog.shareCopied") : ""}
      </span>
    </div>
  );
}
