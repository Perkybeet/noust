import { useQuery } from "@tanstack/react-query";

import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { incidentQuery } from "./api";

/** Whether the console is locked down for an incident: said first, on the page that is about being compliant, when it is. */
export function LockdownNotice() {
  const t = useT();
  const query = useQuery(incidentQuery());
  const lockdown = query.data;
  if (lockdown?.locked !== true) return null;
  return (
    <Notice tone="error" variant="banner" title={t("complianceSetup.lockdown.title")}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure">{t("complianceSetup.lockdown.description")}</p>
        <KeyValueList
          items={[
            ...(lockdown.since ? [{ label: t("complianceSetup.lockdown.since"), value: <RelativeTime value={lockdown.since} />, mono: false, copy: false as const }] : []),
            ...(lockdown.by ? [{ label: t("complianceSetup.lockdown.by"), value: lockdown.by }] : []),
            ...(lockdown.reason ? [{ label: t("complianceSetup.lockdown.reason"), value: lockdown.reason, mono: false }] : []),
            ...(lockdown.package ? [{ label: t("complianceSetup.lockdown.package"), value: lockdown.package }] : []),
          ]}
        />
        <CommandHint label={t("complianceSetup.lockdown.command")} command="noust incident unfreeze" reason="solo-terminal" />
      </div>
    </Notice>
  );
}
