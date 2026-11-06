/**
 * Every widget Home can draw: what it is called, what it is for, which sizes it takes, what a person must hold to
 * see it and what its body is. The gallery lists this, a panel draws from it, and a layout the server kept is read
 * against it (a type this console has not heard of is dropped). A widget that needs a permission is absent from the
 * gallery and from a panel for anyone without it: what a role may not see is not drawn, and it never asks the server
 * for what the server would refuse (a team member sees the applications' widgets and none of the machine's).
 */
import type { ComponentType } from "react";
import type { LucideIcon } from "lucide-react";

import type { PlainKey, T } from "../../i18n";
import { OBJECT_ICONS } from "../ui/objectIcons";
import { TextLink } from "../ui/TextLink";
import { ActionsBody } from "./bodies/ShortcutBodies";
import { AppsBody, DatabasesBody, DeploymentsBody, TasksBody } from "./bodies/AppBodies";
import { FleetBody } from "./bodies/FleetBody";
import { DiskBody, ProcessesBody, ResourcesBody, SecurityBody, ServicesBody, UpdatesBody } from "./bodies/MachineBodies";
import { BackupsBody, CertificatesBody, ProbesBody } from "./bodies/ProtectionBodies";
import { ActivityBody, AlertsBody, AttentionBody, AttentionLink, FiguresBody, WorkBody } from "./bodies/StateBodies";
import type { Placed, Settings, SizeKey } from "./model";
import { newId } from "./model";
import type { FaceOf, PanelScope, WidgetBodyProps } from "./types";

/** The widgets there are, by their id in a stored panel. */
export const WIDGET_TYPES = [
  "attention",
  "figures",
  "apps",
  "resources",
  "work",
  "deployments",
  "certificates",
  "backups",
  "activity",
  "actions",
  "fleet",
  "updates",
  "alerts",
  "disk",
  "processes",
  "tasks",
  "services",
  "databases",
  "probes",
  "security",
] as const;

export type WidgetType = (typeof WIDGET_TYPES)[number];

/** How the gallery groups them. */
export type WidgetCategory = "state" | "apps" | "machine" | "protection" | "fleet" | "shortcuts";

export const CATEGORY_ORDER: readonly WidgetCategory[] = ["state", "apps", "machine", "protection", "fleet", "shortcuts"];

export interface WidgetDef {
  type: WidgetType;
  icon: LucideIcon;
  category: WidgetCategory;
  /** The sizes it has something to say in; the first is the one it is added at. */
  sizes: readonly SizeKey[];
  /** The panels it makes sense in: one that follows the server chosen, or one that is the whole fleet. */
  scopes: readonly PanelScope[];
  /** What a person must hold, all of it, to see it in a panel of one server. A panel of the fleet needs `fleet.read` instead, whatever the widget reads. */
  needs: readonly string[];
  /** The link in its head, to where the whole of it is. */
  link?: { to: string; label: PlainKey };
  /** A head that is drawn from what the widget knows (the count of what needs attention). */
  Head?: ComponentType<Pick<WidgetBodyProps, "scope">>;
  Body: ComponentType<WidgetBodyProps>;
  /** What it keeps beside its place, when it has something to choose. */
  settings?: Settings;
}

