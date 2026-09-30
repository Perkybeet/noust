import { createContext, useContext } from "react";

import type { App } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";

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

