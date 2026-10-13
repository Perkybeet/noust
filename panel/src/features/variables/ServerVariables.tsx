import { useDocumentTitle } from "../../app/documentTitle";
import { CommandHint } from "../../components/page/CommandHint";
import { useT } from "../../i18n";
import { SharedVariables } from "./SharedVariables";
import { useCan } from "./useCan";

/**
 * The server's own shared variables, a section of Settings: defined once here, every
 * application on the server refers to one with `{{server.NAME}}` (a region, a mail host, an
 * observability endpoint). The add action is the page's one primary, in its header.
 */
export function ServerVariables() {
  const t = useT();
  useDocumentTitle(t("variables.nav.settings.documentTitle"), 1);
  const canManage = useCan("settings.manage");
  return (
    <div className="flex flex-col gap-6">
      <SharedVariables scope={{ kind: "server" }} canManage={canManage} primary command={false} />
      <CommandHint command="noust variables list --scope server" label={t("variables.shared.fromTerminal")} />
    </div>
  );
}
