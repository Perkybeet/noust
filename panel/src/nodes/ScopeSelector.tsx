import { Menu as BaseMenu } from "@base-ui/react/menu";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown, Landmark, Plus } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../api/queries/auth";
import { announce } from "../app/Announcer";
import { Badge } from "../components/ui/Badge";
import { Mono } from "../components/ui/Mono";
import { StatusGlyph, stateTextClass } from "../components/ui/StatusPill";
import { POPUP_MOTION } from "../components/ui/Tooltip";
import { useCentral } from "../features/central/central";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { NODE_STATE, nodeStatus, useHasFleet, useServerList } from "./servers";
import type { NodeRecord } from "./servers";
import { useConsoleContext, useSwitchNode } from "./useNode";
import { OBJECT_ICONS } from "../components/ui/objectIcons";

/** Radio values that are not a server; no node name can be one (names are [a-z0-9-]). */
const ALL_SERVERS = "@all";
const THIS_CENTRAL = "@central";
const THIS_SERVER = "@this";

const ITEM = cx(
  "flex min-h-11 cursor-pointer items-center gap-2.5 rounded-control px-2 py-1.5 text-13 outline-none select-none",
  "text-fg data-highlighted:bg-surface-hover",
);

const GROUP_LABEL = "px-2 pt-1.5 pb-1 text-12 font-medium text-fg-muted select-none";

function ItemCheck() {
  return (
    <span className="flex size-4 shrink-0 items-center justify-center">
      <BaseMenu.RadioItemIndicator>
        <Check aria-hidden="true" className="size-4 text-fg" />
      </BaseMenu.RadioItemIndicator>
    </span>
  );
}

function NodeItem({ node }: { node: NodeRecord }) {
  const t = useT();
  const view = NODE_STATE[nodeStatus(node)];
  return (
    <BaseMenu.RadioItem value={node.name} closeOnClick className={ITEM}>
      <ItemCheck />
      <span className="flex min-w-0 flex-1 flex-col">
        <Mono truncate className="font-medium">
          {node.name}
        </Mono>{" "}
        <span className="flex items-center gap-1.5 text-12 text-fg-muted">
          <span className={cx("inline-flex items-center gap-1", stateTextClass(view.state))}>
            <StatusGlyph state={view.state} size={10} />
            {t(view.label)}
          </span>{" "}
          <span aria-hidden="true">·</span>{" "}
          <Mono tone="muted" truncate>
            {node.version ? t("fleet.selector.version", { version: node.version }) : t("fleet.selector.noVersion")}
          </Mono>
        </span>
      </span>
    </BaseMenu.RadioItem>
  );
}

/** Where the selector is drawn: the head of the sidebar (from `lg`), or the top bar (below it). */
export type ScopePlacement = "sidebar" | "topbar";

/**
 * Which scope the console is in, and the way to another: one server (this one or a node), "All
 * servers" (the fleet's pages) or "This central" (the central's own settings, which use the
 * fleet's bar). It is the head of the sidebar from `lg` and the top bar below it (the caller
 * chooses with a media query, never with CSS, which would draw both and repeat their ids): a menu
 * in a drawer is not on screen, and the server's name must always be (DESIGN 6.11, NV-10). Nothing
 * is drawn on a lone server with no nodes: there is nothing to tell apart.
 *
 * A menu of radio items (Base UI: arrow keys, Home/End, typeahead, Escape), so a screen reader
 * hears which one is checked, and each switch is announced.
 */
