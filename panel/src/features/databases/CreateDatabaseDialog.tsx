import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ElevationCancelledError, isApiError, request } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { databaseKeys, databasesQuery, enginesQuery } from "../../api/queries/databases";
import type { DbServer, Engine } from "../../api/queries/databases";
import { isJobFinished, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { JobLogPanel } from "../jobs/JobLogPanel";
import { can, sortInstances } from "./engines";
import { engineFamilyOf } from "./engineKey";
import { formatWords, spendUpload, useDumpFile } from "./import/dumpFile";
import { jobState } from "./jobs";
import { placeOf, serverTitle, sortServers } from "./servers";
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
/** The card that is not an engine but the way to add one: choosing it changes what the button does. */
const OTHER = "other";
const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

type Start = "empty" | "dump";
type Errors = Partial<Record<"engine" | "name" | "owner", string>>;

export interface CreateDatabaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Opens on the engine with this key (`postgresql@pg16`), as an engine's own menu would. */
  engine?: string;
  /** The application that will use it, as the Database tab of an application opens it: chosen already. */
  app?: string;
  /** What "Another engine or version" does: goes on to adding an engine, which is where a database that needs one begins. */
  onAddEngine?: () => void;
}

/** What an engine says of where it runs and what it holds, as the line of its card. */
function engineLine(t: T, server: DbServer): string {
  const place = placeOf(server);
  const where = place.kind === "noust" ? t("databases.create.engineContainer", { name: place.name }) : place.kind === "app" ? t("databases.create.engineStack", { app: place.app }) : t("databases.create.engineSystem");
  const count = server.databases ?? 0;
  return t("databases.create.engineLine", { where, port: server.port != null ? String(server.port) : "–", count });
}

/**
 * "New database", the one dialog that makes a database (from the list, from an application's Database tab, from Home): a name,
 * whether it starts empty or from a dump (what was a button of its own), the engine it is made on (a card each, the default
 * first, and "Another engine or version" for the one that has to be added), and, if an application is to use it, which. For an
 * application it becomes "Create and link": Noust makes an account for it, writes the connection string into its environment
 * and restarts it behind its startup check, as a job its Database tab follows. From a dump, the database is made by loading
 * it and the job is followed here, to its end.
 */
