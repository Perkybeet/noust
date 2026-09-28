import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import type { DatabaseUser } from "../../api/queries/databases";
import { databasesQuery, enginePrivilegesQuery } from "../../api/queries/databases";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { engineLabel } from "./data";
import { useDatabaseActions } from "./useDatabaseActions";

export type GrantMode = "grant" | "revoke";

export interface GrantDialogProps {
  mode: GrantMode;
  user: DatabaseUser;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Grants or revokes privileges on one database for one user. The privilege list comes from
 * `GET /api/databases/engines/{engine}/privileges` - the manager's own whitelist, not a copy of
 * it kept here: leaving every one unchecked grants or revokes the engine's own default (usually
 * every privilege); the server refuses anything not on its own whitelist and says so verbatim.
 */
export function GrantDialog({ mode, user, open, onOpenChange }: GrantDialogProps) {
  const t = useT();
  const formId = useId();
  const databases = useQuery({ ...databasesQuery(user.engine), enabled: open });
  const privileges = useQuery({ ...enginePrivilegesQuery(user.engine), enabled: open });
  const [database, setDatabase] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [host, setHost] = useState(user.host);
  const { grant, revoke } = useDatabaseActions();
  const action = mode === "grant" ? grant : revoke;

  const close = (next: boolean): void => {
    if (!next && action.isPending) return;
    onOpenChange(next);
    if (!next) {
      setDatabase("");
      setSelected([]);
      action.reset();
    }
  };

  const toggle = (privilege: string, checked: boolean): void => {
    setSelected((current) => (checked ? [...current, privilege] : current.filter((value) => value !== privilege)));
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (database === "") return;
    action.mutate(
      { engine: user.engine, username: user.username, database, host, privileges: selected },
      { onSuccess: () => close(false) },
    );
  };

  const options = databases.data?.databases.map((item) => ({ value: item.name, label: item.name })) ?? [];

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={
        mode === "grant"
          ? t("databases.grantDialog.grantTitle", { username: user.username })
          : t("databases.grantDialog.revokeTitle", { username: user.username })
      }
      description={mode === "grant" ? t("databases.grantDialog.grantDescription") : t("databases.grantDialog.revokeDescription")}
      footer={
        <>
          <Button disabled={action.isPending} onClick={() => close(false)}>
            {t("databases.cancel")}
          </Button>
          <Button
            type="submit"
            form={formId}
            variant={mode === "revoke" ? "danger" : "primary"}
            loading={action.isPending}
            disabled={database === ""}
          >
            {mode === "grant" ? t("databases.grantDialog.grant") : t("databases.grantDialog.revoke")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("databases.fields.database")} nativeLabel={false}>
          <Select
            aria-label={t("databases.fields.database")}
            value={database}
            onValueChange={setDatabase}
            placeholder={databases.isPending ? t("databases.grantDialog.loadingDatabases") : t("databases.grantDialog.chooseDatabase")}
            options={options}
            disabled={databases.isPending || options.length === 0}
          />
        </Field>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-0.5 text-13 font-medium text-fg">{t("databases.fields.privileges")}</legend>
          <p className="text-12 text-fg-muted">
            {t("databases.grantDialog.defaultHint", { engine: engineLabel(user.engine) })}
          </p>
          <QueryState
            query={privileges}
            label="privileges"
            skeleton={
              <div aria-hidden="true" className="grid grid-cols-2 gap-x-4 gap-y-2">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-4 w-28" />
                ))}
              </div>
            }
            isEmpty={(data) => data.privileges.length === 0}
            empty={
              <p className="text-13 text-fg-muted">
                {t("databases.grantDialog.noDiscretePrivileges", { engine: engineLabel(user.engine) })}
              </p>
            }
          >
            {(data) => (
              <div className="grid max-h-48 grid-cols-2 gap-x-4 gap-y-2 overflow-y-auto pr-1 scroll-thin">
                {data.privileges.map((privilege) => (
                  <Checkbox
                    key={privilege}
                    checked={selected.includes(privilege)}
                    onCheckedChange={(checked) => {
                      toggle(privilege, checked);
                    }}
                    label={<span className="mono">{privilege}</span>}
                  />
                ))}
              </div>
            )}
          </QueryState>
        </fieldset>
        <Field label={t("databases.fields.host")}>
          <Input mono value={host} onValueChange={setHost} autoComplete="off" spellCheck={false} />
        </Field>
        {action.isError ? (
          <ErrorBlock
            live
            compact
            error={action.error}
            title={mode === "grant" ? t("databases.grantDialog.grantFailed") : t("databases.grantDialog.revokeFailed")}
          />
        ) : null}
      </form>
    </Dialog>
  );
}
