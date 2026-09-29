import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Box, Keyboard, LogOut, Monitor, Moon, Plus, Server, Sun } from "lucide-react";
import { useMemo } from "react";

import { appsQuery } from "../api/queries/apps";
import { appStatus } from "../components/page/status";
import { useSignOut } from "../features/auth/useSignOut";
import { useT } from "../i18n";
import type { PlainKey } from "../i18n";
import { announce } from "./Announcer";
import { useServerList } from "../nodes/servers";
import { useNode, useSwitchNode } from "../nodes/useNode";
import type { Command } from "./CommandPalette";
import { NAV_GROUPS, SETTINGS_ITEM, SETTINGS_TABS } from "./nav";
import { useTheme } from "./theme";
import type { ThemeChoice } from "./theme";

const THEME_ICONS = { system: Monitor, light: Sun, dark: Moon } as const;

const THEME_ACTION_LABEL: Record<ThemeChoice, PlainKey> = {
  system: "shell.commands.followSystemTheme",
  light: "shell.commands.switchToLightTheme",
  dark: "shell.commands.switchToDarkTheme",
};

/**
 * What the palette can do: every page, every application (loaded when the palette opens),
 * and the actions that are not a page.
 */
export function useConsoleCommands(open: boolean, openShortcuts: () => void): Command[] {
  const t = useT();
  const navigate = useNavigate();
  const [theme, setTheme] = useTheme();
  const { signOut } = useSignOut();
  const { data: apps } = useQuery({ ...appsQuery(), enabled: open });
  const { node } = useNode();
  const switchNode = useSwitchNode();
  const { nodes, hostname } = useServerList();

  return useMemo(() => {
    const go = (to: string, params?: Record<string, string>) => () => {
      void navigate({ to, ...(params ? { params: params as never } : {}) });
    };

    const pages: Command[] = [...NAV_GROUPS.flat(), SETTINGS_ITEM].map((item) => {
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
      pages.push({
        id: `page:${tab.to}`,
        group: "Pages",
        label: t(tab.command),
        icon: settingsIcon,
        kind: "navigate",
        run: go(tab.to),
        // Search words, not a sentence: joining them is safe in any language.
        ...(tab.keywords !== undefined ? { keywords: `${t(SETTINGS_ITEM.label)} ${t(tab.keywords)}` } : {}),
      });
    }

    const applications: Command[] = (apps?.apps ?? []).map((app) => ({
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

    // Every other server, when this one has nodes: the selector's list, by name.
    const thisServerName = hostname ?? t("fleet.selector.thisServer");
    const switchTo = (target: string | null, name: string) => () => {
      void switchNode(target).then(() => {
        announce(t("fleet.selector.switched", { name }));
      });
    };
    const servers: Command[] =
      nodes.length === 0 && node === null
        ? []
        : [
            ...(node !== null
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
              .filter((candidate) => candidate.name !== node)
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
  }, [apps, navigate, theme, setTheme, openShortcuts, signOut, t, node, nodes, hostname, switchNode]);
}
