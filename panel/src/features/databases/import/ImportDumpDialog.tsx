import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError, request } from "../../../api/client";
import { databaseKeys, databasesQuery, enginesQuery } from "../../../api/queries/databases";
import { isJobFinished, jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Stepper } from "../../../components/page/Stepper";
import { WizardActions } from "../../../components/page/Wizard";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { creatableEngines } from "../CreateDatabaseDialog";
import { engineFamilyOf } from "../engineKey";
import { engineName, instanceLabel } from "../engines";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { jobState } from "../jobs";
import type { Accepted } from "../jobs";
import { ImportResultList, spendUpload, useDumpFile } from "./dumpFile";

type Step = "dump" | "options" | "import";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

export interface ImportDumpDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Fixed when started from a database's own page; chosen in the dialog otherwise. */
  engine?: string;
  database?: string;
  /** Called with the queued job, for a page that follows its own jobs too. */
  onQueued?: (accepted: Accepted, database: string) => void;
}

/**
 * Loading a dump into a database that exists, from the browser, in three steps: the dump (the file, sent in chunks that survive a
 * dropped connection, and what the server found it to be), how it is loaded (over the data there, or replacing it, with the owners
 * it names or not) and the job, its output streaming as it runs. Nothing is changed before the last button, which asks "Confirm
 * it's you" like every restore. A database that starts from a dump is made in "New database", which takes the same file.
 */
