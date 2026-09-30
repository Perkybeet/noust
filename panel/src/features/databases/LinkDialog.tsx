import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { accessQuery, databaseKeys, databasesQuery, enginesQuery } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { can, sortEngines } from "./engines";
import type { Accepted } from "./jobs";

const DEFAULT = "";
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** The variable a link writes unless told otherwise: what the engine's applications expect. */
export function defaultEnvVar(engine: string | null, engines: readonly { name: string; capabilities?: readonly string[] | undefined }[] | undefined): string {
  const found = engines?.find((item) => item.name === engine);
  return found !== undefined && can(found, "keys") && !can(found, "tables") ? "REDIS_URL" : "DATABASE_URL";
}

export type LinkDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the queued job; the caller follows it where it shows jobs. */
  onQueued: (accepted: Accepted, domain: string, database: string) => void;
} & (
  | {
      /** From a database's page: the application is chosen. */
      engine: string;
      database: string;
      domain?: undefined;
    }
  | {
      /** From an application's Database tab: the database is chosen. */
      domain: string;
      engine?: undefined;
      database?: undefined;
    }
);

/**
 * Giving an existing database to an application: which application (or which database), the
 * account it signs in as, and the variable its connection string is written to. The
 * application restarts on the new variable behind its startup check; if it does not come up,
 * it gets its previous environment back.
 */
export function LinkDialog(props: LinkDialogProps) {
  const { open, onOpenChange, onQueued } = props;
  const t = useT();
  const queryClient = useQueryClient();
  const fromApp = props.domain !== undefined;
  const engines = useQuery({ ...enginesQuery(), enabled: open });
  const apps = useQuery({ ...appsQuery(), enabled: open && !fromApp });
  const databases = useQuery({ ...databasesQuery(), enabled: open && fromApp });

  const [domain, setDomain] = useState(props.domain ?? DEFAULT);
  const [target, setTarget] = useState(props.engine !== undefined ? `${props.engine}/${props.database}` : DEFAULT);
  const [account, setAccount] = useState(DEFAULT);
  const [envVar, setEnvVar] = useState("");
  const [extra, setExtra] = useState(false);
  const [errors, setErrors] = useState<Partial<Record<"domain" | "target" | "envVar", string>>>({});

  const [engine = "", database = ""] = target === DEFAULT ? [] : target.split(/\/(.*)/s);
  const access = useQuery({ ...accessQuery(engine, database), enabled: open && engine !== "" && database !== "" });
  const accounts = (access.data?.access ?? []).filter((entry) => entry.managed && !entry.internal);
  const fallbackVar = defaultEnvVar(engine === "" ? null : engine, engines.data?.engines);

  const link = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/databases/link", {
        params: { domain },
        body: {
          engine,
          database,
          username: account === DEFAULT ? null : account,
          env_var: envVar.trim() === "" ? null : envVar.trim(),
          extra_vars: extra,
          restart: true,
        },
      }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.app(domain) });
      onQueued(accepted, domain, database);
      onOpenChange(false);
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const found: typeof errors = {};
    if (domain === DEFAULT) found.domain = t("databases.link.appRequired");
    if (target === DEFAULT) found.target = t("databases.link.databaseRequired");
    if (envVar.trim() !== "" && !ENV_NAME.test(envVar.trim())) found.envVar = t("databases.link.envVarInvalid");
    setErrors(found);
    if (Object.keys(found).length === 0) link.mutate();
  };

  const engineList = sortEngines(engines.data?.engines ?? []);
  const choices = (databases.data?.databases ?? []).filter((item) => !item.missing && engineList.some((e) => e.name === item.engine && e.running));
  const formId = "link-database-form";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) link.reset();
      }}
      size="md"
      title={fromApp ? t("databases.link.titleFromApp") : t("databases.link.title", { name: props.database })}
      description={t("databases.link.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("databases.common.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={link.isPending}>
            {t("databases.link.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {fromApp ? (
          <Field label={t("databases.fields.database")} nativeLabel={false} error={errors.target}>
            <Select
              value={target === DEFAULT ? null : target}
              placeholder={t("databases.link.databasePlaceholder")}
              onValueChange={(value) => {
                setTarget(value);
                setAccount(DEFAULT);
              }}
              options={choices.map((item) => ({
                value: `${item.engine}/${item.name}`,
                label: item.name,
                hint: engineList.find((e) => e.name === item.engine)?.display_name ?? item.engine,
              }))}
            />
          </Field>
        ) : (
          <Field label={t("databases.link.application")} nativeLabel={false} error={errors.domain}>
            <Select
              value={domain === DEFAULT ? null : domain}
              placeholder={t("databases.link.appPlaceholder")}
              onValueChange={(value) => setDomain(value)}
              options={(apps.data?.apps ?? []).filter((app) => app.app_type !== "static").map((app) => ({ value: app.domain, label: app.domain }))}
            />
          </Field>
        )}
        <Field label={t("databases.link.account")} optional nativeLabel={false} description={t("databases.link.accountHelp")}>
          <Select
            value={account}
            onValueChange={(value) => setAccount(value)}
            options={[{ value: DEFAULT, label: t("databases.link.accountDefault") }, ...accounts.map((entry) => ({ value: entry.username, label: entry.username }))]}
          />
        </Field>
        <Field label={t("databases.link.envVar")} optional description={t.rich("databases.link.envVarHelp", { name: <Mono>{fallbackVar}</Mono> })} error={errors.envVar}>
          <Input mono value={envVar} placeholder={fallbackVar} onValueChange={(value: string) => setEnvVar(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        <Checkbox checked={extra} onCheckedChange={setExtra} label={t("databases.link.extraVars")} description={t("databases.link.extraVarsHelp")} />
        <Notice title={t("databases.link.restartTitle")}>{t("databases.link.restartBody")}</Notice>
        {link.isError ? <ErrorBlock live compact error={link.error} title={t("databases.link.failed")} /> : null}
      </form>
    </Dialog>
  );
}
