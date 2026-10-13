import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { GrantsSection } from "./GrantsSection";
import { RolesSection } from "./RolesSection";

/**
 * Settings > Roles and access: which role an account holds where, and the roles themselves. The
 * grants come first because they are what an operator does every week; the roles, which change
 * rarely, follow. A grant on a project or an environment is how the same person administers one
 * and only reads another.
 */
export function AccessSettings() {
  const t = useT();
  useDocumentTitle(t("access.documentTitle"), 1);
  return (
    <Sections>
      <GrantsSection />
      <RolesSection />
    </Sections>
  );
}
