import type { LucideIcon } from "lucide-react";
import { createContext, useContext } from "react";

import type { App } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { SETTINGS_SECTIONS } from "./sections";

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
  useDocumentTitle(t("appSettings.documentTitle", { section: t(section), domain }), 1);
}


/**
 * The icon the register gives a subsection, as the props of the `Section` that heads its page: one
 * object, one icon, so the heading draws the one its entry in the side list does (docs/DESIGN.md,
 * 2.8). Nothing while the register gives none.
 */
export function subsectionIcon(section: PlainKey): { icon?: LucideIcon } {
  const icon = SETTINGS_SECTIONS.find((candidate) => candidate.label === section)?.icon;
  return icon === undefined ? {} : { icon };
}
