import { useNavigate } from "@tanstack/react-router";
import { CirclePause, CirclePlay, MoreHorizontal, PanelTop, RotateCw, ToggleLeft, ToggleRight } from "lucide-react";

import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { useT } from "../../i18n";
import type { ServiceInfo } from "./data";
import { useServiceActions } from "./useServiceActions";

/**
 * The menu at the end of a service's row: open it, or act on the unit directly. A unit WASM
 * did not create has no menu at all - read-only is enforced here, at the one place every row
 * of every services table gets its actions from, not left to each caller to remember.
 */
export function ServiceRowActions({ service }: { service: ServiceInfo }) {
  const t = useT();
  const navigate = useNavigate();
  const name = service.name;
  const { start, stop, restart, enable, disable } = useServiceActions(name);

  if (!service.managed) return null;

  return (
    <Menu
      align="end"
      trigger={<IconButton label={t("services.actions.actionsFor", { name })} icon={<MoreHorizontal />} size="sm" tooltip={false} />}
    >
      <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/services/$name", params: { name } })}>
        {t("services.actions.open")}
      </MenuItem>
      <MenuSeparator />
      {service.active ? (
        <MenuItem icon={<CirclePause />} disabled={stop.isPending} onClick={() => stop.mutate()}>
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
    </Menu>
  );
}
