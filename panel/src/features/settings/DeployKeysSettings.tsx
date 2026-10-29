import { useDocumentTitle } from "../../app/documentTitle";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { DeployKeysSection } from "./DeployKeysSection";

/**
 * Settings > Deploy keys: the SSH key each repository is cloned with and the host keys this server pinned. They were
 * two cards at the end of Integrations, which held a webhook address, three code hosts, keys and more on one page of
 * 2700 pixels; a section is one subject (NV-9), so they have a page and a door of their own.
 */
export function DeployKeysSettings() {
  const t = useT();
  useDocumentTitle(t("connections.deployKeys.nav.label"), 1);
  return (
    <Sections>
      <DeployKeysSection />
    </Sections>
  );
}
