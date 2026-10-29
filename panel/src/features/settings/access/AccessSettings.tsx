import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { RolesSection } from "../roles/RolesSection";
import { GrantsSection } from "./GrantsSection";

/**
 * Settings > Roles: the one list of roles (item 96), which are edited like any other, and then where a role
 * applies beyond the whole server, in a team, a project or an environment. A person holds one role or
 * several; what they can do is what all of them hold together, seen on the People page.
 */
export function AccessSettings() {
  const t = useT();
  useDocumentTitle(t("roles.documentTitle"), 1);
  return (
    <Sections>
      <p className="max-w-measure text-14 text-pretty text-fg-muted">{t("roles.lead")}</p>
      <RolesSection />
      <GrantsSection />
    </Sections>
  );
}
