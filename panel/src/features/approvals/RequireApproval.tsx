import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { patchConfig } from "../../api/queries/config";
import { useCan } from "../../components/page/permissions";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { SectionSwitch } from "../../components/ui/SectionSwitch";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { SettingsMasterSwitch } from "../settings/SettingsPrimaryAction";
import { approvalKeys } from "./api";
import { useDeciders } from "./deciders";
import { NobodyDecides } from "./NobodyDecides";

/**
 * The switch that makes approvals apply: calls that change the server as root wait for a second person. It was a sentence naming the
 * configuration key (`approval.enabled`) for the operator to go and set; it is the switch the sentence stood for, with its name, and the
 * one question that goes with it: turning it off removes a safeguard, so it asks first. Turning it on asks too when nobody else could
 * decide: with the role that decides held by nobody, what waits waits for a person who does not exist, and giving the role to someone
 * is itself a change that waits, so the way out (give the role first) is said before the switch, with its link.
 *
 * It is the master switch of the page it governs, so it is where a master switch is: in the page's header, on the line of the title
 * (docs/DESIGN.md HD-1), and not a row of its own above the queue, which made the page a third longer than the subject it holds
 * (e2e/settings-length.spec.ts). What it does and what it risks are said by the page's own description (`ApprovalsPage`).
 */
export function RequireApproval({ enabled }: { enabled: boolean }) {
  const t = useT();
  const queryClient = useQueryClient();
  const canChange = useCan("settings.manage");
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [alone, setAlone] = useState(false);
  const deciders = useDeciders();

  const apply = async (value: boolean): Promise<void> => {
    setBusy(true);
    try {
      await patchConfig("approval.enabled", value);
      await queryClient.invalidateQueries({ queryKey: approvalKeys.all });
    } finally {
      setBusy(false);
    }
  };

  // Turning it on is a press that reports its own failure; turning it off waits in the question, which shows a failure in its words.
  const change = (next: boolean): void => {
    if (!next) {
      setAsking(true);
      return;
    }
    // Only said when the server's own numbers say nobody else holds the role: not knowing (no permission to read the accounts) is not a no.
    if (deciders.others === 0) {
      setAlone(true);
      return;
    }
    void apply(true).catch((error: unknown) => {
      reportActionError(t("approvals.require.failed"), error);
    });
  };

  const label = t("approvals.require.label");
  return (
    <SettingsMasterSwitch>
      {canChange ? (
        <SectionSwitch label={label} checked={enabled} busy={busy} onCheckedChange={change} />
      ) : (
        <SectionSwitch label={label} checked={enabled} onCheckedChange={change} disabled reason={t("approvals.require.noPermission")} />
      )}
      <ConfirmDialog
        friction="simple"
        destructive
        open={asking}
        onOpenChange={setAsking}
        title={t("approvals.require.confirmTitle")}
        description={t("approvals.require.confirmBody")}
        actionLabel={t("approvals.require.confirmAction")}
        onConfirm={() => apply(false)}
      />
      <ConfirmDialog
        friction="simple"
        open={alone}
        onOpenChange={setAlone}
        title={t("approvals.nobody.turnOnTitle")}
        description={<NobodyDecides deciders={deciders} moment="turnOn" links />}
        actionLabel={t("approvals.nobody.turnOnAnyway")}
        cancelLabel={t("approvals.nobody.turnOnLater")}
        onConfirm={() => apply(true)}
      />
    </SettingsMasterSwitch>
  );
}
