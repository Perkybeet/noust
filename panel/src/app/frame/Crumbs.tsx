import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useRouterState } from "@tanstack/react-router";
import { ChevronRight, ChevronsUpDown } from "lucide-react";
import { useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { sessionQuery } from "../../api/queries/auth";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { columnIconOf } from "../../features/nav/icons";
import { hasMessage, useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { useHasFleet } from "../../nodes/servers";
import { crumbsFor } from "../crumbs";
import type { CrumbName, CrumbStep } from "../crumbs";
import { DESTS } from "../nav";
import type { ConsolePath } from "../nav";
import { shown } from "../navCore";
import { useNavScope } from "../useNavScope";

/** How many other applications the switcher lists: it is a way to jump, not the list (the area is). */
const SIBLING_APPS = 12;

type Params = Record<string, string>;

function useName(params: Params) {
  const t = useT();
  return (name: CrumbName): string => ("key" in name ? t(name.key) : `${name.prefix ?? ""}${params[name.param] ?? ""}`);
}

/** The other applications, to jump to one from the page of any: the same section of it when that makes sense. */
function AppSwitcher({ route, params, label }: { route: string; params: Params; label: string }) {
  const t = useT();
  const navigate = useNavigate();
  const apps = useQuery({ ...appsQuery(), select: (data) => data.apps.map((app) => app.domain) });
  const others = (apps.data ?? []).filter((domain) => domain !== params["domain"]).slice(0, SIBLING_APPS);
  // A section with parameters of its own (one deployment) means nothing on another application: its overview.
  const only = [...route.matchAll(/\$(\w+)/g)].every((match) => match[1] === "domain");
  const to = (only ? route.replace(/\/+$/, "") : "/apps/$domain") as ConsolePath;
  if (others.length === 0) return null;
  return (
    <Menu trigger={<IconButton size="sm" label={t("nav.crumbs.switch", { name: label })} icon={<ChevronsUpDown />} tooltip={false} className="-ml-0.5" />}>
      {others.map((domain) => (
        <MenuItem
          key={domain}
          onClick={() => {
            void navigate({ to, params: { ...params, domain } as never });
          }}
        >
          <span translate="no">{domain}</span>
        </MenuItem>
      ))}
    </Menu>
  );
}

function tryIcon(id: string) {
  try {
    return columnIconOf({ id });
  } catch {
    // A section of a list that has no icon in the column (a team's tabs): the menu names it alone.
    return undefined;
  }
}

/** The sections beside the page's, to jump to one of them: the tabs of its resource or the sections of its list. */
function SectionSwitcher({ step, params, label }: { step: CrumbStep; params: Params; label: string }) {
  const t = useT();
  const navigate = useNavigate();
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  // The names are asked for when the menu is opened: the sections of a list are written in the catalogs of
  // many pages, and a page never paints a text whose catalog has not arrived (hasMessage asks for it).
  const [open, setOpen] = useState(false);
  const parent = step.entry.parent;
  if (parent === undefined || (step.entry.kind !== "tab" && step.entry.kind !== "section")) return null;
  const siblings = DESTS.filter(
    (entry) =>
      entry.parent === parent &&
      entry.kind === step.entry.kind &&
      entry.group === step.entry.group &&
      shown(entry, { can: (permission) => permissions?.includes(permission) ?? false }),
  );
  if (siblings.length < 2) return null;
  return (
    <Menu
      open={open}
      onOpenChange={setOpen}
      trigger={<IconButton size="sm" label={t("nav.crumbs.switch", { name: label })} icon={<ChevronsUpDown />} tooltip={false} className="-ml-0.5" />}
    >
      {(open ? siblings.filter((entry) => hasMessage(entry.label)) : []).map((entry) => {
        const Icon = tryIcon(entry.id);
        return (
          <MenuItem
            key={entry.id}
            {...(Icon !== undefined ? { icon: <Icon /> } : {})}
            onClick={() => {
              void navigate({ to: entry.to, params: params as never });
            }}
          >
            {t(entry.label)}
          </MenuItem>
        );
      })}
    </Menu>
  );
}

/**
 * Where the page is in the tree, in the top bar: the areas and resources above it as links, and the page
 * last. Generated from the register (`crumbsFor`), so no page writes its own and the trail is the same on
 * every one. The resource step (an application) and the page's own have a switch beside them that lists
 * what is next to it, so a person jumps to another application or another section without going back up.
 */
export function Crumbs({ className }: { className?: string }) {
  const t = useT();
  const scope = useNavScope();
  const hasFleet = useHasFleet();
  const params: Params = useParams({ strict: false });
  const route = useRouterState({ select: (state) => state.matches.at(-1)?.fullPath ?? "" });
  const nameOf = useName(params);
  const steps = crumbsFor(route, scope, hasFleet);
  // The names of the steps are in many pages' catalogs: until every one has arrived the trail is not drawn, and the
  // top bar has the same height without it, so nothing moves when it appears.
  const named = steps.every((step) => !("key" in step.name) || hasMessage(step.name.key));
  if (steps.length === 0 || !named) return null;
  return (
    <nav aria-label={t("shell.pageHeader.breadcrumb")} className={cx("min-w-0", className)}>
      <ol className="flex min-w-0 items-center gap-1 text-14 text-fg-muted">
        {steps.map((step, index) => {
          const last = index === steps.length - 1;
          const label = nameOf(step.name);
          const translate = "param" in step.name ? ({ translate: "no" } as const) : {};
          return (
            <li key={step.id} className={cx("flex items-center gap-1", last || step.resource ? "min-w-0" : "shrink-0")}>
              {index > 0 ? <ChevronRight aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" /> : null}
              {last ? (
                <span aria-current="page" {...translate} className="min-w-0 truncate font-semibold text-fg">
                  {label}
                </span>
              ) : (
                <Link
                  to={step.to}
                  params={params as never}
                  // A step above the page is where the page is under, never the page: nothing marks it current.
                  activeOptions={{ exact: true, includeSearch: false }}
                  {...translate}
                  className="min-w-0 truncate rounded-chip hover:text-accent-fg hover:underline hover:underline-offset-2"
                >
                  {label}
                </Link>
              )}
              {step.resource && step.entry.id === "app" ? <AppSwitcher route={route} params={params} label={label} /> : null}
              {last && !step.resource && step.entry.id !== "app" ? <SectionSwitcher step={step} params={params} label={label} /> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
