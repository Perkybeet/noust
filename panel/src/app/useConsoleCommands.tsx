import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useRouterState } from "@tanstack/react-router";
import { Archive, BookOpen, BookText, Box, Database, FileCode, FileUp, FolderKanban, Keyboard, Landmark, LogOut, Monitor, Moon, Network, Plus, Server, ShieldCheck, Sun, Unplug, Users } from "lucide-react";
import { useMemo } from "react";

import { appQuery, appTypesQuery, appsQuery } from "../api/queries/apps";
import { sessionQuery } from "../api/queries/auth";
import { appStatus } from "../components/page/status";
import { typeName } from "../features/apps/data";
import type { AppInfo } from "../features/apps/data";
import { useSignOut } from "../features/auth/useSignOut";
import { field, fleetViewQuery, originOf } from "../features/fleet/data";
import { useOrganization } from "../features/teams/useOrganization";
import { useT } from "../i18n";
import type { PlainKey } from "../i18n";
import { announce } from "./Announcer";
import { appSectionCommands } from "./appCommands";
import { helpFor, helpUrl } from "./help";
import { nodeOfConsolePath } from "./nodeRoute";
import { useHasFleet, useServerList } from "../nodes/servers";
import { useConsoleContext, useNode, useSwitchNode } from "../nodes/useNode";
import type { Command } from "./CommandPalette";
import { FLEET_ITEM, FLEET_TABS, NAV_GROUPS, SETTINGS_ITEM, SETTINGS_TABS } from "./nav";
import { resourceCommands } from "./resourceCommands";
import type { NavTarget } from "./resourceCommands";
import { useResourceIndex } from "./useResourceIndex";
import { useTheme } from "./theme";
import type { ThemeChoice } from "./theme";
import { appVerbCommands } from "./verbCommands";
import type { AppVerb } from "./verbCommands";

const THEME_ICONS = { system: Monitor, light: Sun, dark: Moon } as const;

const THEME_ACTION_LABEL: Record<ThemeChoice, PlainKey> = {
  system: "shell.commands.followSystemTheme",
  light: "shell.commands.switchToLightTheme",
  dark: "shell.commands.switchToDarkTheme",
};

/**
 * What the palette can do: every page and every settings section; every application, database,
 * site, certificate, service and recent deployment of the server on screen (read when the
 * palette opens, through the console's own queries: `useResourceIndex`); the other servers and
 * contexts; the verbs on each application the principal may act on (restart, stop, roll back,
 * update: `verbCommands`, run by `VerbRunner`); and the actions, the ones that create something first. On the fleet's pages the
 * applications are every server's, each named with its server and opening there.
 */
