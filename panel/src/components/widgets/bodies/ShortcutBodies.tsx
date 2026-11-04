/**
 * The widget of shortcuts: what a person comes to do every day, as a tile of icons that each open the place that
 * does it (with that place's own confirmation, never a second way of doing it from here). What the account may not
 * do is not drawn.
 */
import { Link } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";

import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { ICONS } from "../../ui/icons";
import { OBJECT_ICONS } from "../../ui/objectIcons";
import { useCan } from "../useCan";

interface Shortcut {
  id: string;
  label: PlainKey;
  icon: LucideIcon;
  /** What the account must hold to be offered it. */
  needs: string;
  to: string;
  search?: Record<string, string | boolean>;
}

/** In the order a tile fills: a small one has four, a wide one eight. */
const SHORTCUTS: readonly Shortcut[] = [
  { id: "app", label: "widgets.actions.newApp", icon: ICONS.add, needs: "apps.deploy", to: "/apps/new" },
  { id: "database", label: "widgets.actions.newDatabase", icon: OBJECT_ICONS.database, needs: "databases.write", to: "/databases" },
  { id: "backup", label: "common.words.backUpNow", icon: OBJECT_ICONS.backup, needs: "backups.run", to: "/backups" },
  { id: "port", label: "widgets.actions.openPort", icon: OBJECT_ICONS.port, needs: "server.manage", to: "/domains/ports" },
  { id: "domain", label: "widgets.actions.addDomain", icon: OBJECT_ICONS.domain, needs: "apps.manage", to: "/domains" },
  { id: "terminal", label: "widgets.actions.terminal", icon: OBJECT_ICONS.terminal, needs: "server.terminal", to: "/server/terminal" },
  { id: "logs", label: "widgets.actions.logs", icon: OBJECT_ICONS.log, needs: "server.read", to: "/logs" },
  { id: "update", label: "widgets.actions.systemUpdates", icon: OBJECT_ICONS.update, needs: "server.manage", to: "/server/system" },
  { id: "server", label: "widgets.actions.addServer", icon: OBJECT_ICONS.server, needs: "fleet.manage", to: "/fleet", search: { add: true } },
  { id: "invite", label: "widgets.actions.invite", icon: OBJECT_ICONS.account, needs: "accounts.manage", to: "/settings/accounts" },
];

/** The shortcuts the account may use, as a tile of icons. */
export function ActionsBody() {
  const t = useT();
  const can = useCan();
  const offered = SHORTCUTS.filter((shortcut) => can(shortcut.needs)).slice(0, 8);
  if (offered.length === 0) return <p className="text-13 text-fg-muted">{t("widgets.actions.none")}</p>;
  return (
    <ul className="grid h-full grid-cols-2 content-center gap-1.5 [&>li:nth-child(n+5)]:hidden w-wide:grid-cols-4 w-wide:[&>li:nth-child(n+5)]:block w-xwide:grid-cols-8">
      {offered.map(({ id, label, icon: Icon, to, search }) => (
        <li key={id}>
          <Link
            to={to as never}
            {...(search !== undefined ? { search: search as never } : {})}
            className="elev-raised flex h-14 flex-col items-center justify-center gap-1 rounded-control bg-surface-raised px-1 text-12 font-medium text-fg"
          >
            <Icon aria-hidden="true" className="size-icon-md text-fg-muted" />
            <span className="max-w-full truncate">{t(label)}</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
