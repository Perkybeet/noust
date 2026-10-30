import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { ViewAnswer } from "./views";

/** The select's value for "every server": an option needs one, and no server is named like this. */
const ALL = "@all";

/**
 * Which servers a view is about: "All servers" or one, this central's own among them. It narrows the
 * rows and never changes the page (the central's design, D4), and it is drawn only where there is
 * something to tell apart: a server with no servers of its own has no filter and no column.
 */
export function ServerScope({ view, value, onChange }: { view: ViewAnswer | undefined; value: string | undefined; onChange: (server: string | undefined) => void }) {
  const t = useT();
  if (view?.fleet !== true) return null;
  const servers = view.servers.map((server) => ({ value: server.selector, label: server.local ? t("operation.scope.central", { name: server.name }) : server.name }));
  // A server named in a shared link that is not in the fleet any more still shows as chosen.
  const known = servers.some((server) => server.value === value);
  const options = value !== undefined && !known ? [...servers, { value, label: value }] : servers;
  return (
    <Select
      aria-label={t("operation.scope.label")}
      value={value ?? ALL}
      onValueChange={(next) => {
        onChange(next === ALL ? undefined : next);
      }}
      options={[{ value: ALL, label: t("operation.scope.all") }, ...options]}
      className="min-w-44"
    />
  );
}
