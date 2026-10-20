import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { ComplianceSection } from "./ComplianceSection";
import { PolicySection } from "./PolicySection";

/**
 * Settings > Controls: the compliance frameworks that can be switched on and the controls they are
 * made of, then the security profile the server enforces on every sign-in. They were the end of
 * Security, under the signed-in person's own password and sessions; they are the instance's
 * (NV-7), so they are a screen of their own, in Governance, next to the audit log and the ENS report.
 */
export function ControlsSettings() {
  const t = useT();
  useDocumentTitle(t("nav.screens.controls.command"), 1);
  return (
    <Sections>
      <ComplianceSection />
      <PolicySection />
    </Sections>
  );
}