export function ScopeSelector({ placement, className }: { placement: ScopePlacement; className?: string }) {
  const t = useT();
  const context = useConsoleContext();
  const hasFleet = useHasFleet();
  const { role } = useCentral();
  const switchNode = useSwitchNode();
  const navigate = useNavigate();
  const servers = useServerList();
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });

  const node = context.kind === "server" ? context.node : null;
  if (!hasFleet && node === null) return null;

  const hostname = servers.hostname ?? t("fleet.selector.thisServer");
  const current = node === null ? undefined : servers.nodes.find((candidate) => candidate.name === node);
  const currentView = current === undefined ? null : NODE_STATE[nodeStatus(current)];
  // A node that does not answer says so on the trigger itself, in its word and shape; a
  // reachable one needs nothing beside its name.
  const trouble = currentView !== null && currentView.state !== "running" ? currentView : null;
  const serverName = node ?? hostname;
  const value = context.kind === "fleet" ? ALL_SERVERS : context.kind === "central" ? THIS_CENTRAL : (node ?? THIS_SERVER);
  const mayAdd = permissions?.includes("fleet.manage") ?? false;
  const total = servers.nodes.length + (role === "hub" ? 0 : 1);
  const needing = servers.nodes.filter((candidate) => NODE_STATE[nodeStatus(candidate)].state !== "running" && NODE_STATE[nodeStatus(candidate)].state !== "unknown").length;

  const label =
    context.kind === "fleet"
      ? t("fleet.selector.triggerAll")
      : context.kind === "central"
        ? t("fleet.selector.triggerCentral", { name: hostname })
        : trouble
          ? t("fleet.selector.triggerWithStatus", { name: serverName, status: t(trouble.label) })
          : t("fleet.selector.trigger", { name: serverName });

  const choose = (chosen: unknown): void => {
    if (typeof chosen !== "string" || chosen === value) return;
    if (chosen === ALL_SERVERS) {
      void navigate({ to: "/fleet", search: { node: undefined } }).then(() => {
        announce(t("fleet.selector.switchedAll"));
      });
      return;
    }
    if (chosen === THIS_CENTRAL) {
      void navigate({ to: "/settings/central", search: { node: undefined } }).then(() => {
        announce(t("fleet.selector.switchedCentral"));
      });
      return;
    }
    const target = chosen === THIS_SERVER ? null : chosen;
    void switchNode(target).then(() => {
      announce(t("fleet.selector.switched", { name: target ?? hostname }));
    });
  };

  const side = placement === "sidebar";
  // The name and what it is, in the words of the scope; the sidebar adds a second line under it.
  let icon: ReactNode;
  let name: ReactNode;
  let line: ReactNode;
  if (context.kind === "fleet") {
    icon = <OBJECT_ICONS.node aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />;
    name = <span className="min-w-0 truncate font-medium">{t("fleet.selector.all")}</span>;
    line = (
      <>
        <span>{t("fleet.selector.lineServers", { count: total })}</span>
        {needing > 0 ? (
          <>
            {" "}
            <span aria-hidden="true">·</span> <span className="font-medium text-fail">{t("fleet.selector.lineNeed", { count: needing })}</span>
          </>
        ) : null}
      </>
    );
  } else if (context.kind === "central") {
    icon = <Landmark aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />;
    name = (
      <>
        <span className={cx("shrink-0 font-medium", !side && "max-sm:sr-only")}>{t("fleet.selector.central")}</span>
        {side ? null : (
          <Mono truncate className="min-w-0 font-medium">
            {hostname}
          </Mono>
        )}
      </>
    );
    line = (
      <Mono tone="muted" truncate>
        {hostname}
      </Mono>
    );
  } else {
    icon = <OBJECT_ICONS.server aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />;
    name = (
      <>
        {trouble ? <StatusGlyph state={trouble.state} size={10} className={stateTextClass(trouble.state)} /> : null}
        <Mono truncate className="min-w-0 font-medium">
          {serverName}
        </Mono>
        {trouble && !side ? <span className={cx("shrink-0 text-12 font-medium max-sm:hidden", stateTextClass(trouble.state))}>{t(trouble.label)}</span> : null}
      </>
    );
    line = trouble ? (
      <span className={cx("font-medium", stateTextClass(trouble.state))}>{t(trouble.label)}</span>
    ) : (
      <span>{node === null ? t("fleet.selector.thisServer") : t("fleet.selector.lineServer")}</span>
    );
  }

  return (
    <BaseMenu.Root
      onOpenChange={(open: boolean) => {
        if (open) servers.refetch();
      }}
    >
      <BaseMenu.Trigger
        aria-label={label}
        data-context={context.kind}
        data-placement={placement}
        className={cx(
          "flex min-w-0 cursor-pointer items-center gap-2 rounded-control border border-border-strong bg-surface text-13 text-fg shadow-raised",
          "transition-colors duration-(--duration-fast) ease-out hover:bg-surface-hover",
          side ? "min-h-control-lg w-full py-1 pr-2 pl-2.5" : "h-control-md max-w-60 shrink gap-1.5 pr-1.5 pl-2.5",
          className,
        )}
      >
        {side ? icon : null}
        {side ? (
          <span className="flex min-w-0 flex-1 flex-col text-left">
            <span className="flex min-w-0 items-center gap-1.5">{name}</span>
            <span className="truncate text-12 text-fg-muted">{line}</span>
          </span>
        ) : (
          <>
            {context.kind === "fleet" || context.kind === "central" ? icon : null}
            {name}
          </>
        )}
        <ChevronsUpDown aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
      </BaseMenu.Trigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner side="bottom" align="start" sideOffset={4} className="z-overlay outline-none">
          {/* Named by its trigger, which Base UI links with aria-labelledby. */}
          <BaseMenu.Popup
            className={cx(
              // design-exception: hand-surface a radio menu has no kit component; this is Menu's own popup surface
              "max-h-96 w-72 overflow-y-auto rounded-card border border-border bg-surface-raised p-1 text-fg shadow-overlay outline-none scroll-thin",
              POPUP_MOTION,
            )}
          >
            <BaseMenu.RadioGroup value={value} onValueChange={choose}>
              {hasFleet ? (
                <BaseMenu.Group>
                  <BaseMenu.GroupLabel className={GROUP_LABEL}>{t("fleet.selector.views")}</BaseMenu.GroupLabel>
                  <BaseMenu.RadioItem value={ALL_SERVERS} closeOnClick className={ITEM}>
                    <ItemCheck />
                    <OBJECT_ICONS.node aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate font-medium">{t("fleet.selector.all")}</span>{" "}
                      <span className="text-12 text-fg-muted">{t("fleet.selector.allDescription", { count: servers.nodes.length + (role === "hub" ? 0 : 1) })}</span>
                    </span>
                  </BaseMenu.RadioItem>
                  <BaseMenu.RadioItem value={THIS_CENTRAL} closeOnClick className={ITEM}>
                    <ItemCheck />
                    <Landmark aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="flex min-w-0 items-center gap-1.5">
                        <span className="shrink-0 font-medium">{t("fleet.selector.central")}</span>{" "}
                        <Mono tone="muted" truncate>
                          {hostname}
                        </Mono>
                      </span>{" "}
                      <span className="text-12 text-fg-muted">{t("fleet.selector.centralDescription")}</span>
                    </span>
                  </BaseMenu.RadioItem>
                </BaseMenu.Group>
              ) : null}
              <BaseMenu.Group>
                <BaseMenu.GroupLabel className={cx(GROUP_LABEL, hasFleet && "mt-1 border-t border-border pt-2")}>{t("fleet.selector.servers")}</BaseMenu.GroupLabel>
                <BaseMenu.RadioItem value={THIS_SERVER} closeOnClick className={ITEM}>
                  <ItemCheck />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <Mono truncate className="font-medium">
                      {hostname}
                    </Mono>{" "}
                    <span className="flex items-center gap-1.5 text-12 text-fg-muted">
                      <span>{t("fleet.selector.thisServer")}</span>
                      {role === "hub" ? (
                        <>
                          {" "}
                          <Badge>{t("fleet.selector.hub")}</Badge>
                        </>
                      ) : null}
                      {servers.version !== null ? (
                        <>
                          {" "}
                          <span aria-hidden="true">·</span>{" "}
                          <Mono tone="muted" truncate>
                            {t("fleet.selector.version", { version: servers.version })}
                          </Mono>
                        </>
                      ) : null}
                    </span>
                  </span>
                </BaseMenu.RadioItem>
                {servers.nodes.map((candidate) => (
                  <NodeItem key={candidate.name} node={candidate} />
                ))}
              </BaseMenu.Group>
            </BaseMenu.RadioGroup>
            {servers.failed ? (
              <BaseMenu.Item disabled className="px-2 py-1.5 text-12 text-fg-muted">
                {t("fleet.selector.listFailed")}
              </BaseMenu.Item>
            ) : null}
            {mayAdd ? (
              <div className="mt-1 border-t border-border pt-1">
                <BaseMenu.Item
                  className={ITEM}
                  onClick={() => {
                    void navigate({ to: "/fleet/servers", search: { add: true, node: undefined } });
                  }}
                >
                  <Plus aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
                  <span className="font-medium">{t("fleet.selector.addServer")}</span>
                </BaseMenu.Item>
              </div>
            ) : null}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
