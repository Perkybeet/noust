import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { ReadOnlyLine } from "../../../components/page/permissions";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
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
    <Sections surfaces>
      <ReadOnlyLine permission="accounts.manage" />
      <SurfaceSection title={t("roles.section.title")} icon={OBJECT_ICONS.role} description={t("roles.lead")} flush>
        <RolesSection />
      </SurfaceSection>
      <GrantsSection />
    </Sections>
  );
}
