/**
 * What the command palette does to an application, not only where it goes (checklist M-25):
 * "restart shop.example.com", "roll back shop.example.com". One command per verb and per
 * application the principal may act on, found by the verb and by the application.
 *
 * Choosing one does not run a call of its own. It hands the verb to `VerbRunner`, which runs the
 * application's own hook (`useAppActions`) and draws the dialogs its buttons draw (the question
 * before a stop, the choice of version before a rollback), so the friction, the sudo mode, the
 * permission and the audit trail are the buttons', not a copy of them that could drift. Pure, so
 * every rule is tested without a palette.
 */

import { CircleArrowUp, Play, RotateCw, Square } from "lucide-react";
import type { ReactNode } from "react";

import { appStatus } from "../components/page/status";
import { planBulk } from "../features/apps/bulk";
import type { AppInfo } from "../features/apps/data";
import type { PlainKey, T } from "../i18n";
import type { Command } from "./CommandPalette";
import { OBJECT_ICONS } from "../components/ui/objectIcons";

/** The verbs the application's header offers every day, in its order. */
export type AppVerb = "restart" | "start" | "stop" | "rollback" | "update";

/** The order the header draws them in: Restart, Stop or Start, Roll back, then Update. */
const ORDER: readonly AppVerb[] = ["restart", "stop", "start", "rollback", "update"];

/** The permission each verb's endpoint declares (`web/permissions/routes_apps.py`, `routes_jobs.py`). */
const PERMISSION: Record<AppVerb, "apps.operate" | "apps.deploy"> = {
  restart: "apps.operate",
  start: "apps.operate",
  stop: "apps.operate",
  rollback: "apps.deploy",
  update: "apps.deploy",
};

const LABEL = {
  restart: "shell.commands.verbs.restart",
  start: "shell.commands.verbs.start",
  stop: "shell.commands.verbs.stop",
  rollback: "shell.commands.verbs.rollback",
  update: "shell.commands.verbs.update",
} as const satisfies Record<AppVerb, string>;

const KEYWORDS = {
  restart: "shell.commands.verbs.restartKeywords",
  start: "shell.commands.verbs.startKeywords",
  stop: "shell.commands.verbs.stopKeywords",
  rollback: "shell.commands.verbs.rollbackKeywords",
  update: "shell.commands.verbs.updateKeywords",
} as const satisfies Record<AppVerb, PlainKey>;

const ICON: Record<AppVerb, ReactNode> = {
  restart: <RotateCw />,
  start: <Play />,
  stop: <Square />,
  rollback: <OBJECT_ICONS.rollback />,
  update: <CircleArrowUp />,
};

/** The verbs that open a dialog the operator then works in; the others leave focus where it was. */
const OPENS_DIALOG: ReadonlySet<AppVerb> = new Set(["stop", "rollback"]);

export interface VerbContext {
  t: T;
  /** Whether the principal holds a permission anywhere, or in the application's team. */
  can: (permission: string, team: string | null) => boolean;
  /** The application on screen: its verbs are the ones offered before anything is typed. */
  current: string | null;
  /** Starts a verb on an application. */
  run: (verb: AppVerb, app: AppInfo) => void;
}

/**
 * Whether a verb makes sense for an application right now: the bulk actions' own plan decides
 * the four they share (so the palette and the list's bulk menu cannot disagree about a static
 * site or one that is being deployed), and going back is always possible.
 */
function applies(verb: AppVerb, app: AppInfo): boolean {
  if (verb === "rollback") return true;
  if (planBulk(verb, [app])[0]?.skip !== null) return false;
  // A stack running outside its unit is neither started nor stopped through the unit: the
  // banner's "Hand it back to Noust" is the way (the header leaves both out for it too).
  if ((verb === "start" || verb === "stop") && appStatus(app.status).state === "warning") return false;
  return true;
}

/**
 * @param apps - The applications the console lists.
 * @param context - Translator, what the principal may do, the application on screen, and the runner.
 * @returns One command per verb that applies to each application the principal may act on.
 */
export function appVerbCommands(apps: readonly AppInfo[], { t, can, current, run }: VerbContext): Command[] {
  return apps.flatMap((app) =>
    ORDER.filter((verb) => can(PERMISSION[verb], app.team ?? null) && applies(verb, app)).map(
      (verb): Command => ({
        id: `verb:${verb}:${app.domain}`,
        group: "Operations",
        label: t(LABEL[verb], { domain: app.domain }),
        icon: ICON[verb],
        // Found once the verb is typed; then the application is found by its names too, so
        // "restart shop" finds it by the name it was created with.
        verb: t(KEYWORDS[verb]),
        keywords: [t(KEYWORDS[verb]), app.domain, app.name].join(" "),
        // What a person types on the page of one application is about that application.
        ...(current === app.domain ? { browse: true } : {}),
        kind: OPENS_DIALOG.has(verb) ? "navigate" : "action",
        run: () => {
          run(verb, app);
        },
      }),
    ),
  );
}
