import { Section, Sections } from "../../components/page/Section";
import { useDocumentTitle } from "../../app/documentTitle";
import { useT } from "../../i18n";
import { ExecPanel } from "./ExecPanel";
import { TerminalPanel } from "./TerminalPanel";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * An application's terminal and its one-off commands, under the application's header: the
 * shell as the account the application runs as, and a command run once. Off the tab strip (it is
 * rare), linked from the header's "More actions".
 */
export function AppTerminalPage({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("terminal.document.app", { domain }));
  return (
    <Sections>
      <Section title={t("terminal.page.title")} icon={OBJECT_ICONS.terminal}>
        <TerminalPanel target={{ kind: "app", domain }} />
      </Section>
      <Section title={t("terminal.exec.title")} description={t("terminal.exec.description")}>
        <ExecPanel domain={domain} />
      </Section>
    </Sections>
  );
}
