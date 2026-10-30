import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
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
      <CommandHint command="noust probe status-page show" label={t("common.words.fromTerminal")} />
    </Sections>
  );
}
