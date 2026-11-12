import { Cloud, Link as LinkIcon } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { FleetKindSection } from "./FleetKindSection";

/** The kinds of setting the central carries to every server, which a section of the console shows (`fleet/settings/kinds.py`). */
export type FleetResource = "channel" | "alert_rule" | "backup_destination" | "registry" | "variable" | "console_link" | "policy";

const ICON: Record<FleetResource, LucideIcon> = {
  channel: OBJECT_ICONS.channel,
  alert_rule: OBJECT_ICONS.alert,
  backup_destination: Cloud,
  registry: OBJECT_ICONS.registry,
  variable: OBJECT_ICONS.variable,
  console_link: LinkIcon,
  policy: OBJECT_ICONS.general,
};

/**
 * What the central carries to every server of one kind, in the section of the console where that kind belongs: its words
 * and its icon are the kind's, and the section it is drawn in only says where. Nothing on a lone server (`FleetKindSection`).
 */
export function FleetBlock({ resource }: { resource: FleetResource }) {
  const t = useT();
  const words = {
    channel: {
      title: t("fleetSettings.section.channel.title"),
      description: t("fleetSettings.section.channel.description"),
      valueLabel: t("fleetSettings.section.channel.value"),
      add: t("fleetSettings.section.channel.add"),
      emptyTitle: t("fleetSettings.section.channel.emptyTitle"),
      emptyDescription: t("fleetSettings.section.channel.emptyDescription"),
    },
    alert_rule: {
      title: t("fleetSettings.section.alert_rule.title"),
      description: t("fleetSettings.section.alert_rule.description"),
      valueLabel: t("fleetSettings.section.alert_rule.value"),
      add: t("fleetSettings.section.alert_rule.add"),
      emptyTitle: t("fleetSettings.section.alert_rule.emptyTitle"),
      emptyDescription: t("fleetSettings.section.alert_rule.emptyDescription"),
    },
    backup_destination: {
      title: t("fleetSettings.section.backup_destination.title"),
      description: t("fleetSettings.section.backup_destination.description"),
      valueLabel: t("fleetSettings.section.backup_destination.value"),
      add: t("fleetSettings.section.backup_destination.add"),
      emptyTitle: t("fleetSettings.section.backup_destination.emptyTitle"),
      emptyDescription: t("fleetSettings.section.backup_destination.emptyDescription"),
    },
    registry: {
      title: t("fleetSettings.section.registry.title"),
      description: t("fleetSettings.section.registry.description"),
      valueLabel: t("fleetSettings.section.registry.value"),
      add: t("fleetSettings.section.registry.add"),
      emptyTitle: t("fleetSettings.section.registry.emptyTitle"),
      emptyDescription: t("fleetSettings.section.registry.emptyDescription"),
    },
    variable: {
      title: t("fleetSettings.section.variable.title"),
      description: t("fleetSettings.section.variable.description"),
      valueLabel: t("fleetSettings.section.variable.value"),
      add: t("fleetSettings.section.variable.add"),
      emptyTitle: t("fleetSettings.section.variable.emptyTitle"),
      emptyDescription: t("fleetSettings.section.variable.emptyDescription"),
    },
    policy: {
      title: t("fleetSettings.section.policy.title"),
      description: t("fleetSettings.section.policy.description"),
      valueLabel: t("fleetSettings.section.policy.value"),
      add: t("fleetSettings.section.policy.add"),
      emptyTitle: t("fleetSettings.section.policy.emptyTitle"),
      emptyDescription: t("fleetSettings.section.policy.emptyDescription"),
    },
    console_link: {
      title: t("fleetSettings.section.console_link.title"),
      description: t("fleetSettings.section.console_link.description"),
      valueLabel: t("fleetSettings.section.console_link.value"),
      add: t("fleetSettings.section.console_link.add"),
      emptyTitle: t("fleetSettings.section.console_link.emptyTitle"),
      emptyDescription: t("fleetSettings.section.console_link.emptyDescription"),
    },
  }[resource];
  return (
    <FleetKindSection
      resource={resource}
      icon={ICON[resource]}
      {...words}
      onOff={resource === "channel" || resource === "alert_rule"}
      {...(resource === "variable" ? { table: true, nameLabel: t("fleetSettings.section.variable.name") } : {})}
    />
  );
}
