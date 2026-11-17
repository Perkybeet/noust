import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { patchConfig } from "../../api/queries/config";
import { useCan } from "../../components/page/permissions";
import { Setting } from "../../components/ui/Setting";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { approvalKeys } from "./api";

/**
 * The switch that makes approvals apply: calls that change the server as root wait for a second person. It was a sentence naming the
 * configuration key (`approval.enabled`) for the operator to go and set; it is the switch the sentence stood for, with its name, what it
 * does, and the one way it can go wrong. Turning it off removes a safeguard, so it asks first.
 */
export function RequireApproval({ enabled }: { enabled: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const canChange = useCan("settings.manage");
  const [busy, setBusy] = useState(false);

  const apply = async (value: boolean): Promise<void> => {
    setBusy(true);
    try {
      await patchConfig("approval.enabled", value);
      await queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Setting
      label={t("approvals.require.label")}
      help={t("approvals.require.help")}
      checked={enabled}
      busy={busy}
      // Turning it on is a press that reports its own failure; turning it off waits in the question, which shows a failure in its words.
      onCheckedChange={(next) => {
        if (!next) return apply(false);
        void apply(true).catch((error: unknown) => {
          reportActionError(t("approvals.require.failed"), error);
        });
        return undefined;
      }}
      {...(enabled
        ? {
            confirmOff: {
              title: t("approvals.require.confirmTitle"),
              description: t("approvals.require.confirmBody"),
              actionLabel: t("approvals.require.confirmAction"),
            },
          }
        : { risk: t("approvals.require.risk") })}
      {...(!canChange ? { disabled: true as const, reason: t("approvals.require.noPermission") } : {})}
    />
  );
}
