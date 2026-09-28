import { useDocumentTitle } from "../../app/documentTitle";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { GitHubIntegration } from "./github/GitHubIntegration";

/** Settings > Integrations: the code hosts this server deploys from. GitHub, for now. */
export function IntegrationsSettings() {
  const t = useT();
  useDocumentTitle(t("settings.integrations.documentTitle"), 1);
  return (
    <Sections>
      <GitHubIntegration />
    </Sections>
  );
}
