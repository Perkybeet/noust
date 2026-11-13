import { Check, ChevronsUpDown } from "lucide-react";
import { useState } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { ICONS } from "../ui/icons";
import { Menu, MenuItem } from "../ui/Menu";
import { Mono } from "../ui/Mono";
import { OBJECT_ICONS, POLICY_ICONS } from "../ui/objectIcons";
import { SM_UP, useMediaQuery } from "../ui/useMediaQuery";
import { SegmentedControl } from "./SegmentedControl";

/** What a caller that is not a person does with a call that changes something. */
export type PolicyValue = "alone" | "propose" | "never";
export type PolicyCaller = "token" | "assistant";
export type PolicyEnvironment = "production" | "other";
/** Why a cell offers fewer than the three values. */
export type PolicyReason = "assistant_floor" | "confirm" | "assistant_narrow" | "profile_floor";

export interface PolicyCellModel {
  caller: PolicyCaller;
  environment: PolicyEnvironment;
  /** What the cell says now, with whatever is being edited on top of what is saved. */
  value: PolicyValue;
  /** What this version of Noust does when nobody chose: "Factory: ..." is drawn only where it differs. */
  default: PolicyValue;
  /** The menu: only what the server offers is ever drawn. */
  offered: readonly PolicyValue[];
  /** There is one value only: a padlock for the assistant's floor, and no menu. */
  fixed: boolean;
  reason: PolicyReason | null;
  /** A token that was issued to act in sudo mode keeps "Does it" in a row that asks for confirmation: it is said, and not offered. */
  legacy: boolean;
}

export interface PolicyRowModel {
  /** The permission, as the server names it, in mono. */
  permission: string;
  /** What it lets one do, in this console's words. */
  label: string;
  /** The key: the row asks to confirm it is you, so "Does it" is never offered to the assistant. */
  key: boolean;
  /** A line under the area's table for a floor that applies to this row. */
  floor?: string | null;
  cells: readonly PolicyCellModel[];
}

const CALLERS: readonly PolicyCaller[] = ["token", "assistant"];
const ENVIRONMENTS: readonly PolicyEnvironment[] = ["production", "other"];

/** The value as a person reads it: the shape, then the word. Never colour. */
export function PolicyValueMark({ value }: { value: PolicyValue }) {
  const t = useT();
  const Icon = POLICY_ICONS[value];
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <Icon aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
      <span>{t(`callerPolicy.value.${value}`)}</span>
    </span>
  );
}

export interface PolicyCellProps {
  /** The row's own words, to name the cell for a screen reader ("Back up now, Token, Production: Does it"). */
  rowLabel: string;
  cell: PolicyCellModel;
  /** Absent, the cell is read-only. */
  onChange?: (value: PolicyValue) => void;
}

/**
 * One cell of the policy: who calls, where, and which of the three values it has. The assistant's floor is a padlock and
 * opens nothing; the factory's value is said only where it differs; a token that keeps "Does it" in a row that asks for
 * confirmation says so and is not offered it again. What the server does not offer is not in the menu: no value is drawn off.
 */
