import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { databaseKeys, enginesQuery } from "../../api/queries/databases";
import type { Engine } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { can, sortEngines } from "./engines";

/** Engines a database can be created on: running, and holding named databases (not Redis's slots). */
export function creatableEngines(engines: readonly Engine[]): Engine[] {
  return sortEngines(engines.filter((engine) => engine.running && (can(engine, "sql") || can(engine, "documents"))));
}

const NO_APP = "";
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export interface CreateDatabaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Preselects the engine, as the Engines tab's row does. */
  engine?: string;
}

/**
 * "New database": an engine, a name, and optionally the application that uses it. For an
 * application it becomes "Create and link": Noust makes an account for it, writes the
 * connection string into its environment and restarts it behind its startup check, as a job
 * the application's Database tab follows.
 */
export function CreateDatabaseDialog({ open, onOpenChange, engine: preset }: CreateDatabaseDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const engines = useQuery({ ...enginesQuery(), enabled: open });
  const apps = useQuery({ ...appsQuery(), enabled: open });
  const choices = creatableEngines(engines.data?.engines ?? []);

  const [engine, setEngine] = useState<string | null>(preset ?? null);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [app, setApp] = useState(NO_APP);
  const [errors, setErrors] = useState<Partial<Record<"engine" | "name" | "owner", string>>>({});

  const chosen = choices.find((item) => item.name === engine) ?? (choices.length === 1 ? choices[0] : undefined);
  const forApp = app !== NO_APP;

  const reset = (): void => {
    setEngine(preset ?? null);
    setName("");
    setOwner("");
    setApp(NO_APP);
    setErrors({});
  };

  const create = useMutation({
    mutationFn: async () => {
      if (chosen === undefined) throw new Error("no engine");
      if (forApp) {
        const accepted = await request("post", "/api/apps/{domain}/databases", {
          params: { domain: app },
          body: { engine: chosen.name, name: name.trim() === "" ? null : name.trim(), env_var: null, extra_vars: false, restart: true },
        });
        return { kind: "job" as const, accepted };
      }
      const database = await request("post", "/api/databases/databases", {
        body: { engine: chosen.name, name: name.trim(), owner: owner.trim() === "" ? null : owner.trim(), encoding: null, app: null },
      });
      return { kind: "database" as const, database };
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
      onOpenChange(false);
      reset();
      if (result.kind === "database") {
        announce(t("databases.create.created", { name: result.database.name }));
        void navigate({ to: "/databases/$engine/$name", params: { engine: result.database.engine, name: result.database.name } });
      } else {
        void queryClient.invalidateQueries({ queryKey: jobKeys.active });
        announce(t("databases.create.linking", { domain: app }));
        void navigate({ to: "/apps/$domain/database", params: { domain: app } });
      }
    },
    onError: (error) => {
      if (isApiError(error) && error.fields) {
        const fields = error.fields;
        setErrors({
          ...(fields["name"] ? { name: fields["name"] } : {}),
          ...(fields["owner"] ? { owner: fields["owner"] } : {}),
          ...(fields["engine"] ? { engine: fields["engine"] } : {}),
        });
      }
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (chosen === undefined) found.engine = t("databases.create.engineRequired");
    const trimmed = name.trim();
    if (!forApp && trimmed === "") found.name = t("databases.create.nameRequired");
    else if (trimmed !== "" && !NAME.test(trimmed)) found.name = t("databases.create.nameInvalid");
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    create.mutate();
  };

  const failure = create.isError && !(isApiError(create.error) && create.error.fields) ? create.error : null;
  const formId = "create-database-form";
  const Add = ICONS.add;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          reset();
          create.reset();
        }
      }}
      size="md"
      title={t("databases.create.title")}
      description={t("databases.create.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} icon={<Add aria-hidden="true" />}>
            {forApp ? t("databases.create.submitLinked") : t("databases.create.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {engines.data !== undefined && choices.length === 0 ? (
          <Notice tone="warning" title={t("databases.create.noEngineTitle")}>
            {t("databases.create.noEngineBody")}
          </Notice>
        ) : null}
        <Field label={t("databases.fields.engine")} nativeLabel={false} error={errors.engine}>
          <Select
            value={chosen?.name ?? null}
            placeholder={t("databases.create.enginePlaceholder")}
            onValueChange={(value) => {
              setEngine(value);
              setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([field]) => field !== "engine")));
            }}
            options={choices.map((item) => ({ value: item.name, label: item.version ? `${item.display_name} ${item.version}` : item.display_name }))}
          />
        </Field>
        <Field
          label={t("databases.fields.name")}
          optional={forApp}
          description={forApp ? t("databases.create.nameForApp") : t("databases.create.nameHelp")}
          error={errors.name}
        >
          <Input
            mono
            value={name}
            onValueChange={(value: string) => setName(value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder="shop_production"
          />
        </Field>
        <Field label={t("databases.create.usedBy")} optional nativeLabel={false} description={forApp ? t("databases.create.usedByLinked") : t("databases.create.usedByHelp")}>
          <Select
            value={app}
            onValueChange={(value) => setApp(value)}
            options={[
              { value: NO_APP, label: t("databases.create.noApp") },
              ...(apps.data?.apps ?? []).filter((item) => item.app_type !== "static").map((item) => ({ value: item.domain, label: item.domain })),
            ]}
          />
        </Field>
        {!forApp && chosen?.name === "postgresql" ? (
          <Field label={t("databases.fields.owner")} optional description={t("databases.create.ownerHelp")} error={errors.owner}>
            <Input mono value={owner} onValueChange={(value: string) => setOwner(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
          </Field>
        ) : null}
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.create.failed")} /> : null}
      </form>
    </Dialog>
  );
}
