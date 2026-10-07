import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { databaseKeys } from "../../../api/queries/databases";
import type { Engine } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { provisioningChoicesQuery, provisioningPlanFor } from "../../../api/queries/provisioning";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { can, isContainer, sortEngines } from "../../databases/engines";
import type { Accepted } from "../../databases/jobs";
import { NO_PICK, choiceFor, defaultOffer, initialFlavour, isPickable, isSlotFlavour, needsInstall, requestedVersion, shownVersion } from "../../databases/provisioning/choices";
import type { ProvisionPick } from "../../databases/provisioning/choices";
import { EngineVersionFields } from "../../databases/provisioning/EngineVersionFields";

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
 * "Create database" on an application: the engine and the version it is created on (from what
 * the server says it can do: the one that runs, or one it can install first), and what Noust
 * will write, said before it is done (the database, its account, the variables, the connection
 * string with its password masked). The database is owned by the account the application signs
 * in as, and the application restarts on it behind its startup check.
 */
export function CreateLinkDialog({ domain, open, onOpenChange, onQueued }: CreateLinkDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const choices = useQuery({ ...provisioningChoicesQuery(), enabled: open });
  const list = choices.data?.choices ?? [];
  const [picked, setPicked] = useState<ProvisionPick>(NO_PICK);
  // Until the operator chooses, the first engine that is there is the one on offer.
  const pick: ProvisionPick = { ...picked, flavour: picked.flavour ?? initialFlavour(list) };
  const chosen = choiceFor(list, pick.flavour);
  const usable = chosen !== undefined && isPickable(chosen);
  const version = requestedVersion(pick);
  // The server says an engine will be installed only for a version it was asked about: for the
  // default of one that is missing, the plan asks about the version the default stands for.
  const planVersion = version ?? (chosen !== undefined && needsInstall(chosen) ? (defaultOffer(chosen)?.version ?? null) : null);
  const plan = useQuery({ ...provisioningPlanFor(domain, pick.flavour ?? "", planVersion), enabled: open && usable });
  const [name, setName] = useState("");
  const [envVar, setEnvVar] = useState("");
  const [extra, setExtra] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<"name" | "envVar" | "install", string | undefined>>>({});
  const slot = isSlotFlavour(chosen);
  const installing = needsInstall(chosen);

  const create = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/databases", {
        params: { domain },
        body: {
          engine: pick.flavour ?? "",
          version,
          install: installing && pick.install,
          name: name.trim() === "" ? null : name.trim(),
          env_var: envVar.trim() === "" ? null : envVar.trim(),
          extra_vars: extra,
          restart: true,
        },
      }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.app(domain) });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
      onQueued(accepted, chosen?.engine ?? pick.flavour ?? "");
      onOpenChange(false);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (!slot && name.trim() !== "" && !NAME.test(name.trim())) found.name = t("databases.create.nameInvalid");
    if (slot && name.trim() !== "" && !/^\d{1,2}$/.test(name.trim())) found.name = t("databases.appTab.slotInvalid");
    if (envVar.trim() !== "" && !ENV_NAME.test(envVar.trim())) found.envVar = t("databases.link.envVarInvalid");
    if (installing && !pick.install) found.install = t("provisioning.install.required", { engine: chosen?.display_name ?? "" });
    setErrors(found);
    if (Object.keys(found).length === 0 && usable) create.mutate();
  };

  const fallbackVar = slot ? "REDIS_URL" : "DATABASE_URL";
  const formId = "create-link-form";
  const shown = shownVersion(chosen, pick);
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
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={!usable}>
            {t("databases.appTab.createAction")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {choices.isError && choices.data === undefined ? (
          <ErrorBlock compact error={choices.error} title={t("databases.wizard.enginesFailed")} onRetry={() => void choices.refetch()} />
        ) : choices.data === undefined ? (
          <div aria-busy="true">
            <span className="sr-only">{t("databases.wizard.loading")}</span>
            <Skeleton className="h-control-md w-full rounded-control" />
          </div>
        ) : (
          <EngineVersionFields
            choices={list}
            pick={pick}
            onPick={(next) => {
              setPicked(next);
              setName("");
              if (errors.install !== undefined) setErrors((previous) => ({ ...previous, install: undefined }));
            }}
            installError={errors.install}
          />
        )}
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
                ...(shown !== null ? [{ label: t("provisioning.plan.version"), value: shown, copy: false as const }] : []),
                { label: t("databases.appTab.variables"), value: [envVar.trim() || fallbackVar, ...(extra ? ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"] : [])].join(", "), copy: false },
                { label: t("databases.connect.connectionString"), value: plan.data.url, copy: false },
              ]}
            />
          )}
          {plan.data?.will_install ? <p className="text-13 text-fg-muted">{t("provisioning.plan.willInstall", { engine: plan.data.display_name })}</p> : null}
        </div>
        <Notice title={t("databases.link.restartTitle")}>{t("databases.link.restartBody")}</Notice>
        {create.isError ? <ErrorBlock live compact error={create.error} title={t("databases.appTab.createFailed")} /> : null}
      </form>
    </Dialog>
  );
}