export function PolicyCell({ rowLabel, cell, onChange }: PolicyCellProps) {
  const t = useT();
  const context = { permission: rowLabel, caller: t(`callerPolicy.caller.${cell.caller}`), environment: t(`callerPolicy.environment.${cell.environment}`) };
  const named = t("callerPolicy.cell.labelWithValue", { ...context, value: t(`callerPolicy.value.${cell.value}`) });
  const floor = cell.fixed && cell.reason === "assistant_floor";
  const notes = (
    <>
      {cell.value !== cell.default ? <span className="text-12 text-fg-muted">{t("callerPolicy.cell.factory", { value: t(`callerPolicy.value.${cell.default}`).toLowerCase() })}</span> : null}
      {cell.legacy && cell.value === "alone" ? <span className="max-w-60 text-12 text-pretty text-fg-muted">{t("callerPolicy.cell.legacy")}</span> : null}
      {cell.fixed && !floor && cell.reason !== null ? <span className="max-w-60 text-12 text-pretty text-fg-muted">{t(`callerPolicy.cell.reason.${cell.reason}`)}</span> : null}
    </>
  );
  if (floor) {
    return (
      <span className="inline-flex items-center gap-2 px-2 py-1 text-13 text-fg-muted" title={t("callerPolicy.cell.reason.assistant_floor")}>
        <ICONS.locked aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
        <span>{t(`callerPolicy.value.${cell.value}`)}</span>
        <span className="sr-only">{`${t("callerPolicy.cell.label", context)}: ${t("callerPolicy.cell.fixed")}. ${t("callerPolicy.cell.reason.assistant_floor")}`}</span>
      </span>
    );
  }
  if (cell.fixed || onChange === undefined || cell.offered.length <= 1) {
    return (
      <div className="flex flex-col gap-0.5">
        <span className="inline-flex items-center px-2 py-1 text-13 text-fg" aria-label={named}>
          <PolicyValueMark value={cell.value} />
        </span>
        {notes}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-0.5">
      <Menu
        trigger={
          <button
            type="button"
            aria-label={named}
            className="group -mx-1 inline-flex min-h-8 w-fit cursor-pointer items-center gap-2 rounded-control px-2 py-1 text-left text-13 text-fg transition-colors duration-(--duration-press) hover:bg-bg-sunken"
          >
            <PolicyValueMark value={cell.value} />
            <ChevronsUpDown aria-hidden="true" className="size-icon-sm shrink-0 text-fg-faint" />
          </button>
        }
      >
        {cell.offered.map((value) => {
          const Icon = POLICY_ICONS[value];
          return (
            <MenuItem key={value} icon={<Icon />} description={t(`callerPolicy.valueLine.${value}`)} onClick={() => onChange(value)}>
              <span className="flex items-center justify-between gap-3">
                {t(`callerPolicy.value.${value}`)}
                {value === cell.value ? <Check aria-hidden="true" className="size-icon-sm text-fg-muted" /> : null}
              </span>
            </MenuItem>
          );
        })}
      </Menu>
      {notes}
    </div>
  );
}

function RowLabel({ row }: { row: PolicyRowModel }) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="flex min-w-0 items-start gap-1.5 text-13 text-pretty text-fg">
        {row.label}
        {row.key ? (
          <span className="mt-0.5 shrink-0" title={t("callerPolicy.cell.key")}>
            <ICONS.elevated aria-hidden="true" className="size-icon-xs text-fg-faint" />
            <span className="sr-only">{t("callerPolicy.cell.key")}</span>
          </span>
        ) : null}
      </span>
      <Mono tone="muted" className="text-12">
        {row.permission}
      </Mono>
    </div>
  );
}

const TH = "px-3 py-2 text-left text-12 font-semibold text-fg-muted";

function cellOf(row: PolicyRowModel, caller: PolicyCaller, environment: PolicyEnvironment): PolicyCellModel | undefined {
  return row.cells.find((cell) => cell.caller === caller && cell.environment === environment);
}

export interface PolicyMatrixProps {
  rows: readonly PolicyRowModel[];
  /** The table's accessible name: "Approval policy, Applications". */
  caption: string;
  /** Absent, the whole table is read-only. */
  onChange?: (row: PolicyRowModel, cell: PolicyCellModel, value: PolicyValue) => void;
}