export function CreateDatabaseDialog({ open, onOpenChange, engine: preset, app: presetApp, onAddEngine }: CreateDatabaseDialogProps) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { servers: all } = useDatabaseServers();
  const engines = useQuery({ ...enginesQuery(), enabled: open });
  const apps = useQuery({ ...appsQuery(), enabled: open });
  const databases = useQuery({ ...databasesQuery(), enabled: open });
  const choices = useMemo(() => creatableServers(all ?? [], engines.data?.engines ?? []), [all, engines.data]);

  const [start, setStart] = useState<Start>("empty");
  const [picked, setPicked] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [app, setApp] = useState(presetApp ?? NO_APP);
  const [keepOwners, setKeepOwners] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [pressed, setPressed] = useState(false);
  const followed = useFollowedJob();
  const nameRef = useRef<HTMLInputElement>(null);

  const loaded = all !== undefined && engines.data !== undefined;
  const none = loaded && choices.length === 0;
  const fallbackKey = choices.find((server) => server.key === preset)?.key ?? choices.find((server) => server.default)?.key ?? choices[0]?.key ?? null;
  const key = picked ?? (none ? OTHER : fallbackKey);
  const chosen = key === OTHER ? undefined : choices.find((server) => server.key === key);
  const adding = key === OTHER;
  const forApp = app !== NO_APP && start === "empty";
  const trimmed = name.trim();
  const engineLabel = chosen === undefined ? "" : serverTitle(chosen);
  const dump = useDumpFile({ enabled: open && start === "dump", engine: chosen?.key ?? null, engineLabel, inspect: start === "dump" && followed.id === null });

  const reset = (): void => {
    setStart("empty");
    setPicked(null);
    setName("");
    setOwner("");
    setApp(presetApp ?? NO_APP);
    setKeepOwners(false);
    setErrors({});
    setPressed(false);
    followed.dismiss();
  };

  const finish = (): void => {
    onOpenChange(false);
    reset();
  };

  const create = useMutation({
    mutationFn: async () => {
      if (chosen === undefined) throw new Error("no engine");
      if (forApp) {
        const accepted = await request("post", "/api/apps/{domain}/databases", {
          params: { domain: app },
          body: { engine: chosen.key, name: trimmed === "" ? null : trimmed, env_var: null, extra_vars: false, restart: true, install: false },
        });
        return { kind: "job" as const, accepted };
      }
      const database = await request("post", "/api/databases/databases", {
        body: { engine: chosen.key, name: trimmed, owner: owner.trim() === "" ? null : owner.trim(), encoding: null, app: null },
      });
      return { kind: "database" as const, database };
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.servers });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
      finish();
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

  // A database that starts from a dump is made by loading it: the import creates what is not there.
  const load = useMutation({
    mutationFn: () =>
      request("post", "/api/databases/imports", {
        body: { engine: chosen?.key ?? "", database: trimmed, upload_id: dump.upload.uploadId, path: null, replace: false, keep_owners: engineFamilyOf(chosen?.key ?? "") === "postgresql" && keepOwners, safety_backup: false, as_superuser: false, confirm_superuser: false },
      }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
    },
    onError: (error) => {
      // Closing "Confirm it's you" changes nothing: the form stays as it was.
      if (error instanceof ElevationCancelledError) load.reset();
    },
  });

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const state = job === null ? "queued" : jobState(job.status);
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !finished || settled.current === job.id) return;
    settled.current = job.id;
    spendUpload();
    void queryClient.invalidateQueries({ queryKey: databaseKeys.all });
  }, [job, finished, queryClient]);

  const submit = (event?: SyntheticEvent<HTMLFormElement>): void => {
    event?.preventDefault();
    if (adding) {
      finish();
      onAddEngine?.();
      return;
    }
    const found: Errors = {};
    if (chosen === undefined) found.engine = t("databases.create.engineRequired");
    if (!forApp && trimmed === "") found.name = t("databases.create.nameRequired");
    else if (trimmed !== "" && !NAME.test(trimmed)) found.name = t("databases.create.nameInvalid");
    else if (start === "dump" && chosen !== undefined && (databases.data?.databases ?? []).some((item) => item.engine === chosen.key && item.name === trimmed && !item.missing)) {
      found.name = t("databases.create.nameTaken", { engine: engineLabel });
    }
    setErrors(found);
    if (found.name !== undefined) {
      nameRef.current?.focus();
      return;
    }
    if (found.engine !== undefined) return;
    if (start === "dump") load.mutate();
    else create.mutate();
  };

  const failure = create.isError && !(isApiError(create.error) && create.error.fields) ? create.error : null;
  const formId = "create-database-form";
  const Add = ICONS.add;
  const importing = followed.id !== null;
  // What stops "Create" from being pressed, said where the person looks: the file is not there yet, or it is not for this engine.
  const dumpMissing = start === "dump" && !adding ? (dump.upload.phase !== "done" ? (dump.transferring ? t("dumpImport.upload.uploading") : t("dumpImport.file.required")) : dump.loadable === false ? t("dumpImport.format.notLoadable", { engine: engineLabel }) : undefined) : undefined;

  const cards = [
    ...choices.map((server) => ({ value: server.key, label: serverTitle(server), description: engineLine(t, server) })),
    { value: OTHER, label: t("databases.create.other"), description: t("databases.create.otherLine") },
  ];

  const close = (open: boolean): void => {
    if (open) return;
    if (importing && !finished) {
      // The load goes on in its job: the activity centre follows it, and the dialog does not hold it.
      onOpenChange(false);
      reset();
      return;
    }
    if (start === "dump" && !importing) dump.discard();
    finish();
    create.reset();
    load.reset();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("databases.create.title")}
      description={importing ? undefined : t("databases.create.description")}
      footer={
        importing ? (
          <>
            {state === "succeeded" && chosen !== undefined ? (
              <Link to="/databases/$engine/$name" params={{ engine: chosen.key, name: trimmed }} onClick={finish} className={buttonClassName("secondary")}>
                {t("dumpImport.job.open", { name: trimmed })}
              </Link>
            ) : null}
            <Button variant="primary" onClick={finish}>
              {finished ? t("common.words.done") : t("common.words.close")}
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => close(false)}>{t("common.words.cancel")}</Button>
            {/* design-exception: submit-disabled-invalid the choices that are the person's are made; what is missing is said under the file, not by a button that will not press */}
            <Button type="submit" form={formId} variant="primary" loading={create.isPending || load.isPending} icon={adding ? undefined : <Add aria-hidden="true" />}>
              {adding ? t("databases.create.addEngine") : forApp ? t("databases.create.submitLinked") : t("databases.create.submit")}
            </Button>
          </>
        )
      }
    >
      {importing ? (
        <JobLogPanel
          job={job}
          words={{
            queued: t("dumpImport.job.queued", { name: trimmed }),
            running: t("dumpImport.job.running", { name: trimmed }),
            succeeded: t("dumpImport.job.succeeded", { name: trimmed }),
            failed: t("dumpImport.job.failed", { name: trimmed }),
          }}
          label={t("dumpImport.job.logLabel", { name: trimmed })}
          result={dump.upload.format !== null && state === "succeeded" ? <p className="text-13 text-fg">{t("dumpImport.job.result.format", { format: formatWords(t, dump.upload.format) })}</p> : undefined}
        />
      ) : (
        <form
          id={formId}
          noValidate
          onSubmit={(event) => {
            // The file being absent is said under it, and focus goes to the field that lacks it.
            if (dumpMissing !== undefined && start === "dump") {
              event.preventDefault();
              setPressed(true);
              dump.fileRef.current?.focus();
              return;
            }
            submit(event);
          }}
          className="flex flex-col gap-5"
        >
          <Field label={t("common.words.name")} optional={forApp} description={forApp ? t("databases.create.nameForApp") : t("databases.create.nameHelp")} error={errors.name}>
            <Input ref={nameRef} mono value={name} onValueChange={(value: string) => setName(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="shop_production" />
          </Field>
          <div className="flex flex-col gap-3">
            <SegmentedControl<Start>
              label={t("databases.create.startLabel")}
              value={start}
              onValueChange={setStart}
              options={[
                { value: "empty", label: t("databases.create.startEmpty") },
                { value: "dump", label: t("databases.create.startDump") },
              ]}
            />
            {start === "dump" ? (
              <>
                {dump.field}
                {pressed && dumpMissing !== undefined ? <InlineMessage tone="error">{dumpMissing}</InlineMessage> : null}
                {engineFamilyOf(chosen?.key ?? "") === "postgresql" ? <Checkbox checked={keepOwners} onCheckedChange={setKeepOwners} label={t("dumpImport.options.keepOwners")} description={t("dumpImport.options.keepOwnersHelp")} /> : null}
              </>
            ) : null}
          </div>
          {none ? (
            <Notice tone="warning" title={t("databases.create.noEngineTitle")}>
              {t("databases.create.noEngineBody")}
            </Notice>
          ) : null}
          <ChoiceCards
            legend={t("databases.fields.engine")}
            value={key ?? OTHER}
            onValueChange={(value) => {
              setPicked(value);
              setErrors((previous) => {
                const next = { ...previous };
                delete next.engine;
                return next;
              });
            }}
            options={cards}
            error={errors.engine}
          />
          {start === "empty" && !adding ? (
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
          ) : null}
          {start === "dump" && !adding ? <p className="text-13 text-fg-muted">{t("databases.create.dumpThenLink")}</p> : null}
          {!forApp && start === "empty" && !adding && chosen?.engine === "postgresql" ? (
            <Disclosure label={t("databases.create.more")}>
              <div className="pt-2">
                <Field label={t("databases.fields.owner")} optional description={t("databases.create.ownerHelp")} error={errors.owner}>
                  <Input mono value={owner} onValueChange={(value: string) => setOwner(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
                </Field>
              </div>
            </Disclosure>
          ) : null}
          {failure !== null ? <ErrorBlock live compact error={failure} title={t("databases.create.failed")} /> : null}
          {load.isError && !(load.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={load.error} title={t("dumpImport.job.startFailed")} /> : null}
        </form>
      )}
    </Dialog>
  );
}
