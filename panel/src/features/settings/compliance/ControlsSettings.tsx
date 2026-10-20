import { useDocumentTitle } from "../../../app/documentTitle";
import { Sections } from "../../../components/page/Section";
import { useT } from "../../../i18n";
import { ComplianceSection } from "./ComplianceSection";
import { PolicySection } from "./PolicySection";

/**
 * Settings > Controls: the security profile the server enforces on every sign-in, then the compliance
 * frameworks that can be switched on and the controls they are made of. They were the end of
 * Security, under the signed-in person's own password and sessions; they are the instance's
 * (NV-7), so they are a screen of their own, in Governance, next to the audit log and the ENS report.
 * The profile is first because it is short and known by its first frame: the frameworks, nine cards
 * that arrive from the server, would push it out of the window as they load.
 */
export function ControlsSettings() {
  const t = useT();
  useDocumentTitle(t("nav.screens.controls.command"), 1);
  return (
    <Sections>
      <PolicySection />
      <ComplianceSection />
    </Sections>
  );
}
