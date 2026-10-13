import { useNavigate } from "@tanstack/react-router";
import { ArrowRightLeft, CircleArrowUp, Copy, FolderInput, PanelTop, RotateCw, ScrollText } from "lucide-react";

import { appStatus } from "../../components/page/status";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import { useCloneApp, usePromoteApp } from "../promotion/useAppTransfers";
import { useMoveApp } from "../teams/MoveAppDialog";
import type { AppInfo } from "./data";
import { NothingNewDialog } from "./NothingNewDialog";
import { useAppActions } from "./useAppActions";

/** Whether the app has a unit to restart, start or stop. A static site is served by nginx alone. */
export function hasUnit(app: Pick<AppInfo, "status" | "app_type">): boolean {
  return appStatus(app.status).state !== "static" && app.app_type !== "static";
}

/**
 * The menu at the end of an application's row, named "Actions for {domain}": open it, read its
 * logs, restart, update. On a phone it sits at the top right of the app's card, always in view.
 */
export function AppRowActions({ app }: { app: AppInfo }) {
  const t = useT();
  const navigate = useNavigate();
  const { restart, update, rebuildAnyway, nothingNew, dismissNothingNew } = useAppActions(app.domain);
  const move = useMoveApp(app);
  const promote = usePromoteApp(app);
  const clone = useCloneApp(app);
  const domain = app.domain;
  const unit = hasUnit(app);
  return (
    <>
      <Menu align="end" trigger={<IconButton label={t("apps.rowActions.aria", { domain })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
        <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/apps/$domain", params: { domain } })}>
          {t("apps.rowActions.open")}
        </MenuItem>
        <MenuItem icon={<ScrollText />} onClick={() => void navigate({ to: "/apps/$domain/logs", params: { domain } })}>
          {t("apps.rowActions.logs")}
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<RotateCw />} disabled={!unit || restart.isPending} onClick={() => restart.mutate()}>
          {t("apps.rowActions.restart")}
        </MenuItem>
        <MenuItem icon={<CircleArrowUp />} disabled={update.isPending} onClick={() => update.mutate()}>
          {t("apps.rowActions.update")}
        </MenuItem>
        {move.available || promote.available || clone.available ? <MenuSeparator /> : null}
        {promote.available ? (
          <MenuItem icon={<ArrowRightLeft />} description={t("promotion.promote.menuDescription")} onClick={promote.open}>
            {t("promotion.promote.menu")}
          </MenuItem>
        ) : null}
        {clone.available ? (
          <MenuItem icon={<Copy />} description={t("promotion.clone.menuDescription")} onClick={clone.open}>
            {t("promotion.clone.menu")}
          </MenuItem>
        ) : null}
        {move.available ? (
          <MenuItem icon={<FolderInput />} description={t("teams.move.menuDescription")} onClick={move.open}>
            {t("teams.move.menu")}
          </MenuItem>
        ) : null}
      </Menu>
      {move.dialog}
      {promote.dialog}
      {clone.dialog}
      <NothingNewDialog
        domain={domain}
        refusal={nothingNew}
        pending={rebuildAnyway.isPending}
        onRebuild={() => rebuildAnyway.mutate()}
        onClose={dismissNothingNew}
      />
    </>
  );
}
