import { Sections } from "../../components/page/Section";
import { LogRetentionSection } from "./LogRetentionSection";
import { LogShippingSettings } from "./LogShippingSettings";

/**
 * Settings > Logs: where the logs of applications go besides the journal, and how long the
 * journal keeps them.
 */
export function LogsSettings() {
  return (
    <Sections>
      <LogShippingSettings />
      <LogRetentionSection />
    </Sections>
  );
}
