import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { IconButton } from "../../components/ui/IconButton";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { pathField } from "./wizard";
import type { PathRow, ReviewErrors } from "./wizard";

export interface PersistentPathsFieldProps {
  rows: PathRow[];
  errors: ReviewErrors;
  onChange: (rows: PathRow[]) => void;
}

let added = 0;

/**
 * The folders kept between deploys: linked into every new version from `shared/`, for uploads
 * or anything else a build must not throw away. Empty is the common case - most
 * apps keep no state on disk - so the list starts with nothing and the operator adds what
 * their app needs.
 */
export function PersistentPathsField({ rows, errors, onChange }: PersistentPathsFieldProps) {
  const t = useT();
  const update = (id: string, value: string): void => {
    onChange(rows.map((row) => (row.id === id ? { ...row, value } : row)));
  };
  const add = (): void => {
    added += 1;
    onChange([...rows, { id: `path:${String(added)}`, value: "" }]);
  };

  return (
    <div className="flex flex-col gap-3">
      {/* The label above says what these are; with none, the Add button is the whole story. */}
      {rows.length === 0 ? null : (
        rows.map((row) => (
          <Field
            key={row.id}
            label={t("newApp.paths.path")}
            error={errors[pathField(row)]}
            action={
              <IconButton
                label={row.value.trim() === "" ? t("newApp.paths.removeEmpty") : t("newApp.paths.remove", { path: row.value.trim() })}
                icon={<ICONS.delete />}
                onClick={() => onChange(rows.filter((other) => other.id !== row.id))}
              />
            }
          >
            <Input
              mono
              value={row.value}
              onValueChange={(value: string) => update(row.id, value)}
              placeholder="storage"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </Field>
        ))
      )}
      <div>
        <Button size="sm" icon={<ICONS.add aria-hidden="true" />} onClick={add}>
          {t("newApp.paths.add")}
        </Button>
      </div>
    </div>
  );
}
