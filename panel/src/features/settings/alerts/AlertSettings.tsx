import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { AlertsReach } from "../../alerts/AlertsReach";
import { RuleList } from "./RuleList";

/**
 * Alerts: the rules on the metrics Noust records and which of them are firing. The limits of the
 * checks the monitor makes without a rule are the Checks tab's.
 */
export function AlertSettings() {
  const t = useT();
  useDocumentTitle(t("alerts.documentTitle"), 1);
  return (
    <Sections>
      <AlertsReach />
      <RuleList />
      <CommandHint command="noust alert list" label={t("common.words.sameFromTerminal")} />
    </Sections>
  );
}
