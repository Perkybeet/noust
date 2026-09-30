import { useQuery } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { Landmark, Server } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { SETTINGS_TABS } from "../../app/nav";
import type { SettingsScope } from "../../app/nav";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { SettingsLayout } from "../../components/page/SettingsLayout";
import type { SettingsNavItem } from "../../components/page/SettingsLayout";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useHasFleet, useReturnServer, useServerList } from "../../nodes/servers";
import { useConsoleContext } from "../../nodes/useNode";
import { useCentral } from "../central/central";

function under(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/** Which section of the settings a path is, the deepest first (`/settings` is General's). */
export function settingsTabOf(pathname: string): (typeof SETTINGS_TABS)[number] | undefined {
  const exact = SETTINGS_TABS.find((tab) => tab.exact === true && tab.to === pathname);
  return exact ?? SETTINGS_TABS.find((tab) => tab.exact !== true && under(pathname, tab.to));
}

/**
 * Settings, split by whose they are (spec 4.3). The selected server's own - General,
 * Notifications, Integrations, About - follow the selector: on web-2 they are
 * `/n/web-2/settings/...` and read and write web-2. The central's - its servers, security, API
 * tokens and seal - are the central's whichever server is selected, and say so in their
 * heading, in their group of the navigation and under the title.
 *
 * On a lone server there is nothing to tell apart: one list, no headings. On a fleet the
 * navigation has two groups, the server's (named, in mono) and the central's; from the
 * central's sections the server's group still leads back to the server the operator came from,
 * and a note says how that server's own sign-in, two-factor and tokens are managed there -
 * a node refuses a central on those on purpose, so a compromised central cannot lock anyone out.
 */
export function SettingsShell({ children }: { children: ReactNode }) {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const central = useCentral();
  const back = useReturnServer();
  const { hostname } = useServerList();
  const pathname = useRouterState({ select: (state) => state.location.pathname });

  const onCentral = context.kind === "central";
  // The server the server's sections are about: the one on screen, or the one to go back to.
  const serverNode = context.kind === "server" ? context.node : (back?.node ?? null);
  const serverName = serverNode ?? hostname ?? t("fleet.selector.thisServer");
  const centralName = hostname ?? t("fleet.selector.central");
  const scope: SettingsScope = onCentral ? "central" : "server";

  const serverHeading = (
    <>
      <Server aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
      <span className="sr-only">{t("nav.settingsGroups.serverLabel")}</span>{" "}
      <Mono tone="default" truncate className="min-w-0">
        {serverName}
      </Mono>
    </>
  );
  const centralHeading = (
    <>
      <Landmark aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
      <span className="min-w-0 truncate">{t.rich("nav.settingsGroups.central", { name: <Mono key="central">{centralName}</Mono> })}</span>
    </>
  );

  const { data: session } = useQuery(sessionQuery());
  const items: SettingsNavItem[] = SETTINGS_TABS.filter((tab) => tab.fleetOnly !== true || hasFleet || central.sealed)
    .filter((tab) => tab.permission === undefined || (session?.permissions?.includes(tab.permission) ?? false))
    .map((tab) => ({
    to: tab.to,
    label: t(tab.label),
    ...(tab.exact === true ? { exact: true } : {}),
    // From the central's sections, the server's lead back to the server the operator was on.
    ...(tab.scope === "server" && onCentral ? { search: { node: serverNode ?? undefined } } : {}),
    ...(hasFleet
      ? tab.scope === "server"
        ? { group: serverHeading, groupKey: "server" }
        : { group: centralHeading, groupKey: "central" }
      : {}),
  }));

  const description = !hasFleet
    ? t("settings.page.description")
    : onCentral
      ? t.rich("nav.settingsGroups.centralDescription", { name: <Mono key="central">{centralName}</Mono> })
      : t.rich("nav.settingsGroups.serverDescription", { name: <Mono key="server">{serverName}</Mono> });

  return (
    // T3 under the title "Settings" (the T2 header, with no tabs): the header is the layout's,
    // the same on every section, and says whose sections these are.
    <DetailPage
      header={{
        title: t("settings.page.title"),
        description,
        // The central's sections are no one server's: the header names the central instead.
        ...(onCentral && hasFleet ? { meta: <CentralLabel name={centralName} /> } : {}),
      }}
    >
      <div data-scope={scope} className="min-w-0">
        <SettingsLayout label={t("nav.landmarks.settingsSections")} items={items} index={pathname === "/settings"} backTo="/settings">
          {onCentral && hasFleet && back !== null && back.node !== null ? <NodeAccessNote node={back.node} /> : null}
          {children}
        </SettingsLayout>
      </div>
    </DetailPage>
  );
}

/** "Central · nas": which machine the central's settings are, beside the title. */
function CentralLabel({ name }: { name: string }) {
  const t = useT();
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-13 text-fg-muted">
      <Landmark aria-hidden="true" className="size-icon-sm shrink-0" />
      {t.rich("nav.settingsGroups.central", { name: <Mono key="central">{name}</Mono> })}
    </span>
  );
}

/**
 * Why a node's own sign-in, two-factor and tokens are not here, and where they are: the node
 * refuses a central on them on purpose (`FLEET_REFUSED_PREFIXES`), so they are managed on it.
 */
function NodeAccessNote({ node }: { node: string }) {
  const t = useT();
  return (
    <Notice title={t("nav.settingsGroups.nodeNoteTitle", { node })}>
      <div className="flex flex-col gap-2">
        <p className="max-w-measure text-pretty">{t("nav.settingsGroups.nodeNote", { node })}</p>
        <CommandHint label={t("nav.settingsGroups.nodeNoteCommands", { node })} command="noust user list && noust token list" />
      </div>
    </Notice>
  );
}
