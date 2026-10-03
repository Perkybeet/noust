import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { databaseKeys, enginesQuery, provisioningPlanQuery } from "../../../api/queries/databases";
import type { Engine } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { can, isContainer, sortEngines } from "../../databases/engines";
import type { Accepted } from "../../databases/jobs";
import { defaultEnvVar } from "../../databases/LinkDialog";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Engines an application can be given a database on: running, with databases or slots. */
export function provisionableEngines(engines: readonly Engine[]): Engine[] {
  // A container's databases belong to its compose file: the connection string Noust writes
  // points at the host's engine, so only the host's engines provision.
  return sortEngines(
    engines.filter((engine) => !isContainer(engine) && engine.running && (can(engine, "sql") || can(engine, "documents") || can(engine, "keys"))),
  );
}

export interface CreateLinkDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onQueued: (accepted: Accepted, engine: string) => void;
}

/**
 * "Create database" on an application: an engine, and what Noust will write, said before it is
 * done (the database, its account, the variables, the connection string with its password
 * masked). The database is owned by the account the application signs in as, and the
 * application restarts on it behind its startup check.
 */
export function CreateLinkDialog({ domain, open, onOpenChange, onQueued }: CreateLinkDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const engines = useQuery({ ...enginesQuery(), enabled: open });
  const choices = provisionableEngines(engines.data?.engines ?? []);
  const [engine, setEngine] = useState<string | null>(null);
  const chosen = choices.find((item) => item.name === engine) ?? choices[0];
  const plan = useQuery({ ...provisioningPlanQuery(domain, chosen?.name ?? ""), enabled: open && chosen !== undefined });
  const [name, setName] = useState("");
  const [envVar, setEnvVar] = useState("");
  const [extra, setExtra] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<"name" | "envVar", string>>>({});
  const slot = chosen !== undefined && can(chosen, "keys") && !can(chosen, "tables");

  const create = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/databases", {
        params: { domain },
        body: { engine: chosen?.name ?? "", name: name.trim() === "" ? null : name.trim(), env_var: envVar.trim() === "" ? null : envVar.trim(), extra_vars: extra, restart: true },
      }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.app(domain) });
      onQueued(accepted, chosen?.name ?? "");
      onOpenChange(false);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (!slot && name.trim() !== "" && !NAME.test(name.trim())) found.name = t("databases.create.nameInvalid");
    if (slot && name.trim() !== "" && !/^\d{1,2}$/.test(name.trim())) found.name = t("databases.appTab.slotInvalid");
    if (envVar.trim() !== "" && !ENV_NAME.test(envVar.trim())) found.envVar = t("databases.link.envVarInvalid");
    setErrors(found);
    if (Object.keys(found).length === 0 && chosen !== undefined) create.mutate();
  };

  const fallbackVar = defaultEnvVar(chosen?.name ?? null, engines.data?.engines);
  const formId = "create-link-form";
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) create.reset();
      }}
      size="md"
      title={t("databases.appTab.createTitle")}
      description={t("databases.appTab.createDescription", { domain })}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={chosen === undefined}>
            {t("databases.appTab.createAction")}
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
        <Field label={t("databases.fields.engine")} nativeLabel={false}>
          <Select
            value={chosen?.name ?? null}
            placeholder={t("databases.create.enginePlaceholder")}
            onValueChange={(value) => {
              setEngine(value);
              setName("");
            }}
            options={choices.map((item) => ({ value: item.name, label: item.version ? `${item.display_name} ${item.version}` : item.display_name }))}
          />
        </Field>
        <Field
          label={slot ? t("databases.appTab.slot") : t("databases.fields.name")}
          optional
          description={plan.data ? t.rich("databases.appTab.nameDefault", { name: <Mono>{plan.data.database}</Mono> }) : undefined}
          error={errors.name}
        >
          <Input mono value={name} placeholder={plan.data?.database ?? ""} onValueChange={(value: string) => setName(value)} autoComplete="off" spellCheck={false} />
        </Field>
        <Field label={t("databases.link.envVar")} optional description={t.rich("databases.link.envVarHelp", { name: <Mono>{fallbackVar}</Mono> })} error={errors.envVar}>
          <Input mono value={envVar} placeholder={fallbackVar} onValueChange={(value: string) => setEnvVar(value)} autoComplete="off" spellCheck={false} />
        </Field>
        {!slot ? <Checkbox checked={extra} onCheckedChange={setExtra} label={t("databases.link.extraVars")} description={t("databases.link.extraVarsHelp")} /> : null}
        <div className="flex flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("databases.appTab.planTitle")}</p>
          {plan.isError ? (
            <ErrorBlock compact error={plan.error} title={t("databases.appTab.planFailed")} />
          ) : plan.data === undefined ? (
            <KeyValueListSkeleton rows={3} />
          ) : (
            <KeyValueList
              items={[
                { label: slot ? t("databases.appTab.slot") : t("databases.fields.database"), value: name.trim() || plan.data.database, copy: false },
                ...(slot ? [] : [{ label: t("databases.appTab.account"), value: plan.data.username, copy: false as const }]),
                { label: t("databases.appTab.variables"), value: [envVar.trim() || fallbackVar, ...(extra ? ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"] : [])].join(", "), copy: false },
                { label: t("databases.connect.connectionString"), value: plan.data.url, copy: false },
              ]}
            />
          )}
        </div>
        <Notice title={t("databases.link.restartTitle")}>{t("databases.link.restartBody")}</Notice>
        {create.isError ? <ErrorBlock live compact error={create.error} title={t("databases.appTab.createFailed")} /> : null}
      </form>
    </Dialog>
  );
}
