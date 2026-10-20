import { Boxes } from "lucide-react";

import { area, hidden, strip } from "../../app/navCore";

/** Applications and the wizard that makes one. The register is app/nav.ts; this is one group of it. */
export const APPS_SCREENS = strip(
  area("apps", "/apps", "nav.apps.label", Boxes, { group: "runs", shortcut: ["g", "a"], goTo: "nav.apps.goTo", keywords: "nav.apps.keywords" }),
  () => [hidden("apps.new", "/apps/new", "newApp.page.title", "apps", { parent: "apps" })],
);
