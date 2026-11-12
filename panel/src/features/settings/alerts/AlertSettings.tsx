import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { Watching } from "../../alerts/WatchingSection";
import { RuleList } from "./RuleList";

/**
 * Alerts: what Noust watches without being asked and by which channels it says so, then the rules on the
 * metrics Noust records and which of them are firing. The limits of the checks the monitor makes without
 * a rule are the Checks tab's.
 */
export function AlertSettings() {
  const t = useT();
  useDocumentTitle(t("alerts.documentTitle"), 1);
  return (
    <Sections>
      <Watching />
      <RuleList />
    </Sections>
  );
}
