import { Input as BaseInput } from "@base-ui/react/input";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { FileArchive } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ChangeEvent } from "react";

import { ElevationCancelledError, request } from "../../../api/client";
import { databaseKeys, databasesQuery, enginesQuery } from "../../../api/queries/databases";
import { DEFAULT_IMPORT_MAX_BYTES, dumpInspectionQuery, importLimitQuery } from "../../../api/queries/dumpImport";
import type { DumpFormat } from "../../../api/queries/dumpImport";
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
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Progress } from "../../../components/ui/Progress";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { creatableEngines } from "../CreateDatabaseDialog";
import { engineFamilyOf } from "../engineKey";
import { engineName, instanceLabel } from "../engines";
import { JobLogPanel } from "../../jobs/JobLogPanel";
import { jobState } from "../jobs";
import type { Accepted } from "../jobs";
import { clearStoredUpload, discardUpload, readStoredUpload, writeStoredUpload } from "./storedUpload";
import type { StoredUpload } from "./storedUpload";
import { uploadApi } from "./uploadApi";
import { DUMP_EXTENSIONS, DumpUploader, UploadMismatchError, fileProblem } from "./uploader";
import type { FileProblem, UploadSnapshot } from "./uploader";

type Step = "dump" | "options" | "import";

const NAME = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const FILE_INPUT = "dump-import-file";

/** The kinds of file the server says it found, as words; a kind this console does not know reads as unknown. */
function kindWords(t: T, kind: string): string {
  switch (kind) {
    case "pg-custom":
      return t("dumpImport.format.kind.pg-custom");
    case "pg-tar":
      return t("dumpImport.format.kind.pg-tar");
    case "sql":
      return t("dumpImport.format.kind.sql");
    case "tar-sql":
      return t("dumpImport.format.kind.tar-sql");
    case "mongo-archive":
      return t("dumpImport.format.kind.mongo-archive");
    case "mongo-tree":
      return t("dumpImport.format.kind.mongo-tree");
    default:
      return t("dumpImport.format.kind.unknown");
  }
}

function compressionWords(t: T, compression: string): string | null {
  switch (compression) {
    case "none":
      return t("dumpImport.format.compression.none");
    case "gzip":
      return t("dumpImport.format.compression.gzip");
    case "bz2":
      return t("dumpImport.format.compression.bz2");
    case "xz":
      return t("dumpImport.format.compression.xz");
    case "zip":
      return t("dumpImport.format.compression.zip");
    default:
      return null;
  }
}

/** What the file is, in a sentence: "A SQL script, compressed with gzip". */
export function formatWords(t: T, format: Pick<DumpFormat, "kind" | "compression">): string {
  const kind = kindWords(t, format.kind);
  const compression = compressionWords(t, format.compression);
  return compression === null ? kind : `${kind}, ${compression}`;
}

function problemWords(t: T, problem: FileProblem, file: File, limit: number): string {
  if (problem === "extension") return t("dumpImport.file.extension", { name: file.name });
  if (problem === "empty") return t("dumpImport.file.empty", { name: file.name });
  return t("dumpImport.file.tooLarge", { name: file.name, size: formatBytes(file.size, t.locale), limit: formatBytes(limit, t.locale) });
}

/** The uploader's snapshot, as a React value. */
function useUpload(uploader: DumpUploader): UploadSnapshot {
  const subscribe = useCallback((listener: () => void) => uploader.subscribe(listener), [uploader]);
  return useSyncExternalStore(subscribe, () => uploader.snapshot);
}