export function useConsoleCommands(open: boolean, openShortcuts: () => void, onVerb: (verb: AppVerb, app: AppInfo) => void): Command[] {
  const t = useT();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [theme, setTheme] = useTheme();
  const { signOut } = useSignOut();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const onFleet = context.kind === "fleet";
  const { data: apps } = useQuery({ ...appsQuery(), enabled: open && !onFleet });
  const { data: types } = useQuery({ ...appTypesQuery(), enabled: open && !onFleet, retry: false });
  // Inside an application's pages the palette also finds that application's sections.
  const params = useParams({ strict: false });
  const appDomain = !onFleet && typeof params.domain === "string" ? params.domain : null;
  const { data: currentApp } = useQuery({ ...appQuery(appDomain ?? ""), enabled: open && appDomain !== null, retry: false });
  const { data: fleetApps } = useQuery({ ...fleetViewQuery("apps"), enabled: open && onFleet });
  const listed = useResourceIndex(open, !onFleet);
  const org = useOrganization();
  const index = useMemo(
    () => ({ ...listed, ...(org.available ? { teams: org.teams, projects: org.projects } : {}) }),
    [listed, org.available, org.teams, org.projects],
  );
  const can = org.can;
  const mayOrganise = !onFleet && org.available && can("teams.manage");
  const mayCreateTeams = mayOrganise && org.seesAll;
  const { node } = useNode();
  const switchNode = useSwitchNode();
  const { nodes, hostname } = useServerList();
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });

  return useMemo(() => {
    const go = (to: string, params?: Record<string, string>) => () => {
      void navigate({ to, ...(params ? { params: params as never } : {}) });
    };
    const openPage = (target: NavTarget): void => {
      void navigate({
        to: target.to,
        ...(target.params ? { params: target.params as never } : {}),
        ...(target.search ? { search: target.search as never } : {}),
      });
    };

    const pages: Command[] = [...NAV_GROUPS.flat(), FLEET_ITEM, SETTINGS_ITEM].map((item) => {
      const Icon = item.icon;
      return {
        id: `page:${item.to}`,
        group: "Pages",
        label: t(item.label),
        icon: <Icon />,
        kind: "navigate",
        run: go(item.to),
        ...(item.keywords !== undefined ? { keywords: t(item.keywords) } : {}),
        ...(item.shortcut !== undefined ? { shortcut: item.shortcut.keys } : {}),
      };
    });
    const settingsIcon = <SETTINGS_ITEM.icon />;
    for (const tab of SETTINGS_TABS.slice(1)) {
      if (tab.fleetOnly === true && !hasFleet) continue;
      // A section the operator may not open is not offered (the settings' navigation hides it too).
      if (tab.permission !== undefined && !(permissions?.includes(tab.permission) ?? false)) continue;
      pages.push({
        id: `page:${tab.to}`,
        group: "Settings",
        label: t(tab.command),
        icon: settingsIcon,
        kind: "navigate",
        run: go(tab.to),
        // Search words, not a sentence: joining them is safe in any language.
        keywords: [t(SETTINGS_ITEM.label), tab.keywords !== undefined ? t(tab.keywords) : "", tab.scope === "central" ? t("fleet.selector.central") : ""].join(" "),
      });
    }
    if (hasFleet) {
      const fleetIcon = <Network />;
      for (const tab of FLEET_TABS.slice(1)) {
        pages.push({
          id: `page:${tab.to}`,
          group: "Pages",
          label: t(tab.command),
          icon: fleetIcon,
          kind: "navigate",
          run: go(tab.to),
          keywords: [t(FLEET_ITEM.label), tab.keywords !== undefined ? t(tab.keywords) : ""].join(" "),
        });
      }
    }

    if (appDomain !== null) pages.push(...appSectionCommands(appDomain, currentApp, t, go));

    const applications: Command[] = onFleet
      ? (fleetApps?.items ?? []).flatMap((row): Command[] => {
          const domain = field(row, "domain");
          if (domain === null) return [];
          const origin = originOf(row);
          const { node: target, pathname } = nodeOfConsolePath(origin.href);
          return [
            {
              id: `app:${origin.node}:${domain}`,
              group: "Applications",
              label: t("fleet.commands.appOn", { domain, server: origin.node }),
              icon: <Box />,
              keywords: [field(row, "name") ?? "", field(row, "app_type") ?? "", origin.node].join(" "),
              status: appStatus(field(row, "status") ?? "unknown").state,
              kind: "navigate",
              run: () => {
                void navigate({ to: pathname, search: { node: target ?? undefined } });
              },
            },
          ];
        })
      : (apps?.apps ?? []).map((app) => {
          // "Next.js" for `nextjs`: found by either, shown as the one a person reads.
          const typeLabel = app.app_type ? typeName(types?.types ?? [], app.app_type) : null;
          return {
            id: `app:${app.domain}`,
            group: "Applications",
            label: app.domain,
            ...(typeLabel !== null ? { detail: typeLabel } : {}),
            icon: <Box />,
            keywords: [app.name, app.app_type ?? ""].join(" "),
            status: appStatus(app.status).state,
            kind: "navigate",
            run: go("/apps/$domain", { domain: app.domain }),
          };
        });

    const resources = resourceCommands(index, { t, navigate: openPage });
    // The fleet's applications belong to other servers: their verbs are run on the server's own pages.
    const verbs = onFleet ? [] : appVerbCommands(apps?.apps ?? [], { t, can, current: appDomain, run: onVerb });

    const startWizard = (start: "recipe" | "import" | "adopt") => () => {
      void navigate({ to: "/apps/new", search: { start } as never });
    };
    const actions: Command[] = [
      {
        id: "action:new-app",
        group: "Actions",
        label: t("shell.commands.newApplication"),
        icon: <Plus />,
        keywords: t("shell.commands.newApplicationKeywords"),
        kind: "navigate",
        run: go("/apps/new"),
      },
      // The wizard's other ways in: the same page, started on the step that is the way.
      {
        id: "action:new-from-recipe",
        group: "Actions",
        label: t("shell.commands.newFromRecipe"),
        icon: <BookOpen />,
        keywords: t("shell.commands.newFromRecipeKeywords"),
        kind: "navigate",
        run: startWizard("recipe"),
      },
      {
        id: "action:import-app",
        group: "Actions",
        label: t("shell.commands.importApplication"),
        icon: <FileUp />,
        keywords: t("shell.commands.importApplicationKeywords"),
        kind: "navigate",
        run: startWizard("import"),
      },
      {
        id: "action:adopt-stack",
        group: "Actions",
        label: t("shell.commands.adoptStack"),
        icon: <Unplug />,
        keywords: t("shell.commands.adoptStackKeywords"),
        kind: "navigate",
        run: startWizard("adopt"),
      },
      {
        id: "action:new-database",
        group: "Actions",
        label: t("shell.commands.newDatabase"),
        icon: <Database />,
        keywords: t("shell.commands.newDatabaseKeywords"),
        kind: "navigate",
        run: () => {
          void navigate({ to: "/databases", search: { new: true } as never });
        },
      },
      {
        id: "action:new-backup",
        group: "Actions",
        label: t("shell.commands.newBackup"),
        icon: <Archive />,
        keywords: t("shell.commands.newBackupKeywords"),
        kind: "navigate",
        run: () => {
          void navigate({ to: "/backups", search: { new: true } as never });
        },
      },
      {
        id: "action:new-site",
        group: "Actions",
        label: t("shell.commands.newSite"),
        icon: <FileCode />,
        keywords: t("shell.commands.newSiteKeywords"),
        kind: "navigate",
        run: () => {
          void navigate({ to: "/domains/sites", search: { new: true } as never });
        },
      },
      {
        id: "action:issue-certificate",
        group: "Actions",
        label: t("shell.commands.issueCertificate"),
        icon: <ShieldCheck />,
        keywords: t("shell.commands.issueCertificateKeywords"),
        kind: "navigate",
        run: () => {
          void navigate({ to: "/domains", search: { new: true } as never });
        },
      },
      ...(mayOrganise
        ? [
            {
              id: "action:new-project",
              group: "Actions" as const,
              label: t("shell.commands.newProject"),
              icon: <FolderKanban />,
              keywords: t("shell.commands.newProjectKeywords"),
              kind: "navigate" as const,
              run: () => {
                void navigate({ to: "/projects", search: { new: true } as never });
              },
            },
          ]
        : []),
      ...(mayCreateTeams
        ? [
            {
              id: "action:new-team",
              group: "Actions" as const,
              label: t("shell.commands.newTeam"),
              icon: <Users />,
              keywords: t("shell.commands.newTeamKeywords"),
              kind: "navigate" as const,
              run: () => {
                void navigate({ to: "/teams", search: { new: true } as never });
              },
            },
          ]
        : []),
      {
        id: "action:add-server",
        group: "Actions",
        label: t("fleet.commands.addServer"),
        icon: <Plus />,
        keywords: t("fleet.commands.addServerKeywords"),
        kind: "navigate",
        run: () => {
          void navigate({ to: "/settings/servers", search: { add: true } });
        },
      },
      ...(["dark", "light", "system"] as const)
        .filter((choice) => choice !== theme)
        .map((choice): Command => {
          const Icon = THEME_ICONS[choice];
          return {
            id: `action:theme-${choice}`,
            group: "Actions",
            label: t(THEME_ACTION_LABEL[choice]),
            icon: <Icon />,
            keywords: t("shell.commands.themeKeywords"),
            kind: "action",
            run: () => {
              setTheme(choice);
            },
          };
        }),
      {
        id: "action:documentation",
        group: "Actions",
        label: t("shell.commands.documentation"),
        icon: <BookText />,
        keywords: t("shell.commands.documentationKeywords"),
        kind: "action",
        run: () => {
          window.open(helpUrl(helpFor(pathname)), "_blank", "noopener,noreferrer");
        },
      },
      {
        id: "action:shortcuts",
        group: "Actions",
        label: t("shell.session.keyboardShortcuts"),
        icon: <Keyboard />,
        shortcut: ["?"],
        keywords: t("shell.commands.shortcutsKeywords"),
        kind: "action",
        run: openShortcuts,
      },
      {
        id: "action:sign-out",
        group: "Actions",
        label: t("shell.session.signOut"),
        icon: <LogOut />,
        keywords: t("shell.commands.signOutKeywords"),
        kind: "navigate",
        run: () => void signOut(),
      },
    ];

    // The other contexts and servers, when this console holds a fleet: the selector's list.
    const thisServerName = hostname ?? t("fleet.selector.thisServer");
    const switchTo = (target: string | null, name: string) => () => {
      void switchNode(target).then(() => {
        announce(t("fleet.selector.switched", { name }));
      });
    };
    const onThisServer = context.kind === "server" && node === null;
    const servers: Command[] =
      !hasFleet && node === null
        ? []
        : [
            ...(hasFleet && !onFleet
              ? [
                  {
                    id: "server:all",
                    group: "Servers" as const,
                    label: t("fleet.selector.viewAll"),
                    icon: <Network />,
                    keywords: t("fleet.selector.keywords"),
                    kind: "navigate" as const,
                    run: () => {
                      void navigate({ to: "/fleet", search: { node: undefined } }).then(() => {
                        announce(t("fleet.selector.switchedAll"));
                      });
                    },
                  },
                ]
              : []),
            ...(hasFleet && context.kind !== "central"
              ? [
                  {
                    id: "server:central",
                    group: "Servers" as const,
                    label: t("fleet.selector.viewCentral", { name: thisServerName }),
                    icon: <Landmark />,
                    keywords: t("fleet.selector.keywords"),
                    kind: "navigate" as const,
                    run: () => {
                      void navigate({ to: "/settings/servers", search: { node: undefined } }).then(() => {
                        announce(t("fleet.selector.switchedCentral"));
                      });
                    },
                  },
                ]
              : []),
            ...(!onThisServer
              ? [
                  {
                    id: "server:this",
                    group: "Servers" as const,
                    label: t("fleet.selector.switchToThisServer", { name: thisServerName }),
                    icon: <Server />,
                    keywords: t("fleet.selector.keywords"),
                    kind: "navigate" as const,
                    run: switchTo(null, thisServerName),
                  },
                ]
              : []),
            ...nodes
              .filter((candidate) => context.kind !== "server" || candidate.name !== node)
              .map((candidate) => ({
                id: `server:${candidate.name}`,
                group: "Servers" as const,
                label: t("fleet.selector.switchTo", { name: candidate.name }),
                icon: <Server />,
                keywords: t("fleet.selector.keywords"),
                kind: "navigate" as const,
                run: switchTo(candidate.name, candidate.name),
              })),
          ];

    return [...pages, ...applications, ...resources, ...servers, ...verbs, ...actions];
  }, [apps, types, index, fleetApps, onFleet, navigate, theme, setTheme, openShortcuts, signOut, t, pathname, appDomain, currentApp, node, nodes, hostname, switchNode, hasFleet, context.kind, permissions, mayOrganise, mayCreateTeams, can, onVerb]);
}
