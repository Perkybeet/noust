import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { databaseKeys, enginesQuery } from "../../api/queries/databases";
import type { DbServer, Engine } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { can, sortInstances } from "./engines";
import { serverTitle, sortServers } from "./servers";
import { useDatabaseServers } from "./servers/useDatabaseServers";

/** Engines a database can be created on: running, and holding named databases (not Redis's slots). */
export function creatableEngines(engines: readonly Engine[]): Engine[] {
  return sortInstances(engines.filter((engine) => engine.running && (can(engine, "sql") || can(engine, "documents"))));
}

/**
 * The servers a database can be created on: running, and holding named databases (not Redis's slots), the
 * default of each engine first. An application's own container is one of them: it is listed, and a database of
 * its own can be made in it.
 */
export function creatableServers(servers: readonly DbServer[], engines: readonly Engine[]): DbServer[] {
  const named = new Set(engines.filter((engine) => can(engine, "sql") || can(engine, "documents")).map((engine) => engine.name));
  return sortServers(servers.filter((server) => server.running && named.has(server.key)));
}

const NO_APP = "";
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export interface CreateDatabaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Opens on the server with this key (`postgresql@pg16`), as a server's own menu would. */
  engine?: string;
}

type Errors = Partial<Record<"engine" | "server" | "name" | "owner", string>>;

/**
 * "New database": which engine, a name and, if an application is to use it, which one. It is made on the default
 * server of the engine, and the server is asked about only when the engine has more than one (another version
 * beside the first): nobody is asked where it runs when there is one place. For an application it becomes "Create
 * and link": Noust makes an account for it, writes the connection string into its environment and restarts it
 * behind its startup check, as a job the application's Database tab follows.
 */
export function CreateDatabaseDialog({ open, onOpenChange, engine: preset }: CreateDatabaseDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { servers: all } = useDatabaseServers();
  const engines = useQuery({ ...enginesQuery(), enabled: open });
  const apps = useQuery({ ...appsQuery(), enabled: open });
  const choices = useMemo(() => creatableServers(all ?? [], engines.data?.engines ?? []), [all, engines.data]);
  const flavours = useMemo(() => {
    const seen = new Map<string, string>();
    for (const server of choices) if (!seen.has(server.flavour)) seen.set(server.flavour, server.display_name);
    return [...seen].map(([value, label]) => ({ value, label }));
  }, [choices]);

  const [flavour, setFlavour] = useState<string | null>(null);
  const [serverKey, setServerKey] = useState<string | null>(preset ?? null);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [app, setApp] = useState(NO_APP);
  const [errors, setErrors] = useState<Errors>({});

  const presetFlavour = choices.find((server) => server.key === preset)?.flavour;
  // One engine on offer is the engine: nobody is asked to choose between one thing.
  const chosenFlavour = flavour ?? presetFlavour ?? (flavours.length === 1 ? (flavours[0]?.value ?? null) : null);
  const onEngine = choices.filter((server) => server.flavour === chosenFlavour);
  const chosen = onEngine.find((server) => server.key === serverKey) ?? onEngine.find((server) => server.default) ?? onEngine[0];
  const forApp = app !== NO_APP;

  const reset = (): void => {
    setFlavour(null);
    setServerKey(preset ?? null);
    setName("");
    setOwner("");
    setApp(NO_APP);
    setErrors({});
  };

  const create = useMutation({
    mutationFn: async () => {
      if (chosen === undefined) throw new Error("no server");
      if (forApp) {
        const accepted = await request("post", "/api/apps/{domain}/databases", {
          params: { domain: app },
          body: { engine: chosen.key, name: name.trim() === "" ? null : name.trim(), env_var: null, extra_vars: false, restart: true, install: false },
        });
        return { kind: "job" as const, accepted };
      }
      const database = await request("post", "/api/databases/databases", {
        body: { engine: chosen.key, name: name.trim(), owner: owner.trim() === "" ? null : owner.trim(), encoding: null, app: null },
      });
      return { kind: "database" as const, database };
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.servers });
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
    const found: Errors = {};
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
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.words.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} icon={<Add aria-hidden="true" />}>
            {forApp ? t("databases.create.submitLinked") : t("databases.create.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {all !== undefined && engines.data !== undefined && choices.length === 0 ? (
          <Notice tone="warning" title={t("databases.create.noEngineTitle")}>
            {t("databases.create.noEngineBody")}
          </Notice>
        ) : null}
        <Field label={t("databases.fields.engine")} nativeLabel={false} error={errors.engine}>
          <Select
            value={chosenFlavour}
            placeholder={t("databases.create.enginePlaceholder")}
            onValueChange={(value) => {
              setFlavour(value);
              setServerKey(null);
              setErrors((previous) => {
                const next = { ...previous };
                delete next.engine;
                return next;
              });
            }}
            options={flavours}
          />
        </Field>
        {onEngine.length > 1 ? (
          <Field label={t("databases.create.server")} nativeLabel={false} description={t("databases.create.serverHelp")}>
            <Select
              value={chosen?.key ?? null}
              onValueChange={(value) => setServerKey(value)}
              options={onEngine.map((server) => ({ value: server.key, label: server.default ? t("databases.create.serverDefault", { server: serverTitle(server) }) : serverTitle(server) }))}
            />
          </Field>
        ) : null}
        <Field label={t("common.words.name")} optional={forApp} description={forApp ? t("databases.create.nameForApp") : t("databases.create.nameHelp")} error={errors.name}>
          <Input mono value={name} onValueChange={(value: string) => setName(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="shop_production" />
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
        {!forApp && chosen?.engine === "postgresql" ? (
          <Disclosure label={t("databases.create.more")}>
            <div className="pt-2">
              <Field label={t("databases.fields.owner")} optional description={t("databases.create.ownerHelp")} error={errors.owner}>
                <Input mono value={owner} onValueChange={(value: string) => setOwner(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
              </Field>
            </div>
          </Disclosure>
        ) : null}
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.create.failed")} /> : null}
      </form>
    </Dialog>
  );
}
