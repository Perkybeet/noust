import { useDocumentTitle } from "../../app/documentTitle";
import { Sections } from "../../components/page/Section";
import { GitHubIntegration } from "./github/GitHubIntegration";

/** Settings > Integrations: the code hosts this server deploys from. GitHub, for now. */
export function IntegrationsSettings() {
  useDocumentTitle("Integrations", 1);
  return (
    <Sections>
      <GitHubIntegration />
    </Sections>
  );
}
