import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import type { AttentionAction } from "../overview/attentionTarget";

/**
 * What each button of a row of the inbox acts on, drawn before its word: the object's own icon
 * (components/ui/objectIcons.ts), the verb being the button's word. In a file of its own so that the
 * Overview's card, which draws the same buttons without icons, does not read the map.
 */
const OPEN_ICON: Readonly<Record<Extract<AttentionAction, { mode: "open" }>["kind"], LucideIcon>> = {
  viewLog: OBJECT_ICONS.log,
  diagnose: OBJECT_ICONS.app,
  renew: OBJECT_ICONS.certificate,
  openService: OBJECT_ICONS.service,
  openFinding: OBJECT_ICONS.server,
  openProbes: OBJECT_ICONS.probe,
  review: OBJECT_ICONS.approval,
  openUpdates: OBJECT_ICONS.update,
  reviewRecipe: OBJECT_ICONS.recipe,
};

/** The icon of a button, as the node the button draws: decorative, the size of a small button's line. */
export function attentionIcon(action: AttentionAction): ReactNode {
  const Icon = action.mode === "alert" ? OBJECT_ICONS.alert : OPEN_ICON[action.kind];
  return <Icon aria-hidden="true" className="size-icon-sm shrink-0" />;
}
