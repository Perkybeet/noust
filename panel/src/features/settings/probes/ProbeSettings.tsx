import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { ProbeList } from "./ProbeList";
import { StatusPageSection } from "./StatusPageSection";

/**
 * Settings > Probes: the addresses the monitor asks from outside the application and where each
 * stands, then the public status page made of the ones marked public.
 */
export function ProbeSettings() {
  const t = useT();
  useDocumentTitle(t("probes.documentTitle"), 1);
  return (
    <Sections>
      <ProbeList />
      <StatusPageSection />
      <CommandHint command="noust probe list" label={t("common.words.fromTerminal")} />
    </Sections>
  );
}
