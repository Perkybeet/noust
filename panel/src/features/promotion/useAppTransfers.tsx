import { useState } from "react";
import type { ReactNode } from "react";

import { previewParentOf } from "../apps/data";
import type { MovableApp } from "../teams/MoveAppDialog";
import { useOrganization } from "../teams/useOrganization";
import { CloneDialog } from "./CloneDialog";
import { PromoteDialog } from "./PromoteDialog";

/** What the actions need to know about an application. */
export interface TransferableApp extends MovableApp {
  /** `releases` for an app with instant rollback: only those have a release to promote. */
  layout?: string | null | undefined;
}

export interface AppTransfer {
  /** Whether the action means something for this app and the operator may do it. */
  available: boolean;
  open: () => void;
  dialog: ReactNode;
}

/**
 * "Promote to another environment" for one application: the release that serves goes to the
 * same app in another environment of its project, without being built again. Offered where it
 * can mean something: the app has releases, is not a preview, the project has more than one
 * environment, and the operator may deploy in the app's team. Which apps can receive it is the
 * dialog's to read and the server's to decide.
 */
export function usePromoteApp(app: TransferableApp | undefined): AppTransfer {
  const org = useOrganization();
  const [opened, setOpened] = useState(false);
  const available =
    app?.layout === "releases" && previewParentOf(app) === null && org.available && org.environments.length > 1 && org.can("apps.deploy", app.team ?? null);
  return {
    available,
    open: () => setOpened(true),
    dialog: opened && app !== undefined ? <PromoteDialog domain={app.domain} open onOpenChange={setOpened} /> : null,
  };
}

/**
 * "Clone application" for one application: another app with its settings, in the same or another
 * environment. Offered to whoever may manage applications in the app's team, except on a preview.
 * Always offered otherwise: a server nobody organised can still clone to another domain.
 */
export function useCloneApp(app: TransferableApp | undefined): AppTransfer {
  const org = useOrganization();
  const [opened, setOpened] = useState(false);
  const available = app !== undefined && previewParentOf(app) === null && (!org.available || org.can("apps.manage", app.team ?? null));
  return {
    available,
    open: () => setOpened(true),
    dialog: opened && app !== undefined ? <CloneDialog app={app} open onOpenChange={setOpened} /> : null,
  };
}
