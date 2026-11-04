import { Sections } from "../../../components/page/Section";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useT } from "../../../i18n";
import { RegistriesSection } from "./RegistriesSection";

/**
 * Settings > Image registries: the accounts this server pulls private images with. It was one of the
 * cards of Integrations, which held code hosts, keys, registries and an export that had nothing to do
 * with each other; a section is one subject (NV-9), so it has its own page and its own door.
 */
export function RegistriesSettings() {
  const t = useT();
  useDocumentTitle(t("registries.nav.label"), 1);
  return (
    <Sections>
      <RegistriesSection />
    </Sections>
  );
}
