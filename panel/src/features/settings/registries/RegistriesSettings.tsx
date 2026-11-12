import { Sections } from "../../../components/page/Section";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useT } from "../../../i18n";
import { FleetBlock } from "../fleet/FleetBlock";
import { FleetStrip } from "../fleet/FollowLine";
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
      <FleetStrip />
      <RegistriesSection />
      {/* What the central carries to every server: the accounts the others pull private images with. */}
      <FleetBlock resource="registry" />
    </Sections>
  );
}
