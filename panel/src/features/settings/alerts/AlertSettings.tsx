import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { Watching } from "../../alerts/WatchingSection";
import { FleetBlock } from "../fleet/FleetBlock";
import { FleetStrip } from "../fleet/FollowLine";
import { RuleList } from "./RuleList";

/**
 * Alerts: what Noust watches without being asked and by which channels it says so, then the rules on the
 * metrics Noust records and which of them are firing. The limits of the checks the monitor makes without
 * a rule are the Checks tab's. On a central the strip of the servers that do not get its changes comes first, and the
 * rules it carries to every server come after the central's own.
 */
export function AlertSettings() {
  const t = useT();
  useDocumentTitle(t("alerts.documentTitle"), 1);
  return (
    <Sections>
      <FleetStrip />
      <Watching />
      <RuleList />
      {/* The rules the central carries to every server, with the servers that follow each one. */}
      <FleetBlock resource="alert_rule" />
    </Sections>
  );
}
