import { useMutation } from "@tanstack/react-query";
import { Check, Share2 } from "lucide-react";
import { useId } from "react";

import { request } from "../../../api/client";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { useCopyState } from "../../../components/ui/useCopyState";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import type { LogLevelFilter } from "./buildLog";

/** Bytes asked for when the copy is of the whole log: more than any build writes. */
const WHOLE_LOG = 64 * 1024 * 1024;

export interface LogControlsProps {
  deploymentId: number;
  level: LogLevelFilter;
  onLevel: (level: LogLevelFilter) => void;
  times: boolean;
  onTimes: (times: boolean) => void;
}

/**
 * What a deploy's log is read as: at which level, with or without the times, and a copy that is
 * safe to share. The copy is asked of the server, which filters by the same words the viewer
 * colours with and masks anything that looks like a credential, so what leaves the console is
 * decided in one place.
 */
export function LogControls({ deploymentId, level, onLevel, times, onTimes }: LogControlsProps) {
  const t = useT();
  const { state, copy } = useCopyState(2000);
  const describedBy = useId();
  const share = useMutation({
    mutationFn: () =>
      request("get", "/api/deployments/{deployment_id}/log", {
        params: { deployment_id: deploymentId },
        query: { tail: WHOLE_LOG, level, timestamps: times, sanitize: true },
      }),
    onSuccess: (log) => copy(log.content),
    onError: (error) => {
      reportActionError(t("runtime.deployLog.shareFailed"), error);
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <SegmentedControl<LogLevelFilter>
        label={t("runtime.deployLog.levelLabel")}
        value={level}
        onValueChange={onLevel}
        options={[
          { value: "all", label: t("runtime.deployLog.levelAll") },
          { value: "warn", label: t("runtime.deployLog.levelWarn") },
          { value: "error", label: t("runtime.deployLog.levelError") },
        ]}
      />
      <Checkbox label={t("runtime.deployLog.timesLabel")} checked={times} onCheckedChange={onTimes} />
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
