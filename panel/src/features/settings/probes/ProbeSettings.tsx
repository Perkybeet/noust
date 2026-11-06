import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { ProbeList } from "./ProbeList";

/** Alerts > Probes: the addresses the monitor asks from outside the application and where each stands. */
export function ProbeSettings() {
  const t = useT();
  useDocumentTitle(t("probes.documentTitle"), 1);
  return (
    <Sections>
      <ProbeList />
    </Sections>
  );
}
