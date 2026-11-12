import { useNavigate } from "@tanstack/react-router";

import { KeyValueList } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { Drawer } from "../../../components/ui/Drawer";
import { MenuItem, MenuSeparator } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import type { Cell } from "./data";
import { FaceMark, whyText } from "./FaceMark";
import { faceOf, isWaiting } from "./faces";
import { kindWord } from "./ShareDialog";
import { ServersTable } from "./ServersTable";
import type { ServerLine } from "./ServersTable";
import { useValueText } from "./valueText";
import type { FleetRow } from "./useFleetKind";

/** What the panel can do with a setting on a server: each one opens a dialog of the section that holds the panel. */
export interface PanelActions {
  edit: () => void;
  /** A value for this server alone: new, or changing the one it has. */
  own: (node: string) => void;
  removeOwn: (node: string) => void;
  /** Put the fleet's value back where the server's operator changed it. */
  putBack: (node: string) => void;
  /** Apply what is waiting, to this server or (without one) to every one that has something waiting. */
  apply: (node?: string) => void;
}

/** The menu of a server's row: what can be done with this setting on this server. */
function ServerMenu({ row, cell, canManage, actions }: { row: FleetRow; cell: Cell; canManage: boolean; actions: PanelActions }) {
  const t = useT();
  const navigate = useNavigate();
  const face = faceOf(cell);
  const own = row.exceptions.some((exception) => exception.node === cell.node);
  const canOwn = canManage && (face === "follows" || face === "exception" || face === "pending");
  const waiting = isWaiting(cell) && (face === "pending" || face === "exception" || face === "off");
  return (
    <RowMenu name={cell.node}>
      {canOwn ? <MenuItem onClick={() => actions.own(cell.node)}>{own ? t("fleetSettings.panel.changeOwn") : t("fleetSettings.panel.giveOwn")}</MenuItem> : null}
      {canManage && own ? <MenuItem onClick={() => actions.removeOwn(cell.node)}>{t("fleetSettings.panel.removeOwn")}</MenuItem> : null}
      {canManage && face === "changedHere" ? (
        <MenuItem description={t("fleetSettings.panel.putBackHelp")} onClick={() => actions.putBack(cell.node)}>
          {t("fleetSettings.panel.putBack")}
        </MenuItem>
      ) : null}
      {canManage && waiting ? (
        <MenuItem disabled={face === "off"} {...(face === "off" ? { description: t("fleetSettings.panel.applyNowOff") } : {})} onClick={() => actions.apply(cell.node)}>
          {t("fleetSettings.panel.applyNow")}
        </MenuItem>
      ) : null}
      <MenuSeparator />
      <MenuItem
        onClick={() => {
          void navigate({ to: "/server/central", search: { node: cell.node } });
        }}
      >
        {t("fleetSettings.panel.openServer", { server: cell.node })}
      </MenuItem>
    </RowMenu>
  );
}

/**
 * The panel of one setting: the value the fleet holds, which is the central's, and the table of servers, a row each,
 * with what it holds, how it stands and why. A value for one server alone is made and unmade from its row, here and
 * not on a page of its own.
 */
export function SettingPanel({
  row,
  resource,
  valueLabel,
  canManage,
  actions,
  onClose,
}: {
  row: FleetRow;
  resource: string;
  valueLabel: string;
  canManage: boolean;
  actions: PanelActions;
  onClose: () => void;
}) {
  const t = useT();
  const valueText = useValueText(resource);
  const kindTitle = kindWord(t, resource, resource);
  const value = row.fleet !== null ? valueText(row.fleet.value) : "";
  const lines: ServerLine[] = row.status.servers.map((cell) => {
    const mine = row.exceptions.find((exception) => exception.node === cell.node);
    const face = faceOf(cell);
    const own = mine !== undefined ? valueText(mine.value) : "";
    return {
      name: cell.node,
      value:
        own !== "" ? (
          <span>{own}</span>
        ) : face === "follows" || face === "pending" || face === "off" ? (
          <span className="text-fg-muted">{t("fleetSettings.panel.fleetsOwn")}</span>
        ) : (
          <span className="text-fg-faint">—</span>
        ),
      state: <FaceMark face={face} />,
      note: whyText(t, cell),
      menu: <ServerMenu row={row} cell={cell} canManage={canManage} actions={actions} />,
    };
  });
  return (
    <Drawer
      open
      size="lg"
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={<Mono tone="default">{row.name}</Mono>}
      description={kindTitle}
    >
      <div className="flex flex-col gap-5">
        <SurfaceSection
          title={t("fleetSettings.panel.fleetValue")}
          {...(canManage
            ? {
                actions: (
                  <Button size="sm" variant="secondary" onClick={actions.edit}>
                    {t("common.words.edit")}
                  </Button>
                ),
              }
            : {})}
        >
          <KeyValueList
            items={[
              { label: valueLabel, value, mono: false, copy: false },
              {
                label: t("fleetSettings.panel.lastChange"),
                value: row.status.changed_by !== "" ? (
                  <span>
                    {row.status.changed_by}, <RelativeTime value={row.status.changed_at} />
                  </span>
                ) : null,
                mono: false,
                copy: false,
              },
            ]}
          />
        </SurfaceSection>
        <SurfaceSection title={t("fleetSettings.panel.servers")} description={t("fleetSettings.panel.serversHelp")} flush>
          <ServersTable lines={lines} caption={t("fleetSettings.panel.caption", { name: row.name })} valueHeader={valueLabel} />
        </SurfaceSection>
      </div>
    </Drawer>
  );
}
