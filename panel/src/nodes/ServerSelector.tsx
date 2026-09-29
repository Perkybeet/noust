import { Menu as BaseMenu } from "@base-ui/react/menu";
import { Check, ChevronsUpDown } from "lucide-react";

import { announce } from "../app/Announcer";
import { StatusGlyph, stateTextClass } from "../components/ui/StatusPill";
import { POPUP_MOTION } from "../components/ui/Tooltip";
import { useT } from "../i18n";
import { cx } from "../lib/cx";
import { NODE_STATE, nodeStatus, useServerList } from "./servers";
import type { NodeRecord } from "./servers";
import { useNode, useSwitchNode } from "./useNode";

/** The radio value of this server; no node name can be it (names are [a-z0-9-]). */
const THIS_SERVER = "@this";

const ITEM = cx(
  "flex min-h-11 cursor-pointer items-center gap-2.5 rounded-control px-2 py-1.5 text-13 outline-none select-none",
  "text-fg data-highlighted:bg-surface-hover",
);

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
        <span translate="no" className="mono truncate font-medium">
          {node.name}
        </span>{" "}
        <span className="flex items-center gap-1.5 text-12 text-fg-muted">
          <span className={cx("inline-flex items-center gap-1", stateTextClass(view.state))}>
            <StatusGlyph state={view.state} size={10} />
            {t(view.label)}
          </span>
          {" "}
                        <span aria-hidden="true">·</span>{" "}
          <span className="mono truncate">
            {node.version ? t("fleet.selector.version", { version: node.version }) : t("fleet.selector.noVersion")}
          </span>
        </span>
      </span>
    </BaseMenu.RadioItem>
  );
}

/**
 * The server the console is looking at, and the way to another: this server first, then
 * every node of the central with its reachability and version. Always shows the current
 * server's name; hidden entirely on a server with no nodes. A menu of radio items (Base UI:
 * arrow keys, Home/End, typeahead, Escape), so a screen reader hears which one is checked.
 */
export function ServerSelector({ className }: { className?: string }) {
  const t = useT();
  const { node } = useNode();
  const switchNode = useSwitchNode();
  const servers = useServerList();

  if (servers.nodes.length === 0 && node === null) return null;

  const thisServerName = servers.hostname ?? t("fleet.selector.thisServer");
  const current = node === null ? undefined : servers.nodes.find((candidate) => candidate.name === node);
  const currentView = current === undefined ? null : NODE_STATE[nodeStatus(current)];
  // A node that does not answer says so on the trigger itself, in its word and shape; a
  // reachable one needs nothing beside its name.
  const trouble = currentView !== null && currentView.state !== "running" ? currentView : null;
  const name = node ?? thisServerName;

  const choose = (value: unknown): void => {
    if (typeof value !== "string") return;
    const target = value === THIS_SERVER ? null : value;
    if (target === node) return;
    void switchNode(target).then(() => {
      announce(t("fleet.selector.switched", { name: target ?? thisServerName }));
    });
  };

  return (
    <BaseMenu.Root
      onOpenChange={(open: boolean) => {
        if (open) servers.refetch();
      }}
    >
      <BaseMenu.Trigger
        aria-label={trouble ? t("fleet.selector.triggerWithStatus", { name, status: t(trouble.label) }) : t("fleet.selector.trigger", { name })}
        className={cx(
          "flex h-8 max-w-44 min-w-0 shrink cursor-pointer items-center gap-1.5 rounded-control border border-border bg-surface pr-1.5 pl-2.5 text-13 text-fg shadow-raised",
          "transition-colors duration-(--duration-fast) ease-out hover:border-border-strong/60",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
          className,
        )}
      >
        {trouble ? <StatusGlyph state={trouble.state} size={10} className={stateTextClass(trouble.state)} /> : null}
        <span translate="no" className="mono min-w-0 truncate font-medium">
          {name}
        </span>
        {trouble ? (
          <span className={cx("shrink-0 text-12 font-medium max-sm:hidden", stateTextClass(trouble.state))}>{t(trouble.label)}</span>
        ) : null}
        <ChevronsUpDown aria-hidden="true" className="size-3.5 shrink-0 text-fg-faint" />
      </BaseMenu.Trigger>
      <BaseMenu.Portal>
        <BaseMenu.Positioner side="bottom" align="start" sideOffset={4} className="z-50 outline-none">
          {/* Named by its trigger ("Server: web-2"), which Base UI links with aria-labelledby. */}
          <BaseMenu.Popup
            className={cx(
              "max-h-[min(70vh,28rem)] w-72 overflow-y-auto rounded-card border border-border bg-surface-raised p-1 text-fg shadow-overlay outline-none scroll-thin",
              POPUP_MOTION,
            )}
          >
            <BaseMenu.RadioGroup value={node ?? THIS_SERVER} onValueChange={choose}>
              <BaseMenu.GroupLabel className="px-2 pt-1.5 pb-1 text-12 font-medium text-fg-faint select-none">
                {t("fleet.selector.label")}
              </BaseMenu.GroupLabel>
              <BaseMenu.RadioItem value={THIS_SERVER} closeOnClick className={ITEM}>
                <ItemCheck />
                <span className="flex min-w-0 flex-1 flex-col">
                  <span translate="no" className="mono truncate font-medium">
                    {thisServerName}
                  </span>{" "}
                  <span className="flex items-center gap-1.5 text-12 text-fg-muted">
                    <span>{t("fleet.selector.thisServer")}</span>
                    {servers.version !== null ? (
                      <>
                        {" "}
                        <span aria-hidden="true">·</span>{" "}
                        <span className="mono truncate">{t("fleet.selector.version", { version: servers.version })}</span>
                      </>
                    ) : null}
                  </span>
                </span>
              </BaseMenu.RadioItem>
              {servers.nodes.map((candidate) => (
                <NodeItem key={candidate.name} node={candidate} />
              ))}
            </BaseMenu.RadioGroup>
            {servers.failed ? (
              <BaseMenu.Item disabled className="px-2 py-1.5 text-12 text-fg-muted">
                {t("fleet.selector.listFailed")}
              </BaseMenu.Item>
            ) : null}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}