export function ImportDumpDialog({ open, onOpenChange, engine: presetEngine, database: presetDatabase, onQueued }: ImportDumpDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fixed = presetEngine !== undefined && presetDatabase !== undefined;
  const engines = useQuery({ ...enginesQuery(), enabled: open && !fixed });
  const databases = useQuery({ ...databasesQuery(), enabled: open });
  const choices = creatableEngines(engines.data?.engines ?? []);

  const [step, setStep] = useState<Step>("dump");
  const [pickedEngine, setPickedEngine] = useState<string | null>(null);
  const [typedDatabase, setTypedDatabase] = useState("");
  const engine = presetEngine ?? pickedEngine ?? (choices.length === 1 ? (choices[0]?.name ?? null) : null);
  const database = presetDatabase ?? typedDatabase.trim();
  const [replace, setReplace] = useState(false);
  const [keepOwners, setKeepOwners] = useState(false);
  const [safety, setSafety] = useState(true);
  const [typed, setTyped] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"engine" | "database", string>>>({});
  const followed = useFollowedJob();
  const databaseRef = useRef<HTMLInputElement>(null);
  const typedRef = useRef<HTMLInputElement>(null);

  const exists = engine !== null && database !== "" && (databases.data?.databases ?? []).some((item) => item.engine === engine && item.name === database && !item.missing);
  const family = engine === null ? "" : engineFamilyOf(engine);
  const choice = choices.find((item) => item.name === engine);
  const engineLabel = choice !== undefined ? instanceLabel(t, choice) : engine !== null ? engineName(engine) : "";
  const dump = useDumpFile({ enabled: open, engine, engineLabel, inspect: step === "dump" });
  const { upload, loadable } = dump;

  const importMutation = useMutation({
    mutationFn: () =>
      request("post", "/api/databases/imports", {
        body: { engine: engine ?? "", database, upload_id: upload.uploadId, path: null, replace, keep_owners: family === "postgresql" && keepOwners, safety_backup: safety, as_superuser: false, confirm_superuser: false },
      }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
      onQueued?.(accepted, database);
      setStep("import");
    },
    onError: (error) => {
      // Closing "Confirm it's you" changes nothing: the options stay as they were.
      if (error instanceof ElevationCancelledError) importMutation.reset();
    },
  });

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const running = followed.id !== null && !finished;

  // What a finished import changed is read again once.
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    spendUpload();
    void queryClient.invalidateQueries({ queryKey: databaseKeys.all });
  }, [job, queryClient]);

  const reset = (): void => {
    setStep("dump");
    setReplace(false);
    setKeepOwners(false);
    setSafety(true);
    setTyped("");
    setErrors({});
    setPickedEngine(null);
    setTypedDatabase("");
    followed.dismiss();
    importMutation.reset();
  };

  const close = (): void => {
    if (importMutation.isPending) return;
    if (running) {
      toast.info(t("dumpImport.job.running", { name: database }), {
        description: t("dumpImport.job.elsewhere"),
        action: { label: t("dumpImport.job.open", { name: database }), onClick: () => void navigate({ to: "/activity" }) },
      });
    } else if (step !== "import") {
      dump.discard();
    }
    onOpenChange(false);
    reset();
  };

  const nextFromDump = (): void => {
    const found: typeof errors = {};
    if (!fixed) {
      if (engine === null) found.engine = t("dumpImport.target.engineRequired");
      if (database === "") found.database = t("dumpImport.target.databaseRequired");
      else if (!NAME.test(database)) found.database = t("dumpImport.target.databaseInvalid");
    }
    setErrors(found);
    if (found.engine !== undefined) return;
    if (found.database !== undefined) {
      databaseRef.current?.focus();
      return;
    }
    setStep("options");
  };

  const missingOnDump =
    !fixed && engine === null
      ? t("dumpImport.target.engineRequired")
      : !fixed && database === ""
        ? t("dumpImport.target.databaseRequired")
        : upload.phase !== "done"
          ? dump.transferring
            ? t("dumpImport.upload.uploading")
            : t("dumpImport.file.required")
          : loadable === false
            ? t("dumpImport.format.notLoadable", { engine: engineLabel })
            : undefined;

  const mustType = replace && exists;
  const missingOnOptions = mustType && typed.trim() !== database ? t("dumpImport.options.confirmRequired") : undefined;

  const steps = [
    { id: "dump", label: t("dumpImport.steps.dump") },
    { id: "options", label: t("dumpImport.steps.options") },
    { id: "import", label: t("dumpImport.steps.import") },
  ];

  let body;
  let footer;
  if (step === "dump") {
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        {fixed ? null : (
          <>
            <Field label={t("dumpImport.target.engine")} nativeLabel={false} error={errors.engine}>
              <Select
                value={engine}
                placeholder={t("dumpImport.target.enginePlaceholder")}
                onValueChange={(value) => {
                  setPickedEngine(value);
                  setErrors({});
                }}
                options={choices.map((item) => ({ value: item.name, label: instanceLabel(t, item) }))}
              />
            </Field>
            {engines.data !== undefined && choices.length === 0 ? <InlineMessage>{t("dumpImport.target.noEngine")}</InlineMessage> : null}
            <Field label={t("dumpImport.target.database")} description={t("dumpImport.target.databaseHelp")} error={errors.database}>
              <Input
                ref={databaseRef}
                mono
                value={typedDatabase}
                onValueChange={(value: string) => {
                  setTypedDatabase(value);
                  setErrors({});
                }}
                autoComplete="off"
                spellCheck={false}
              />
            </Field>
          </>
        )}
        {dump.field}
        {/* A file over the limit is not sent from here: the terminal takes a path on the server, which the console cannot. */}
        {fixed || engine !== null ? (
          <CommandHint
            reason="solo-terminal"
            label={t("dumpImport.limit.path", { limit: formatBytes(dump.limit, t.locale) })}
            command={`noust db import ${database === "" ? "NAME" : database} /srv/dumps/dump.sql.gz -e ${engine ?? "postgresql"}`}
          />
        ) : null}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{ label: t("common.words.cancel"), onClick: close }}
        next={{ label: t("common.words.continue"), onClick: nextFromDump }}
        {...(missingOnDump !== undefined ? { missing: missingOnDump, onMissing: () => (upload.phase === "idle" ? dump.fileRef.current?.focus() : undefined) } : {})}
      />
    );
  } else if (step === "options") {
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        <KeyValueList
          items={[
            { label: t("dumpImport.options.summary.dump"), value: upload.fileName },
            { label: t("dumpImport.options.summary.into"), value: t("dumpImport.target.into", { name: database, engine: engineLabel }), mono: false, copy: false },
            {
              label: t("dumpImport.options.summary.mode"),
              value: !exists ? t("dumpImport.options.summary.createNew") : replace ? t("dumpImport.options.summary.replaceAll") : t("dumpImport.options.summary.loadOver"),
              mono: false,
              copy: false,
            },
          ]}
        />
        <p className="text-13 text-fg-muted">{exists ? t("dumpImport.options.exists", { name: database, engine: engineLabel }) : t("dumpImport.options.created", { name: database, engine: engineLabel })}</p>
        {exists ? (
          <>
            <Checkbox checked={replace} onCheckedChange={setReplace} label={t("dumpImport.options.replace")} description={t("dumpImport.options.replaceHelp", { name: database })} />
            {replace ? (
              <p className="text-13 text-fg-muted">{t("dumpImport.options.safetyAlways", { name: database })}</p>
            ) : (
              <>
                <Checkbox checked={safety} onCheckedChange={setSafety} label={t("dumpImport.options.safety", { name: database })} description={t("dumpImport.options.safetyHelp")} />
                <p className="text-12 text-fg-muted">{t("dumpImport.options.loadsOver", { name: database })}</p>
              </>
            )}
          </>
        ) : null}
        {family === "postgresql" ? <Checkbox checked={keepOwners} onCheckedChange={setKeepOwners} label={t("dumpImport.options.keepOwners")} description={t("dumpImport.options.keepOwnersHelp")} /> : null}
        {mustType ? (
          <Field label={t("dumpImport.options.confirmTitle")} description={t.rich("dumpImport.options.confirmType", { name: <Mono key="name">{database}</Mono> })}>
            <Input ref={typedRef} mono value={typed} onValueChange={(value: string) => setTyped(value)} autoComplete="off" spellCheck={false} />
          </Field>
        ) : null}
        {importMutation.isError && !(importMutation.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={importMutation.error} title={t("dumpImport.job.startFailed")} /> : null}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{
          label: t("common.words.back"),
          onClick: () => {
            if (!importMutation.isPending) setStep("dump");
          },
        }}
        next={{ label: t("dumpImport.start"), loading: importMutation.isPending, onClick: () => importMutation.mutate() }}
        {...(missingOnOptions !== undefined ? { missing: missingOnOptions, onMissing: () => typedRef.current?.focus() } : {})}
      />
    );
  } else {
    const state = job === null ? "queued" : jobState(job.status);
    body = (
      <JobLogPanel
        job={job}
        words={{
          queued: t("dumpImport.job.queued", { name: database }),
          running: t("dumpImport.job.running", { name: database }),
          succeeded: t("dumpImport.job.succeeded", { name: database }),
          failed: t("dumpImport.job.failed", { name: database }),
        }}
        label={t("dumpImport.job.logLabel", { name: database })}
        result={<ImportResultList result={job !== null && state === "succeeded" ? job.result : undefined} database={database} />}
      />
    );
    footer = (
      <>
        {state === "succeeded" && engine !== null ? (
          <Link to="/databases/$engine/$name" params={{ engine, name: database }} onClick={close} className={buttonClassName("secondary")}>
            {t("dumpImport.job.open", { name: database })}
          </Link>
        ) : null}
        <Button variant="primary" onClick={close}>
          {finished ? t("common.words.done") : t("common.words.close")}
        </Button>
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="lg"
      title={fixed ? t("dumpImport.titleInto", { name: database }) : t("dumpImport.title")}
      description={t("dumpImport.description")}
      footer={footer}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