export const WIDGETS: readonly WidgetDef[] = [
  { type: "attention", icon: OBJECT_ICONS.alert, category: "state", sizes: ["medium", "large", "wide", "xlarge"], scopes: ["server", "fleet"], needs: ["server.read"], Head: AttentionLink, Body: AttentionBody },
  { type: "figures", icon: OBJECT_ICONS.overview, category: "state", sizes: ["medium", "large", "wide"], scopes: ["server"], needs: ["server.read"], Body: FiguresBody },
  { type: "work", icon: OBJECT_ICONS.work, category: "state", sizes: ["small", "medium", "large"], scopes: ["server", "fleet"], needs: ["apps.read"], Body: WorkBody },
  { type: "activity", icon: OBJECT_ICONS.activity, category: "state", sizes: ["medium", "large", "tall"], scopes: ["server", "fleet"], needs: ["server.read"], link: { to: "/activity", label: "widgets.activity.all" }, Body: ActivityBody },
  { type: "alerts", icon: OBJECT_ICONS.firing, category: "state", sizes: ["small", "medium"], scopes: ["server", "fleet"], needs: ["server.read"], link: { to: "/alerts", label: "widgets.alerts.all" }, Body: AlertsBody },
  { type: "apps", icon: OBJECT_ICONS.app, category: "apps", sizes: ["small", "medium", "large", "wide"], scopes: ["server", "fleet"], needs: ["apps.read"], link: { to: "/apps", label: "widgets.apps.all" }, Body: AppsBody },
  { type: "deployments", icon: OBJECT_ICONS.deployment, category: "apps", sizes: ["small", "medium", "large", "wide"], scopes: ["server"], needs: ["apps.read"], link: { to: "/activity", label: "widgets.deployments.all" }, Body: DeploymentsBody },
  { type: "tasks", icon: OBJECT_ICONS.schedule, category: "apps", sizes: ["small", "medium"], scopes: ["server"], needs: ["apps.read"], link: { to: "/cron", label: "widgets.tasks.all" }, Body: TasksBody },
  { type: "databases", icon: OBJECT_ICONS.database, category: "apps", sizes: ["medium", "large", "tall"], scopes: ["server"], needs: ["databases.read"], link: { to: "/databases", label: "widgets.databases.all" }, Body: DatabasesBody },
  { type: "resources", icon: OBJECT_ICONS.resources, category: "machine", sizes: ["small", "medium", "tall"], scopes: ["server"], needs: ["server.read"], Body: ResourcesBody },
  { type: "disk", icon: OBJECT_ICONS.disk, category: "machine", sizes: ["small", "medium", "tall"], scopes: ["server"], needs: ["server.read"], Body: DiskBody },
  { type: "processes", icon: OBJECT_ICONS.processes, category: "machine", sizes: ["medium", "large"], scopes: ["server"], needs: ["server.read"], Body: ProcessesBody },
  { type: "services", icon: OBJECT_ICONS.service, category: "machine", sizes: ["small", "medium"], scopes: ["server"], needs: ["server.read"], link: { to: "/server/services", label: "widgets.services.all" }, Body: ServicesBody },
  { type: "updates", icon: OBJECT_ICONS.update, category: "machine", sizes: ["small", "medium", "large"], scopes: ["server", "fleet"], needs: ["server.read"], link: { to: "/server/system", label: "widgets.updates.all" }, Body: UpdatesBody },
  { type: "certificates", icon: OBJECT_ICONS.certificate, category: "protection", sizes: ["small", "medium", "large"], scopes: ["server", "fleet"], needs: ["apps.read"], Body: CertificatesBody },
  { type: "backups", icon: OBJECT_ICONS.backup, category: "protection", sizes: ["small", "medium", "tall"], scopes: ["server", "fleet"], needs: ["backups.read"], link: { to: "/backups", label: "widgets.backups.all" }, Body: BackupsBody },
  { type: "probes", icon: OBJECT_ICONS.probe, category: "protection", sizes: ["small", "medium", "wide"], scopes: ["server"], needs: ["server.read"], link: { to: "/alerts/probes", label: "widgets.probes.all" }, Body: ProbesBody },
  { type: "security", icon: OBJECT_ICONS.security, category: "protection", sizes: ["small", "medium"], scopes: ["server"], needs: ["server.read"], link: { to: "/server/security", label: "widgets.security.all" }, Body: SecurityBody },
  { type: "fleet", icon: OBJECT_ICONS.server, category: "fleet", sizes: ["medium", "large", "wide", "xlarge"], scopes: ["fleet"], needs: ["fleet.read"], link: { to: "/fleet/servers", label: "widgets.fleet.all" }, Body: FleetBody },
  { type: "actions", icon: OBJECT_ICONS.shortcuts, category: "shortcuts", sizes: ["small", "medium", "wide"], scopes: ["server", "fleet"], needs: [], Body: ActionsBody },
];

/** The widget of a type, or undefined for one this console does not have. */
export function widgetOf(type: string): WidgetDef | undefined {
  return WIDGETS.find((widget) => widget.type === type);
}

/** What a panel of a scope needs for one widget: the widget's own for a server, the fleet's read for the fleet. */
function needsFor(widget: WidgetDef, scope: PanelScope): readonly string[] {
  return scope === "fleet" ? ["fleet.read"] : widget.needs;
}

/** Whether a widget can be in a panel of this scope for a credential that holds these permissions. */
export function isOffered(widget: WidgetDef, scope: PanelScope, can: (permission: string) => boolean): boolean {
  return widget.scopes.includes(scope) && needsFor(widget, scope).every(can);
}

/** The widgets a panel of this scope may hold for what the credential holds, in the order the gallery lists them. */
export function offeredWidgets(scope: PanelScope, can: (permission: string) => boolean): WidgetDef[] {
  return WIDGETS.filter((widget) => isOffered(widget, scope, can));
}

/** A widget as it is first put in a panel: its first size and what it keeps by default, with an id that is free. */
export function newPlaced(widget: WidgetDef, layout: readonly Placed[]): Placed {
  return { id: newId(widget.type, layout.map((placed) => placed.id)), type: widget.type, size: widget.sizes[0] ?? "medium", settings: { ...widget.settings } };
}

/** The sizes of a widget type for `fromDocument`, or null for one that is not here (or that this credential may not see). */
export function sizesOf(scope: PanelScope, can: (permission: string) => boolean): (type: string) => readonly SizeKey[] | null {
  return (type) => {
    const widget = widgetOf(type);
    return widget !== undefined && isOffered(widget, scope, can) ? widget.sizes : null;
  };
}

/**
 * What the grid draws of each placed widget of a panel of this scope: its title and icon from the catalog, its link
 * (or its own head), and its body for the size it has. A widget this scope cannot hold has no face.
 */
export function faceFor(t: T, scope: PanelScope, onSettings?: (id: string, next: Settings) => void): FaceOf {
  return (item) => {
    const widget = widgetOf(item.type);
    if (widget === undefined) return null;
    const { Body, Head } = widget;
    return {
      title: t(`widgets.${widget.type}.title`),
      icon: widget.icon,
      sizes: widget.sizes,
      ...(Head !== undefined
        ? { link: <Head scope={scope} /> }
        : widget.link !== undefined
          ? {
              link: (
                <TextLink to={widget.link.to as never} size="ui" className="shrink-0 px-1 text-12">
                  {t(widget.link.label)}
                </TextLink>
              ),
            }
          : {}),
      body: (size) => <Body size={size} scope={scope} settings={item.settings} {...(onSettings !== undefined ? { onSettings: (next: Settings) => onSettings(item.id, next) } : {})} />,
    };
  };
}
