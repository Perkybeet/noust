import { hidden } from "../../app/navCore";
import type { Strip } from "../../app/navCore";
import { APPS_TABS } from "./appsTabs";

const [apps, ...views] = APPS_TABS;

/**
 * Applications, the wizard that makes one, and the recipes it can start from. The register is
 * app/nav.ts; this is one group of it. The wizard is reached from the list and is in no strip.
 */
export const APPS_SCREENS: Strip = apps === undefined ? [] : [apps, hidden("apps.new", "/apps/new", "newApp.page.title", "apps", { parent: "apps" }), ...views];
