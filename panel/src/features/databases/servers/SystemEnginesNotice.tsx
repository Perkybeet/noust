import { Notice } from "../../../components/ui/Notice";
import { ServerText } from "../../../components/ui/ServerText";
import { useT } from "../../../i18n";
import type { SystemEngines } from "./useDatabaseServers";

/**
 * Why the list has no engine of the system's own, in the words the server says (the table's sentence for `databases`) and with its fix
 * under it. It is drawn only where the machine has none (a machine that only has Docker), and there is nothing to press: what it says is
 * what the page does not offer, so no install of a package is asked of a machine that cannot make one (item 188).
 */
export function SystemEnginesNotice({ system }: { system: SystemEngines }) {
  const t = useT();
  const reason = system.reason;
  if (system.available || reason === null) return null;
  return (
    <Notice tone="info" title={t("dbServers.noSystem.title")}>
      <span className="block">
        <ServerText coded={reason} text={reason.text} />
      </span>
      {reason.hint !== null && reason.hint !== undefined ? (
        <span className="mt-1 block text-fg-muted">
          <ServerText coded={reason} text={reason.hint} part="hint" />
        </span>
      ) : null}
    </Notice>
  );
}
