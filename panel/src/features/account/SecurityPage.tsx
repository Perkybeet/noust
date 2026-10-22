import { useQuery } from "@tanstack/react-query";

import { sessionQuery } from "../../api/queries/auth";
import { useDocumentTitle } from "../../app/documentTitle";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { PasskeysSection } from "../auth/PasskeysSection";
import { SessionsSection } from "../settings/SessionsSection";
import { TwoFactorSection } from "../settings/TwoFactorSection";

/**
 * Account > Sign-in security: the authenticator app, the passkeys and where the person is signed
 * in. They were in Settings > Security among twenty settings of the server and the central; they
 * are the person's own, two clicks from the account menu.
 */
export function SecurityPage() {
  const t = useT();
  useDocumentTitle(t("account.securityDocumentTitle"), 1);
  const { data: session } = useQuery(sessionQuery());
  return (
    <Sections>
      <TwoFactorSection session={session} />
      <PasskeysSection />
      <SessionsSection />
    </Sections>
  );
}
