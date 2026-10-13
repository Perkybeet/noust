import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { IdentitiesSection } from "./IdentitiesSection";
import { LocalSignInSection } from "./LocalSignInSection";
import { ProvidersSection } from "./ProvidersSection";

/**
 * Settings > Single sign-on: the identity providers people sign in with, the people linked to
 * them, and whether passwords and passkeys still sign accounts in beside them. The providers
 * come first because everything else depends on there being one; turning the password sign-in
 * off is last, with its way back spelled out.
 */
export function SsoSettings() {
  const t = useT();
  useDocumentTitle(t("ssoAdmin.documentTitle"), 1);
  return (
    <Sections>
      <ProvidersSection />
      <LocalSignInSection />
      <IdentitiesSection />
    </Sections>
  );
}
