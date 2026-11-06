import type { LucideIcon } from "lucide-react";
import { createContext, useContext } from "react";

import type { App } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import type { SaveBarHandle } from "./formParts";

/**
 * The application whose settings are open. The settings' layout provides it once the app is
 * read, so every subsection renders with the app in hand and none of them repeats the load,
 * its skeleton or its failure (the app's own layout says those).
 */
export const SettingsAppContext = createContext<App | null>(null);

/** The application whose settings are open: only inside the settings' layout. */
export function useSettingsApp(): App {
  const app = useContext(SettingsAppContext);
  if (app === null) throw new Error("useSettingsApp is used outside AppSettingsLayout");
  return app;
}

/** Names the browser tab after the subsection: "Deploys - Settings - shop.example.com". */
export function useSubsectionTitle(section: PlainKey, domain: string): void {
  const t = useT();
  // Inside a page that gathers it, the page names the browser tab, not each part of it.
  const hosted = useContext(PartHostContext);
  useDocumentTitle(hosted ? null : t("appSettings.documentTitle", { section: t(section), domain }), 1);
}


/** True inside a page that gathers parts (`features/app/pages/ConfigPage`): a part leaves out what the page says once. */
export const PartHostContext = createContext(false);

/** True inside a part that its page folds under a button: its own heading is then the button's, kept for a reader but not drawn. */
export const PartFoldedContext = createContext(false);

/** What a part writes its save state to, inside a page that has one bar for all of them (`saveHost.tsx`). */
export interface SaveRegistry {
  set: (id: string, bar: SaveBarHandle) => void;
  remove: (id: string) => void;
}
export const SaveRegistryContext = createContext<SaveRegistry | null>(null);

/**
 * The icons the register gives the subsections, by the label of each: the settings' layout provides
 * them, so a page never imports the register (which names the strings of a dozen namespaces) to
 * head itself with the icon its entry in the side list has.
 */
export const SettingsIconsContext = createContext<ReadonlyMap<PlainKey, LucideIcon>>(new Map());

/**
 * The icon of a subsection, as the props of the `Section` that heads its page: one object, one icon,
 * so the heading draws the one its entry in the side list does (docs/DESIGN.md, 2.8). Nothing while
 * the register gives none.
 */
export function useSubsectionIcon(section: PlainKey): { icon?: LucideIcon } {
  const icon = useContext(SettingsIconsContext).get(section);
  return icon === undefined ? {} : { icon };
}
