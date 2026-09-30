import { useState } from "react";
import type { SyntheticEvent } from "react";

import type { RelationColumn } from "../../../api/queries/databases";
import { Button } from "../../../components/ui/Button";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Popover } from "../../../components/ui/Popover";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import type { PlainKey } from "../../../i18n";
import { OPERATORS, UNARY } from "./search";
import type { Filter, Operator } from "./search";

const OPERATOR_WORDS: Readonly<Record<Operator, PlainKey>> = {
  eq: "databases.data.ops.eq",
  neq: "databases.data.ops.neq",
  lt: "databases.data.ops.lt",
  lte: "databases.data.ops.lte",
  gt: "databases.data.ops.gt",
  gte: "databases.data.ops.gte",
  like: "databases.data.ops.like",
  ilike: "databases.data.ops.ilike",
  in: "databases.data.ops.in",
  null: "databases.data.ops.null",
  notnull: "databases.data.ops.notnull",
};

/** The symbol a filter chip shows for its operator: short, universal, not translated. */
const OPERATOR_SIGNS: Readonly<Record<Operator, string>> = {
  eq: "=",
  neq: "≠",
  lt: "<",
  lte: "≤",
  gt: ">",
  gte: "≥",
  like: "LIKE",
  ilike: "ILIKE",
  in: "IN",
  null: "IS NULL",
  notnull: "IS NOT NULL",
};

/** The operators that make sense for a column of this kind. */
function operatorsFor(kind: string | undefined): readonly Operator[] {
  if (kind === "numeric" || kind === "datetime") return OPERATORS.filter((op) => op !== "like" && op !== "ilike");
  if (kind === "boolean") return ["eq", "neq", "null", "notnull"];
  if (kind === "binary") return ["null", "notnull"];
  return OPERATORS;
}

export interface FilterRowProps {
  columns: readonly RelationColumn[];
  filters: readonly Filter[];
  onChange: (filters: Filter[]) => void;
}

/**
 * The table's filters as chips (`status = paid`), each removable, and "Add filter": a column,
 * an operator fit for its type, and a value. The server applies them; the URL keeps them.
 */
export function FilterRow({ columns, filters, onChange }: FilterRowProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [column, setColumn] = useState<string | null>(null);
  const [op, setOp] = useState<Operator>("eq");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const chosen = columns.find((item) => item.name === column) ?? columns[0];
  const allowed = operatorsFor(chosen?.kind);
  const unary = UNARY.has(op);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    event.stopPropagation();
    if (chosen === undefined) return;
    if (!unary && value === "") {
      setError(t("databases.data.valueRequired"));
      return;
    }
    onChange([...filters, { column: chosen.name, op, value: unary ? "" : value }]);
    setOpen(false);
    setValue("");
    setError(null);
  };

  const Add = ICONS.add;
  const Close = ICONS.dismiss;
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {filters.map((filter, index) => (
        <span key={`${filter.column}:${filter.op}:${filter.value}:${String(index)}`} className="inline-flex h-control-sm items-center gap-1 rounded-control border border-border bg-surface pr-0.5 pl-2 text-12">
          <Mono>{filter.column}</Mono>
          <span className="text-fg-muted">{OPERATOR_SIGNS[filter.op]}</span>
          {UNARY.has(filter.op) ? null : <Mono tone="default">{filter.value}</Mono>}
          <IconButton
            size="sm"
            label={t("databases.data.removeFilter", { column: filter.column })}
            icon={<Close />}
            onClick={() => onChange(filters.filter((_, at) => at !== index))}
          />
        </span>
      ))}
      <Popover
        open={open}
        onOpenChange={setOpen}
        align="start"
        title={t("databases.data.addFilterTitle")}
        trigger={
          <Button size="sm" variant="ghost" icon={<Add aria-hidden="true" />}>
            {t("databases.data.addFilter")}
          </Button>
        }
      >
        <form noValidate onSubmit={submit} className="mt-3 flex flex-col gap-3">
          <Field label={t("databases.data.column")} nativeLabel={false}>
            <Select
              size="sm"
              mono
              value={chosen?.name ?? null}
              onValueChange={(next) => {
                setColumn(next);
                const kind = columns.find((item) => item.name === next)?.kind;
                if (!operatorsFor(kind).includes(op)) setOp(operatorsFor(kind)[0] ?? "eq");
              }}
              options={columns.map((item) => ({ value: item.name, label: item.name, hint: item.type }))}
            />
          </Field>
          <Field label={t("databases.data.operator")} nativeLabel={false}>
            <Select size="sm" value={op} onValueChange={(next) => setOp(next)} options={allowed.map((item) => ({ value: item, label: t(OPERATOR_WORDS[item]) }))} />
          </Field>
          {unary ? null : (
            <Field label={t("databases.data.value")} description={op === "in" ? t("databases.data.inHelp") : op === "like" || op === "ilike" ? t("databases.data.likeHelp") : undefined} error={error}>
              <Input mono size="sm" value={value} onValueChange={(next: string) => setValue(next)} autoComplete="off" spellCheck={false} />
            </Field>
          )}
          <div className="flex justify-end">
            <Button type="submit" size="sm">
              {t("databases.data.applyFilter")}
            </Button>
          </div>
        </form>
      </Popover>
      {filters.length > 0 ? (
        <Button size="sm" variant="ghost" onClick={() => onChange([])}>
          {t("databases.data.clearFilters")}
        </Button>
      ) : null}
    </div>
  );
}
