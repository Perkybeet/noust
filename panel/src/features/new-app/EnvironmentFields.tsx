import { ChevronDown, Eye, EyeOff, KeyRound } from "lucide-react";
import { useState } from "react";

import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { generateSecret } from "./secrets";
import { envField, envNameField } from "./wizard";
import type { EnvRow, ReviewErrors } from "./wizard";

function Label({ row }: { row: EnvRow }) {
  const t = useT();
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <Mono>{row.name}</Mono>
      {row.secret ? <span className="text-12 font-normal text-fg-muted">{t("newApp.env.secret")}</span> : null}
    </span>
  );
}

/** Where a declared variable's value came from, when that is worth saying. */
function origin(t: T, row: EnvRow): string | undefined {
  if (row.proposed !== undefined) {
    if (row.proposed.generated) return t("newApp.env.proposalGenerated");
    if (row.example !== null && row.example !== "") return t("newApp.env.proposalDefault");
    return row.secret ? t("newApp.env.pasteOrGenerate") : undefined;
  }
  if (row.example !== null && row.example !== "") return t(row.secret ? "newApp.env.exampleSecret" : "newApp.env.exampleDefault");
  return row.secret ? t("newApp.env.pasteOrGenerate") : undefined;
}

/**
 * Whether a declared variable asks for the operator's attention: it has no value to fall back
 * on, or its default is an example secret, which is public. These come first, in view; the rest
 * already have a value and are folded.
 */
export function needsValue(row: EnvRow): boolean {
  if (row.proposed?.generated === true) return false;
  return row.required || (row.secret && row.example !== null && row.example !== "");
}

function DeclaredRow({ row, error, onChange }: { row: EnvRow; error: string | undefined; onChange: (value: string) => void }) {
  const t = useT();
  const [shown, setShown] = useState(false);
  const said = origin(t, row);
  // The platform's own note on where the value came from there, verbatim.
  const note = row.proposed?.note ?? null;
  const description =
    note !== null ? (
      <>
        {said !== undefined ? `${said} ` : null}
        <Mono>{note}</Mono>
      </>
    ) : (
      said
    );
  return (
    <Field
      label={<Label row={row} />}
      optional={!row.required}
      error={error}
      {...(description !== undefined ? { description } : {})}
      {...(row.secret
        ? {
            action: (
              <Button icon={<KeyRound aria-hidden="true" />} aria-label={t("newApp.env.generateNamed", { name: row.name })} onClick={() => onChange(generateSecret())}>
                <span className="hidden sm:inline">{t("newApp.env.generate")}</span>
              </Button>
            ),
          }
        : {})}
    >
      <Input
        mono
        type={row.secret && !shown ? "password" : "text"}
        value={row.value}
        onValueChange={(value: string) => onChange(value)}
        autoComplete={row.secret ? "new-password" : "off"}
        autoCapitalize="off"
        spellCheck={false}
        {...(row.secret
          ? {
              suffix: (
                <IconButton
                  label={t(shown ? "newApp.env.hide" : "newApp.env.show", { name: row.name })}
                  icon={shown ? <EyeOff /> : <Eye />}
                  size="sm"
                  pressed={shown}
                  onClick={() => setShown(!shown)}
                />
              ),
            }
          : {})}
      />
    </Field>
  );
}

function AddedRow({
  row,
  nameError,
  valueError,
  onChange,
  onRemove,
}: {
  row: EnvRow;
  nameError: string | undefined;
  valueError: string | undefined;
  onChange: (patch: Partial<EnvRow>) => void;
  onRemove: () => void;
}) {
  const t = useT();
  return (
    <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start">
      <Field label={t("newApp.env.name")} error={nameError} className="sm:w-56 sm:shrink-0">
        <Input
          mono
          value={row.name}
          onValueChange={(value: string) => onChange({ name: value })}
          placeholder="API_URL"
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
        />
      </Field>
      <Field
        label={t("newApp.env.value")}
        error={valueError}
        className="min-w-0 flex-1"
        action={
          <IconButton
            label={row.name.trim() === "" ? t("newApp.env.removeUnnamed") : t("newApp.env.remove", { name: row.name.trim() })}
            icon={<ICONS.delete />}
            onClick={onRemove}
          />
        }
      >
        <Input mono value={row.value} onValueChange={(value: string) => onChange({ value })} autoComplete="off" spellCheck={false} />
      </Field>
    </div>
  );
}

export interface EnvironmentFieldsProps {
  rows: EnvRow[];
  errors: ReviewErrors;
  onChange: (rows: EnvRow[]) => void;
}

let added = 0;

/**
 * The app's environment, generated from `.env.example`: the variables that need a value first
 * (no default, or an example secret that is public), each with a generator beside a secret;
 * the ones that already have a value folded under one line, open when one of them needs a fix.
 * More can be added; everything can be changed later from the app's Environment tab.
 */
export function EnvironmentFields({ rows, errors, onChange }: EnvironmentFieldsProps) {
  const t = useT();
  const declared = rows.filter((row) => row.declared);
  const first = declared.filter(needsValue);
  const folded = declared.filter((row) => !needsValue(row));
  const extra = rows.filter((row) => !row.declared);
  const foldedErrors = folded.some((row) => errors[envField(row)] !== undefined || errors[envNameField(row)] !== undefined);
  const update = (id: string, patch: Partial<EnvRow>): void => {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  };
  const add = (): void => {
    added += 1;
    onChange([...rows, { id: `added:${String(added)}`, name: "", value: "", secret: false, required: false, declared: false, example: null }]);
  };

  return (
    <div className="flex flex-col gap-5">
      {declared.length === 0 ? <p className="text-13 text-fg-muted">{t("newApp.env.noExample")}</p> : null}
      {first.map((row) => (
        <DeclaredRow key={row.id} row={row} error={errors[envField(row)]} onChange={(value) => update(row.id, { value })} />
      ))}
      {folded.length > 0 ? (
        <details open={foldedErrors || first.length === 0} className="group overflow-hidden rounded-card border border-border">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 -outline-offset-2 hover:bg-surface-hover [&::-webkit-details-marker]:hidden">
            <span className="text-13 font-medium text-fg">{t("newApp.env.withValues", { count: folded.length })}</span>
            <ChevronDown aria-hidden="true" className="size-icon-sm text-fg-muted transition-transform duration-(--duration-fast) group-open:rotate-180" />
          </summary>
          <div className="flex flex-col gap-5 border-t border-border px-4 py-4">
            {folded.map((row) => (
              <DeclaredRow key={row.id} row={row} error={errors[envField(row)]} onChange={(value) => update(row.id, { value })} />
            ))}
          </div>
        </details>
      ) : null}
      {extra.length > 0 ? (
        <div className="flex flex-col gap-4 border-t border-border pt-5">
          {extra.map((row) => (
            <AddedRow
              key={row.id}
              row={row}
              nameError={errors[envNameField(row)]}
              valueError={errors[envField(row)]}
              onChange={(patch) => update(row.id, patch)}
              onRemove={() => onChange(rows.filter((other) => other.id !== row.id))}
            />
          ))}
        </div>
      ) : null}
      <div>
        <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={add}>
          {t("newApp.env.add")}
        </Button>
      </div>
    </div>
  );
}
