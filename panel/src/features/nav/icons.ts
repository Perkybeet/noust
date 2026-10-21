import {
  Anchor,
  BadgeInfo,
  Blocks,
  BookCheck,
  Bot,
  Braces,
  CloudCog,
  CodeXml,
  Computer,
  DoorOpen,
  FileClock,
  FileDown,
  FileJson,
  FileOutput,
  Fingerprint,
  FolderSymlink,
  GitFork,
  HardDriveDownload,
  IdCard,
  Key,
  ListTodo,
  ListTree,
  MemoryStick,
  Megaphone,
  MonitorCog,
  Package,
  RadioTower,
  Radar,
  ScanEye,
  Ship,
  ShieldHalf,
  ShieldUser,
  Siren,
  Sliders,
  Stamp,
  Truck,
  UserLock,
  UserRoundCog,
  Vault,
  Wrench,
  Zap,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { Dest } from "../../app/navCore";
import { ICONS } from "../../components/ui/icons";

/**
 * What each section of a list on the side is, drawn: Settings, an application's settings and the
 * account. An object, never a state (colour is for state), and not one icon twice in a list
 * (docs/DESIGN.md I-5, NV-19). The areas carry their own, where they are written (the sidebar draws
 * them); a tab has none of its own, because no strip draws one (its width is the budget) and the
 * palette shows the area's it is under.
 *
 * A table of its own, and not an argument of each entry, so that a page that draws a strip of tabs
 * reads the group of screens (features/nav/backups.ts) and not these. And the icons here are the
 * ones nothing else in the console draws (`icons.test.ts` holds it): an icon two places import
 * becomes a file of its own that every route which reaches it must list to preload, and forty of
 * them cost the first page more than the icons are worth; one nobody else imports stays in this
 * table's file, and a route lists that one.
 */
const BY_ID: Readonly<Record<string, LucideIcon>> = {
  // General, the first entry of an application's settings: its own tab drawn as the head of the list.
  "app.settings": Sliders,
  "app.settings.source": GitFork,
  "app.settings.build": Wrench,
  "app.settings.builds": Package,
  "app.settings.container": Ship,
  "app.settings.deploys": Truck,
  "app.settings.deploy-on-push": Zap,
  "app.settings.hooks": Anchor,
  "app.settings.previews": ScanEye,
  "app.settings.resources": MemoryStick,
  "app.settings.disk": HardDriveDownload,
  "app.settings.processes": ListTree,
  "app.settings.mounts": FolderSymlink,
  "app.settings.security": ShieldHalf,
  "app.settings.access": DoorOpen,
  "app.settings.export": FileDown,
  "app.settings.delete": ICONS.delete,
  "settings.variables": Braces,
  "settings.secret-managers": Vault,
  "settings.console": MonitorCog,
  "settings.integrations": Blocks,
  "settings.about": BadgeInfo,
  "settings.notifications": Megaphone,
  "settings.alerts": Siren,
  "settings.probes": Radar,
  "settings.log-shipping": FileOutput,
  "settings.tokens": Key,
  "settings.api": CodeXml,
  "settings.manifest": FileJson,
  "settings.mcp": Bot,
  "settings.accounts": IdCard,
  "settings.access": UserLock,
  "settings.sso": Fingerprint,
  "settings.approvals": Stamp,
  "settings.audit": FileClock,
  "settings.controls": ListTodo,
  "settings.compliance": BookCheck,
  "settings.servers": Computer,
  "settings.cloud": CloudCog,
  "settings.central": RadioTower,
  account: UserRoundCog,
  "account.security": ShieldUser,
};

/** The ids this table draws: a test holds them to the sections of the register, in both directions. */
export const ICON_IDS: readonly string[] = Object.keys(BY_ID);

/** What a section is, drawn: its own or the table's. A section with neither is a typo, and a test says so. */
export function iconOf(entry: Pick<Dest, "id" | "icon">): LucideIcon {
  const icon = entry.icon ?? BY_ID[entry.id];
  if (icon === undefined) throw new Error(`the section "${entry.id}" has no icon: add it to features/nav/icons.ts`);
  return icon;
}

/** The icon of a section, or none: for what draws a screen that may be a tab, which has no icon of its own. */
export function sectionIcon(entry: Pick<Dest, "id" | "icon">): LucideIcon | undefined {
  return entry.icon ?? BY_ID[entry.id];
}
