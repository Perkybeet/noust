import { Sections } from "../../components/page/Section";
import { LogRetentionSection } from "./LogRetentionSection";
import { LogShippingSettings } from "./LogShippingSettings";
import { MetricsExportSection } from "./MetricsExportSection";

/**
 * Settings > Logs and metrics export: where the logs of applications go besides the journal, how
 * long the journal keeps them, and the address a scraper asks for the server's metrics.
 */
export function LogsSettings() {
  return (
    <Sections>
      <LogShippingSettings />
      <LogRetentionSection />
      <MetricsExportSection />
    </Sections>
  );
}
