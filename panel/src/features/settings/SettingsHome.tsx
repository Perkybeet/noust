import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { rolesQuery } from "../../api/queries/roles";
import { useDocumentTitle } from "../../app/documentTitle";
import { Card } from "../../components/ui/Card";
import { Badge } from "../../components/ui/Badge";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { accountsQuery } from "./accounts/api";
import { useSettingsItems } from "./useSettingsItems";
import type { SettingsSection } from "./useSettingsItems";

/** What each group of Settings is called in the hub's cards: the same words as the second column's headings. */
function groupsOf(items: readonly SettingsSection[]): { key: string; title: string; sections: SettingsSection[] }[] {
  const groups: { key: string; title: string; sections: SettingsSection[] }[] = [];
  for (const item of items) {
    const key = item.groupKey ?? "";
    const last = groups.find((group) => group.key === key);
    if (last !== undefined) last.sections.push(item);
    else groups.push({ key, title: typeof item.group === "string" ? item.group : key, sections: [item] });
  }
  return groups;
}

/** What is true now beside each door, and which doors are still being asked. */
interface Facts {
  facts: Readonly<Record<string, ReactNode>>;
  /** The ids of the doors whose count is on its way: their card says so, and the count shows a placeholder. */
  asking: ReadonlySet<string>;
}

/**
 * A line of what is true now beside a door: how many people have an account, how many roles there
 * are, which version runs. Only what an existing query already knows and the person may read; a door
 * with no fact is just a door.
 */
function useFacts(): Facts {
  const t = useT();
  const session = useQuery(sessionQuery());
  const can = (permission: string): boolean => session.data?.permissions?.includes(permission) ?? false;
  const accounts = useQuery({ ...accountsQuery(), enabled: can("accounts.read") });
  const roles = useQuery({ ...rolesQuery(), enabled: can("accounts.read") });
  const facts: Record<string, ReactNode> = {};
  if (accounts.data !== undefined) facts["settings.accounts"] = t("settingsHome.facts.people", { count: accounts.data.accounts.length });
  if (roles.data !== undefined) facts["settings.access"] = t("settingsHome.facts.roles", { count: roles.data.roles.length });
  if (session.data?.version !== undefined) facts["settings.about"] = t("settingsHome.facts.version", { version: session.data.version });
  const asking = new Set<string>();
  if (accounts.isLoading) asking.add("settings.accounts");
  if (roles.isLoading) asking.add("settings.access");
  return { facts, asking };
}

function Door({ section, fact, asking }: { section: SettingsSection; fact: ReactNode | undefined; asking: boolean }) {
  const t = useT();
  const summary = `settingsHome.summary.${section.id.replace(/^settings\./, "")}` as PlainKey;
  return (
    <li>
      <Link
        to={section.to}
        {...(section.search !== undefined ? { search: section.search as never } : {})}
        className="group flex min-h-14 items-center gap-3 px-5 py-3 transition-colors duration-(--duration-press) ease-out hover:bg-surface-hover -outline-offset-2"
      >
        {section.icon !== undefined ? <section.icon aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint group-hover:text-fg-muted" /> : null}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-13 font-medium text-fg">{section.label}</span>{" "}
          <span className="text-12 text-pretty text-fg-muted">{t(summary)}</span>
        </span>
        {asking ? <Skeleton className="h-5 w-16 rounded-chip" /> : fact !== undefined ? <Badge>{fact}</Badge> : null}
        <ChevronRight aria-hidden="true" className="size-icon-md shrink-0 text-fg-faint" />
      </Link>
    </li>
  );
}

/**
 * Where Settings opens: every section as a door, under the group that says what it is for, with one
 * line on what is inside and, where it is known, what is true now. A newcomer finds a setting by its
 * purpose here without knowing what it is called (task 18); the second column and the palette find it
 * by name. Drawn from the register, so a section added there is a door here.
 */
export function SettingsHome() {
  const t = useT();
  useDocumentTitle(t("settingsHome.documentTitle"), 1);
  const { items } = useSettingsItems();
  const { facts, asking } = useFacts();
  const groups = groupsOf(items);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <p className="max-w-measure text-14 text-pretty text-fg-muted">{t("settingsHome.lead")}</p>
      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {groups.map((group) => (
          <Card key={group.key} title={group.title} level={2} padding="none" className="self-start" loading={group.sections.some((section) => asking.has(section.id))}>
            <ul aria-label={t("settingsHome.doors", { group: group.title })} className="m-0 flex list-none flex-col divide-y divide-border border-t border-border p-0">
              {group.sections.map((section) => (
                <Door key={section.id} section={section} fact={facts[section.id]} asking={asking.has(section.id)} />
              ))}
            </ul>
          </Card>
        ))}
      </div>
    </div>
  );
}
