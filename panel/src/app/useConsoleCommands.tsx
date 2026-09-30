import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Box, Keyboard, Landmark, LogOut, Monitor, Moon, Network, Plus, Server, Sun } from "lucide-react";
import { useMemo } from "react";

import { appsQuery } from "../api/queries/apps";
import { sessionQuery } from "../api/queries/auth";
import { appStatus } from "../components/page/status";
import { useSignOut } from "../features/auth/useSignOut";
import { field, fleetViewQuery, originOf } from "../features/fleet/data";
import { useT } from "../i18n";
import type { PlainKey } from "../i18n";
import { announce } from "./Announcer";
import { nodeOfConsolePath } from "./nodeRoute";
import { useHasFleet, useServerList } from "../nodes/servers";
import { useConsoleContext, useNode, useSwitchNode } from "../nodes/useNode";
import type { Command } from "./CommandPalette";
import { FLEET_ITEM, FLEET_TABS, NAV_GROUPS, SETTINGS_ITEM, SETTINGS_TABS } from "./nav";
import { useTheme } from "./theme";
import type { ThemeChoice } from "./theme";

const THEME_ICONS = { system: Monitor, light: Sun, dark: Moon } as const;

const THEME_ACTION_LABEL: Record<ThemeChoice, PlainKey> = {
  system: "shell.commands.followSystemTheme",
  light: "shell.commands.switchToLightTheme",
  dark: "shell.commands.switchToDarkTheme",
};

/**
 * What the palette can do: every page, every application (loaded when the palette opens), the
 * other servers and contexts, and the actions that are not a page. On the fleet's pages the
 * applications are every server's, each named with its server and opening there.
 */
export function useConsoleCommands(open: boolean, openShortcuts: () => void): Command[] {
  const t = useT();
  const navigate = useNavigate();
  const [theme, setTheme] = useTheme();
  const { signOut } = useSignOut();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const onFleet = context.kind === "fleet";
  const { data: apps } = useQuery({ ...appsQuery(), enabled: open && !onFleet });
  const { data: fleetApps } = useQuery({ ...fleetViewQuery("apps"), enabled: open && onFleet });
  const { node } = useNode();
  const switchNode = useSwitchNode();
  const { nodes, hostname } = useServerList();
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });

  return useMemo(() => {
    const go = (to: string, params?: Record<string, string>) => () => {
      void navigate({ to, ...(params ? { params: params as never } : {}) });
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
        group: "Pages",
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
      : (apps?.apps ?? []).map((app) => ({
          id: `app:${app.domain}`,
          group: "Applications",
          label: app.domain,
          icon: <Box />,
          keywords: [app.name, app.app_type ?? ""].join(" "),
          status: appStatus(app.status).state,
          kind: "navigate",
          run: go("/apps/$domain", { domain: app.domain }),
        }));

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
                    group: "Actions" as const,
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
                    group: "Actions" as const,
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
                    group: "Actions" as const,
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
                group: "Actions" as const,
                label: t("fleet.selector.switchTo", { name: candidate.name }),
                icon: <Server />,
                keywords: t("fleet.selector.keywords"),
                kind: "navigate" as const,
                run: switchTo(candidate.name, candidate.name),
              })),
          ];

    return [...pages, ...applications, ...servers, ...actions];
  }, [apps, fleetApps, onFleet, navigate, theme, setTheme, openShortcuts, signOut, t, node, nodes, hostname, switchNode, hasFleet, context.kind, permissions]);
}