function UploadProgress({ upload, onRetry, onCancel }: { upload: UploadSnapshot; onRetry: () => void; onCancel: () => void }) {
  const t = useT();
  const percent = upload.size > 0 ? Math.min(100, Math.floor((upload.received / upload.size) * 100)) : 0;
  const working = upload.phase === "starting" || upload.phase === "uploading" || upload.phase === "waiting" || upload.phase === "completing";
  const label =
    upload.phase === "done"
      ? t("dumpImport.upload.done")
      : upload.phase === "completing"
        ? t("dumpImport.upload.completing")
        : upload.phase === "starting"
          ? t("dumpImport.upload.starting")
          : t("dumpImport.upload.uploading");
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 items-center gap-2 text-13">
        <FileArchive aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
        <Mono tone="default" truncate>
          {upload.fileName}
        </Mono>
        <span className="shrink-0 text-fg-muted">{formatBytes(upload.size, t.locale)}</span>
      </div>
      <Progress value={upload.phase === "done" ? 100 : percent} label={label} />
      <p className="text-12 text-fg-muted tabular-nums">{t("dumpImport.upload.progress", { received: formatBytes(upload.received, t.locale), size: formatBytes(upload.size, t.locale) })}</p>
      {upload.phase === "waiting" ? (
        <Notice tone="warning" live>
          {upload.retryInMs !== null && upload.retryInMs >= 1000
            ? t("dumpImport.upload.waiting", { seconds: Math.round(upload.retryInMs / 1000), attempt: upload.attempt })
            : t("dumpImport.upload.waitingNow", { attempt: upload.attempt })}
        </Notice>
      ) : null}
      {upload.phase === "paused" ? (
        <Notice tone="warning" live>
          {t("dumpImport.upload.paused", { received: formatBytes(upload.received, t.locale) })}
        </Notice>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        {upload.phase === "waiting" || upload.phase === "paused" ? (
          <Button size="sm" onClick={onRetry}>
            {t("dumpImport.upload.retry")}
          </Button>
        ) : null}
        {working || upload.phase === "paused" ? (
          <Button size="sm" variant="ghost" onClick={onCancel}>
            {t("dumpImport.upload.cancel")}
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** The result of the job as the server reports it: what was created, replaced and kept. */
interface ImportResult {
  created?: boolean;
  replaced?: boolean;
  safety_copy?: string | null;
  notes?: string[];
  format?: { kind: string; compression: string };
}

function resultOf(result: unknown): ImportResult {
  return typeof result === "object" && result !== null ? result : {};
}

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
 * Importing a dump from the browser, in three steps: the dump (where it goes, the file, sent in
 * chunks that survive a dropped connection, and what the server found it to be), how it is
 * loaded (over the data there, or replacing it, with the owners it names or not) and the job,
 * its output streaming as it runs. Nothing is changed before the last button, which asks
 * "Confirm it's you" like every restore.
 */
export function ImportDumpDialog({ open, onOpenChange, engine: presetEngine, database: presetDatabase, onQueued }: ImportDumpDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const fixed = presetEngine !== undefined && presetDatabase !== undefined;
  const engines = useQuery({ ...enginesQuery(), enabled: open && !fixed });
  const databases = useQuery({ ...databasesQuery(), enabled: open });
  const limitQuery = useQuery({ ...importLimitQuery(), enabled: open });
  const limit = limitQuery.data ?? DEFAULT_IMPORT_MAX_BYTES;
  const choices = creatableEngines(engines.data?.engines ?? []);

  const [step, setStep] = useState<Step>("dump");
  const [pickedEngine, setPickedEngine] = useState<string | null>(null);
  const [typedDatabase, setTypedDatabase] = useState("");
  const engine = presetEngine ?? pickedEngine ?? (choices.length === 1 ? (choices[0]?.name ?? null) : null);
  const database = presetDatabase ?? typedDatabase.trim();
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [replace, setReplace] = useState(false);
  const [keepOwners, setKeepOwners] = useState(false);
  const [safety, setSafety] = useState(true);
  const [typed, setTyped] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"engine" | "database", string>>>({});
  // Read once, when the dialog is opened (it is drawn only while open).
  const [stored, setStored] = useState<StoredUpload | null>(() => readStoredUpload());
  const followed = useFollowedJob();
  const fileRef = useRef<HTMLInputElement>(null);
  const databaseRef = useRef<HTMLInputElement>(null);
  const typedRef = useRef<HTMLInputElement>(null);

  const uploader = useMemo(() => new DumpUploader(uploadApi), []);
  const upload = useUpload(uploader);
  const transferring = upload.phase === "starting" || upload.phase === "uploading" || upload.phase === "waiting" || upload.phase === "paused" || upload.phase === "completing";

  // An upload this browser left unfinished: the server still holds what arrived.
  const held = useQuery({
    queryKey: ["databases", "imports", "held", stored?.id ?? ""],
    queryFn: async ({ signal }) => {
      try {
        return await request("get", "/api/databases/imports/uploads/{upload_id}", { params: { upload_id: stored?.id ?? "" }, signal });
      } catch (error: unknown) {
        // Gone from the server (swept, or never this account's): there is nothing to continue.
        clearStoredUpload();
        throw error;
      }
    },
    enabled: stored !== null && upload.phase === "idle",
    retry: false,
  });
  const resumable = stored !== null && held.data !== undefined && !held.data.complete ? held.data : null;
  useEffect(() => {
    if (upload.uploadId !== null && (upload.phase === "uploading" || upload.phase === "waiting" || upload.phase === "paused")) {
      writeStoredUpload({ id: upload.uploadId, name: upload.fileName, size: upload.size });
    } else if (upload.phase === "done" || upload.phase === "cancelled" || upload.phase === "failed") {
      clearStoredUpload();
    }
  }, [upload.phase, upload.uploadId, upload.fileName, upload.size]);

  // Asked while the dump is being chosen only: once the import runs it consumes the upload, and
  // asking again about a file that is gone would be an error nobody can act on.
  const inspection = useQuery({ ...dumpInspectionQuery(upload.uploadId ?? "", engine ?? ""), enabled: upload.phase === "done" && upload.uploadId !== null && engine !== null && step === "dump" });
  const exists = engine !== null && database !== "" && (databases.data?.databases ?? []).some((item) => item.engine === engine && item.name === database && !item.missing);
  const family = engine === null ? "" : engineFamilyOf(engine);
  const choice = choices.find((item) => item.name === engine);
  const engineLabel = choice !== undefined ? instanceLabel(t, choice) : engine !== null ? engineName(engine) : "";
  const loadable = inspection.data?.loadable;

  const begin = (chosen: File): void => {
    const problem = fileProblem(chosen, limit);
    setFile(chosen);
    if (problem !== null) {
      setFileError(problemWords(t, problem, chosen, limit));
      return;
    }
    setFileError(null);
    if (stored !== null && resumable !== null && stored.name === chosen.name && stored.size === chosen.size) {
      void uploader.continueUpload(stored.id, chosen);
    } else {
      if (stored !== null) void discardUpload(stored.id);
      void uploader.start(chosen);
    }
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>): void => {
    const chosen = event.target.files?.[0];
    event.target.value = "";
    if (chosen !== undefined) begin(chosen);
  };

  const importMutation = useMutation({
    mutationFn: () =>
      request("post", "/api/databases/imports", {
        body: { engine: engine ?? "", database, upload_id: upload.uploadId, path: null, replace, keep_owners: family === "postgresql" && keepOwners, safety_backup: safety },
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
    clearStoredUpload();
    void queryClient.invalidateQueries({ queryKey: databaseKeys.all });
  }, [job, queryClient]);

  const reset = (): void => {
    setStep("dump");
    setFile(null);
    setFileError(null);
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
    } else if (step !== "import" && upload.uploadId !== null && upload.phase !== "idle") {
      // A dump that was not imported does not stay on the server's disk.
      void uploader.cancel();
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
          ? transferring
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
    const sizeLimit = formatBytes(limit, t.locale);
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
            {engines.data !== undefined && choices.length === 0 ? <Notice tone="warning">{t("dumpImport.target.noEngine")}</Notice> : null}
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
        {upload.phase === "idle" || upload.phase === "failed" || upload.phase === "cancelled" ? (
          <>
            {stored !== null && resumable !== null && upload.phase === "idle" ? (
              <Notice
                tone="warning"
                title={t("dumpImport.resume.title")}
                action={
                  <Button
                    size="sm"
                    onClick={() => {
                      void discardUpload(stored.id);
                      clearStoredUpload();
                      setStored(null);
                    }}
                  >
                    {t("dumpImport.resume.discard")}
                  </Button>
                }
              >
                {t("dumpImport.resume.body", { name: stored.name, percent: `${String(Math.floor((resumable.received / Math.max(1, resumable.size)) * 100))}%` })}
              </Notice>
            ) : null}
            <Field
              label={t("dumpImport.file.label")}
              description={t("dumpImport.file.help", { limit: sizeLimit })}
              error={upload.phase === "failed" ? null : fileError}
            >
              <BaseInput
                ref={fileRef}
                id={FILE_INPUT}
                type="file"
                accept={DUMP_EXTENSIONS.map((extension) => `.${extension}`).join(",")}
                onChange={onFile}
                className="max-w-full rounded-control text-13 text-fg-muted file:mr-3 file:h-control-md file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover"
              />
            </Field>
            {fileError !== null && file !== null && fileProblem(file, limit) === "tooLarge" ? <p className="text-12 text-fg-muted">{t("dumpImport.file.tooLargeHint")}</p> : null}
            {upload.phase === "cancelled" ? <p className="text-13 text-fg-muted">{t("dumpImport.upload.cancelled")}</p> : null}
            {upload.phase === "failed" ? (
              <ErrorBlock
                live
                compact
                error={upload.error}
                title={upload.error instanceof UploadMismatchError ? t("dumpImport.file.mismatch", { name: upload.error.heldName, size: formatBytes(upload.error.heldSize, t.locale) }) : t("dumpImport.upload.failed")}
              />
            ) : null}
          </>
        ) : (
          <UploadProgress upload={upload} onRetry={() => uploader.retryNow()} onCancel={() => void uploader.cancel()} />
        )}
        {upload.phase === "done" ? (
          <div className="flex min-w-0 flex-col gap-2" role="status">
            <p className="text-13 font-medium text-fg">{t("dumpImport.format.title")}</p>
            <p className="text-13 text-fg">{upload.format !== null ? formatWords(t, upload.format) : kindWords(t, "unknown")}</p>
            {upload.format !== null ? <p className="text-12 text-fg-muted">{upload.format.description}</p> : null}
            {inspection.isError ? <ErrorBlock compact error={inspection.error} title={t("dumpImport.format.inspectFailed")} onRetry={() => void inspection.refetch()} /> : null}
            {loadable === true ? <p className="text-13 text-fg">{t("dumpImport.format.loadable", { engine: engineLabel })}</p> : null}
            {loadable === false ? (
              <Notice tone="error" title={t("dumpImport.format.notLoadable", { engine: engineLabel })}>
                {inspection.data?.reason ?? ""}
              </Notice>
            ) : null}
            <div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  void uploader.cancel();
                  setFile(null);
                }}
              >
                {t("dumpImport.upload.another")}
              </Button>
            </div>
          </div>
        ) : null}
        {fixed || engine !== null ? <CommandHint label={t("dumpImport.limit.path", { limit: sizeLimit })} command={`noust db import ${database === "" ? "NAME" : database} /srv/dumps/dump.sql.gz -e ${engine ?? "postgresql"}`} /> : null}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{ label: t("dumpImport.cancel"), onClick: close }}
        next={{ label: t("dumpImport.next"), onClick: nextFromDump }}
        {...(missingOnDump !== undefined ? { missing: missingOnDump, onMissing: () => (upload.phase === "idle" ? fileRef.current?.focus() : undefined) } : {})}
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
          label: t("dumpImport.back"),
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
    const result = job !== null && state === "succeeded" ? resultOf(job.result) : {};
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
        result={
          <ul className="flex flex-col gap-1 text-13 text-fg">
            {result.created === true ? <li>{t("dumpImport.job.result.created", { name: database })}</li> : null}
            {result.replaced === true ? <li>{t("dumpImport.job.result.replaced", { name: database })}</li> : null}
            {result.safety_copy !== undefined && result.safety_copy !== null ? <li>{t("dumpImport.job.result.safety", { file: result.safety_copy })}</li> : null}
            {result.format !== undefined ? <li>{t("dumpImport.job.result.format", { format: formatWords(t, result.format) })}</li> : null}
            {(result.notes ?? []).map((note) => (
              <li key={note} className="text-fg-muted" translate="no">
                {note}
              </li>
            ))}
          </ul>
        }
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
          {finished ? t("dumpImport.done") : t("dumpImport.close")}
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
