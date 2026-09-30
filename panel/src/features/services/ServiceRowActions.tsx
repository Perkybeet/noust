import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { CirclePause, CirclePlay, PanelTop, RotateCw, ScrollText, ToggleLeft, ToggleRight } from "lucide-react";

import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import type { ServiceInfo } from "./data";
import { useServiceActions } from "./useServiceActions";

/**
 * The menu at the end of a service's row: open it, read its journal, or act on the unit
 * directly. A unit Noust did not create can be opened and read, never acted on - read-only is
 * enforced here, at the one place every row of every services table gets its actions from,
 * not left to each caller to remember.
 */
export function ServiceRowActions({ service }: { service: ServiceInfo }) {
  const t = useT();
  const navigate = useNavigate();
  const name = service.name;
  const { node } = useNode();
  const { start, stop, restart, enable, disable } = useServiceActions(name);
  const [confirmStop, setConfirmStop] = useState(false);

  return (
    <>
    <Menu align="end" trigger={<IconButton label={t("services.actions.actionsFor", { name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/server/services/$name", params: { name } })}>
        {t("services.actions.open")}
      </MenuItem>
      <MenuItem icon={<ScrollText />} onClick={() => void navigate({ to: "/server/logs", search: { unit: name } })}>
        {t("services.actions.viewLogs")}
      </MenuItem>
      {service.managed ? (
        <>
          <MenuSeparator />
          {service.active ? (
            <MenuItem icon={<CirclePause />} disabled={stop.isPending} onClick={() => setConfirmStop(true)}>
              {t("services.actions.stop")}
            </MenuItem>
          ) : (
            <MenuItem icon={<CirclePlay />} disabled={start.isPending} onClick={() => start.mutate()}>
              {t("services.actions.start")}
            </MenuItem>
          )}
          <MenuItem icon={<RotateCw />} disabled={restart.isPending} onClick={() => restart.mutate()}>
            {t("services.actions.restart")}
          </MenuItem>
          <MenuSeparator />
          {service.enabled ? (
            <MenuItem icon={<ToggleLeft />} disabled={disable.isPending} onClick={() => disable.mutate()}>
              {t("services.actions.disableAtBoot")}
            </MenuItem>
          ) : (
            <MenuItem icon={<ToggleRight />} disabled={enable.isPending} onClick={() => enable.mutate()}>
              {t("services.actions.enableAtBoot")}
            </MenuItem>
          )}
        </>
      ) : null}
    </Menu>
    <ConfirmDialog
      open={confirmStop}
      onOpenChange={setConfirmStop}
      friction="simple"
      server={node}
      title={t("services.detail.stopTitle", { name })}
      description={t("services.detail.stopDescription")}
      actionLabel={t("services.detail.stopService")}
      // The outcome is the row's state, or the action's own error toast (useServiceActions).
      onConfirm={() => {
        stop.mutate();
        return Promise.resolve();
      }}
    />
    </>
  );
}
