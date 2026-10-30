import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";

import { sessionQuery } from "../../api/queries/auth";
import { Section } from "../../components/page/Section";
import { buttonClassName } from "../../components/ui/Button";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";

interface QuickAction {
  key: string;
  label: PlainKey;
  icon: LucideIcon;
  permission: string;
  /** Where it is done: each opens the place that does it, with that place's own confirmation, never a second way of doing it from here. */
  to: "/apps/new" | "/fleet" | "/settings/accounts" | "/backups";
  search?: Record<string, string | boolean>;
}

const ACTIONS: readonly QuickAction[] = [
  { key: "deploy", label: "overview.quickActions.deploy", icon: OBJECT_ICONS.deployment, permission: "apps.deploy", to: "/apps/new" },
  { key: "server", label: "overview.quickActions.addServer", icon: OBJECT_ICONS.server, permission: "fleet.manage", to: "/fleet", search: { add: true } },
  { key: "invite", label: "overview.quickActions.invite", icon: OBJECT_ICONS.account, permission: "accounts.manage", to: "/settings/accounts" },
  { key: "backup", label: "overview.quickActions.backUp", icon: OBJECT_ICONS.backup, permission: "backups.read", to: "/backups" },
];

/**
 * The four things a person comes to do and has not yet found where: deploy, add a server, invite
 * someone, make a backup. Each leads to the place that does it. One the account may not do is not
 * drawn (what a role may not see is absent), and the section is not drawn when none is.
 */
export function QuickActions() {
  const t = useT();
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  const offered = ACTIONS.filter((action) => permissions?.includes(action.permission) ?? false);
  if (offered.length === 0) return null;
  return (
    <Section title={t("overview.quickActions.title")}>
      <ul className="flex min-w-0 flex-col gap-2">
        {offered.map(({ key, label, icon: Icon, to, search }) => (
          <li key={key}>
            <Link
              to={to}
              {...(search !== undefined ? { search: search as never } : {})}
              className={buttonClassName("secondary", "md", "w-full justify-start")}
            >
              <Icon aria-hidden="true" className="text-accent-fg" />
              {t(label)}
            </Link>
          </li>
        ))}
      </ul>
    </Section>
  );
}
