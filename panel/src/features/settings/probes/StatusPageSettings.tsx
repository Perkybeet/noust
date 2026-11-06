import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { StatusPageSection } from "./StatusPageSection";

/** Alerts > Status page: the probes marked public as one static page, kept up to date and published on a name. */
export function StatusPageSettings() {
  const t = useT();
  useDocumentTitle(t("probes.page.documentTitle"), 1);
  return (
    <Sections>
      <StatusPageSection />
    </Sections>
  );
}
