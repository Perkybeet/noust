import { Eye, EyeOff, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { generateSecret } from "./secrets";
import { envField, envNameField } from "./wizard";
import type { EnvRow, ReviewErrors } from "./wizard";

function Label({ row }: { row: EnvRow }) {
  const t = useT();
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
      <code translate="no" className="text-12 font-medium text-fg">
        {row.name}
      </code>
      {row.required ? <span className="text-12 font-medium text-fg-muted">{t("newApp.env.required")}</span> : null}
      {row.secret ? <span className="text-12 font-normal text-fg-faint">{t("newApp.env.secret")}</span> : null}
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
        <span translate="no" className="mono">
          {note}
        </span>
      </>
    ) : (
      said
    );
  return (
    <div className={row.secret ? "grid grid-cols-[minmax(0,1fr)_auto] items-start gap-2" : undefined}>
      <Field label={<Label row={row} />} error={error} {...(description !== undefined ? { description } : {})}>
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
      {row.secret ? (
        <Button
          size="md"
          icon={<KeyRound aria-hidden="true" />}
          aria-label={t("newApp.env.generateNamed", { name: row.name })}
          onClick={() => onChange(generateSecret())}
          className="mt-[1.625rem]"
        >
          <span className="hidden sm:inline">{t("newApp.env.generate")}</span>
        </Button>
      ) : null}
    </div>
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
    <div className="grid gap-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_auto] sm:items-start">
      <Field label={t("newApp.env.name")} error={nameError}>
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
      <Field label={t("newApp.env.value")} error={valueError}>
        <Input mono value={row.value} onValueChange={(value: string) => onChange({ value })} autoComplete="off" spellCheck={false} />
      </Field>
      <IconButton
        label={row.name.trim() === "" ? t("newApp.env.removeUnnamed") : t("newApp.env.remove", { name: row.name.trim() })}
        icon={<Trash2 />}
        onClick={onRemove}
        className="sm:mt-[1.625rem]"
      />
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
 * The app's environment, generated from `.env.example`: one field per declared variable, its
 * default filled in, credentials masked with a generator beside them, the ones without a default
 * marked. More variables can be added; everything can be changed later from the app's
 * Environment tab.
 */
export function EnvironmentFields({ rows, errors, onChange }: EnvironmentFieldsProps) {
  const t = useT();
  const declared = rows.filter((row) => row.declared);
  const extra = rows.filter((row) => !row.declared);
  const update = (id: string, patch: Partial<EnvRow>): void => {
    onChange(rows.map((row) => (row.id === id ? { ...row, ...patch } : row)));
  };
  const add = (): void => {
    added += 1;
    onChange([
      ...rows,
      { id: `added:${String(added)}`, name: "", value: "", secret: false, required: false, declared: false, example: null },
    ]);
  };

  return (
    <div className="flex flex-col gap-4">
      {declared.length === 0 ? (
        <p className="text-13 text-fg-muted">{t("newApp.env.noExample")}</p>
      ) : (
        <div className="flex flex-col gap-4">
          {declared.map((row) => (
            <DeclaredRow key={row.id} row={row} error={errors[envField(row)]} onChange={(value) => update(row.id, { value })} />
          ))}
        </div>
      )}
      {extra.length > 0 ? (
        <div className="flex flex-col gap-3 border-t border-border pt-4">
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
        <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={add}>
          {t("newApp.env.add")}
        </Button>
      </div>
    </div>
  );
}
