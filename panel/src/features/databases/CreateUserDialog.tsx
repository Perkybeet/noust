import { TriangleAlert } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { ReactElement, SyntheticEvent } from "react";

import type { Engine } from "../../api/queries/databases";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyButton } from "../../components/ui/CopyButton";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { engineLabel } from "./data";
import { useDatabaseActions } from "./useDatabaseActions";

export interface CreateUserDialogProps {
  engines: readonly Engine[];
  trigger: ReactElement<Record<string, unknown>>;
}

/**
 * Creates a database user. The password the engine assigns comes back exactly once, in the
 * response of this one call - Noust stores only what the engine stores, a hash, so there is
 * nowhere to read it back from afterwards. The dialog holds the created user until the
 * operator closes it on purpose, rather than a toast that could be missed.
 */
export function CreateUserDialog({ engines, trigger }: CreateUserDialogProps) {
  const t = useT();
  const formId = useId();
  const usernameRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  // See CreateDatabaseDialog: derived live so the default survives `engines` arriving after
  // this dialog has already mounted, instead of freezing on whatever it was at mount.
  const [chosenEngine, setEngine] = useState("");
  const engine = engines.some((item) => item.name === chosenEngine) ? chosenEngine : (engines[0]?.name ?? "");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [host, setHost] = useState("localhost");
  const { createUser } = useDatabaseActions();

  const reset = (): void => {
    setUsername("");
    setPassword("");
    setHost("localhost");
    createUser.reset();
  };

  const close = (next: boolean): void => {
    if (!next && createUser.isPending) return;
    setOpen(next);
    if (!next) reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (username.trim() === "" || engine === "") return;
    createUser.mutate({ engine, username: username.trim(), password: password.trim() || undefined, host: host.trim() });
  };

  const created = createUser.data;

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      trigger={trigger}
      initialFocus={usernameRef}
      title={created ? t("databases.createUserDialog.created", { username: created.username }) : t("databases.createUserDialog.title")}
      description={created ? undefined : t("databases.createUserDialog.description")}
      footer={
        created ? (
          <Button
            variant="primary"
            onClick={() => {
              close(false);
            }}
          >
            {t("databases.createUserDialog.done")}
          </Button>
        ) : (
          <>
            <Button disabled={createUser.isPending} onClick={() => close(false)}>
              {t("databases.cancel")}
            </Button>
            <Button
              type="submit"
              form={formId}
              variant="primary"
              loading={createUser.isPending}
              disabled={username.trim() === ""}
            >
              {t("databases.createUserDialog.title")}
            </Button>
          </>
        )
      }
    >
      {created ? (
        <div className="flex flex-col gap-4">
          <div className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn-soft px-3 py-2.5">
            <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
            <p className="text-13 text-pretty text-fg">{t("databases.createUserDialog.passwordWarning")}</p>
          </div>
          <Field label={t("databases.fields.username")}>
            <Input
              readOnly
              mono
              value={created.username}
              suffix={<CopyButton value={created.username} label={t("databases.createUserDialog.copyUsername")} />}
            />
          </Field>
          <Field label={t("databases.fields.password")}>
            <Input
              readOnly
              mono
              value={created.password}
              suffix={<CopyButton value={created.password} label={t("databases.createUserDialog.copyPassword")} />}
            />
          </Field>
        </div>
      ) : (
        <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
          <Field label={t("databases.fields.engine")} nativeLabel={false}>
            <Select
              aria-label={t("databases.fields.engine")}
              value={engine}
              onValueChange={setEngine}
              options={engines.map((item) => ({ value: item.name, label: engineLabel(item.name) }))}
            />
          </Field>
          <Field label={t("databases.fields.username")}>
            <Input ref={usernameRef} mono value={username} onValueChange={setUsername} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label={t("databases.fields.password")} optional description={t("databases.createUserDialog.passwordDescription")}>
            <Input mono type="password" value={password} onValueChange={setPassword} autoComplete="off" />
          </Field>
          {engine !== "redis" ? (
            <Field label={t("databases.fields.host")} description={t("databases.createUserDialog.hostDescription")}>
              <Input mono value={host} onValueChange={setHost} autoComplete="off" spellCheck={false} />
            </Field>
          ) : null}
          {createUser.isError ? (
            <ErrorBlock live compact error={createUser.error} title={t("databases.createUserDialog.createFailed")} />
          ) : null}
        </form>
      )}
    </Dialog>
  );
}