function Table({ rows, caption, onChange }: PolicyMatrixProps) {
  const t = useT();
  const floors = [...new Set(rows.flatMap((row) => (row.floor !== undefined && row.floor !== null ? [row.floor] : [])))];
  return (
    <div className="flex flex-col">
      <div className="overflow-x-auto scroll-thin">
        <table aria-label={caption} className="w-full min-w-176 border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th rowSpan={2} scope="col" className={cx(TH, "w-1/4 align-bottom")}>
                {t("callerPolicy.matrix.permission")}
              </th>
              <th colSpan={2} scope="colgroup" className={cx(TH, "border-l border-border pb-0")}>
                <span className="inline-flex items-center gap-1.5">
                  <OBJECT_ICONS.token aria-hidden="true" className="size-icon-sm text-fg-faint" />
                  {t("callerPolicy.caller.token")}
                </span>
              </th>
              <th colSpan={2} scope="colgroup" className={cx(TH, "border-l border-border pb-0")}>
                <span className="inline-flex items-center gap-1.5">
                  <OBJECT_ICONS.assistant aria-hidden="true" className="size-icon-sm text-fg-faint" />
                  {t("callerPolicy.caller.assistant")}
                </span>
              </th>
            </tr>
            <tr className="border-b border-border">
              {CALLERS.flatMap((caller) =>
                ENVIRONMENTS.map((environment) => (
                  <th key={`${caller}.${environment}`} scope="col" className={cx(TH, "pt-1 font-normal", environment === "production" && "border-l border-border")}>
                    {t(`callerPolicy.environment.${environment}`)}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.permission} className="border-b border-border last:border-b-0">
                <th scope="row" className="px-3 py-2.5 text-left align-top font-normal">
                  <RowLabel row={row} />
                </th>
                {CALLERS.flatMap((caller) =>
                  ENVIRONMENTS.map((environment) => {
                    const cell = cellOf(row, caller, environment);
                    return (
                      <td key={`${caller}.${environment}`} className={cx("px-3 py-2 align-top", environment === "production" && "border-l border-border")}>
                        {cell !== undefined ? <PolicyCell rowLabel={row.label} cell={cell} {...(onChange !== undefined ? { onChange: (value: PolicyValue) => onChange(row, cell, value) } : {})} /> : null}
                      </td>
                    );
                  }),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {floors.length > 0 ? (
        <ul className="m-0 flex list-none flex-col gap-0.5 border-t border-border px-4 py-2.5 text-12 text-fg-muted">
          {floors.map((floor) => (
            <li key={floor} className="flex items-start gap-1.5">
              <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-xs shrink-0 text-fg-faint" />
              {floor}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** On a phone a table of four columns cannot be read: who calls and where are chosen once and each permission has one value. */
function List({ rows, caption, onChange }: PolicyMatrixProps) {
  const t = useT();
  const [caller, setCaller] = useState<PolicyCaller>("token");
  const [environment, setEnvironment] = useState<PolicyEnvironment>("production");
  return (
    <div className="flex flex-col gap-3 px-4 pb-4">
      <div className="flex flex-wrap items-center gap-3">
        <SegmentedControl
          label={t("callerPolicy.matrix.caller")}
          options={[
            { value: "token", label: t("callerPolicy.matrix.shortToken") },
            { value: "assistant", label: t("callerPolicy.matrix.shortAssistant") },
          ]}
          value={caller}
          onValueChange={setCaller}
        />
        <SegmentedControl
          label={t("callerPolicy.matrix.environment")}
          options={[
            { value: "production", label: t("callerPolicy.matrix.shortProduction") },
            { value: "other", label: t("callerPolicy.matrix.shortOther") },
          ]}
          value={environment}
          onValueChange={setEnvironment}
        />
      </div>
      <ul aria-label={caption} className="elev-seated m-0 flex list-none flex-col divide-y divide-border rounded-card border border-border bg-surface p-0">
        {rows.map((row) => {
          const cell = cellOf(row, caller, environment);
          return (
            <li key={row.permission} className="flex flex-col gap-2 px-4 py-3">
              <RowLabel row={row} />
              {cell !== undefined ? <PolicyCell rowLabel={row.label} cell={cell} {...(onChange !== undefined ? { onChange: (value: PolicyValue) => onChange(row, cell, value) } : {})} /> : null}
              {cell?.fixed === true && row.floor !== undefined && row.floor !== null ? <p className="text-12 text-fg-muted">{row.floor}</p> : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The policy of who calls as one table per area: a token and the assistant, each in production and in the other environments,
 * with three values per cell. From 640 px it is a table (the permission, then four cells); below it, a list with the caller
 * and the environment chosen once, because four columns cannot be read on a phone. It draws what the server offers and
 * decides nothing: a value that is not offered is not a menu entry, and the padlock opens nothing.
 */
export function PolicyMatrix(props: PolicyMatrixProps) {
  const wide = useMediaQuery(SM_UP);
  return wide ? <Table {...props} /> : <List {...props} />;
}
