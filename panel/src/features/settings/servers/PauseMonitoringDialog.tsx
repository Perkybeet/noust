import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { fleetKeys } from "../../fleet/data";
import { nodeKeys, setNodeMonitoring } from "../../fleet/nodes";

export interface PauseMonitoringDialogProps {
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** The longest reason the central keeps (the API's limit). */
const REASON_MAX = 200;

/**
 * Pausing a server's monitoring for maintenance: while it is paused the central does not ask
 * about it, does not count it as down and never announces it, so rebooting it does not page
 * anyone. Nothing is hidden: the fleet still shows what it last answered. Resuming is one click.
 */
export function PauseMonitoringDialog({ name, open, onOpenChange }: PauseMonitoringDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  return (
    <ConfirmDialog
      friction="simple"
      open={open}
      onOpenChange={onOpenChange}
      title={t("servers.settings.monitoring.pauseTitle", { name })}
      description={t("servers.settings.monitoring.pauseDescription", { name })}
      actionLabel={t("servers.settings.monitoring.pauseAction")}
      onConfirm={async () => {
        await setNodeMonitoring(name, true, reason.trim() === "" ? null : reason.trim());
        void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
        void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
        toast.success(t("servers.settings.monitoring.pausedToast", { name }));
      }}
    >
      <Field label={t("servers.settings.monitoring.reason")} optional description={t("servers.settings.monitoring.reasonHelp")}>
        <Input maxLength={REASON_MAX} value={reason} onValueChange={(value: string) => setReason(value)} autoComplete="off" />
      </Field>
    </ConfirmDialog>
  );
}
