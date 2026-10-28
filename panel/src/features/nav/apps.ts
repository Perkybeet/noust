import { hidden } from "../../app/navCore";
import type { Strip } from "../../app/navCore";
import { APPS_AREA } from "./appsTabs";

/**
 * Applications and the wizard that makes one. The register is app/nav.ts; this is one group of it.
 * The wizard is reached from the list and is in no strip. Projects, the other view of the list, is
 * written with its own screens (features/nav/projects.ts), and the recipes are the catalog's.
 */
export const APPS_SCREENS: Strip = [APPS_AREA, hidden("apps.new", "/apps/new", "newApp.page.title", "apps", { parent: "apps" })];
