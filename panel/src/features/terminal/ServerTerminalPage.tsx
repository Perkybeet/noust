import { useDocumentTitle } from "../../app/documentTitle";
import { Section } from "../../components/page/Section";
import { useT } from "../../i18n";
import { TerminalPanel } from "./TerminalPanel";

/**
 * A root shell on the server itself, under the Server header. Off until an administrator turns
 * it on, and said so when it is: see `TerminalPanel`.
 */
export function ServerTerminalPage() {
  const t = useT();
  useDocumentTitle(t("terminal.document.server"));
  return (
    <Section title={t("terminal.page.title")}>
      <TerminalPanel target={{ kind: "server" }} />
    </Section>
  );
}
