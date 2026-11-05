/**
 * Choosing a dump and sending it to the server, as one piece the two things that load a dump share: "New database" (a database
 * that starts from a dump) and "Import a dump" over a database that exists (its Backups). The file goes in chunks that survive a
 * dropped connection, what arrived is remembered so the next visit can go on, and the server says what the file is and whether
 * the engine it is for can load it. Nothing in it makes a database: whoever uses it starts the import with the upload's id.
 */

import { Input as BaseInput } from "@base-ui/react/input";
import { useQuery } from "@tanstack/react-query";
import { FileArchive } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { ChangeEvent, ReactNode, RefObject } from "react";

import { request } from "../../../api/client";
import { DEFAULT_IMPORT_MAX_BYTES, dumpInspectionQuery, importLimitQuery } from "../../../api/queries/dumpImport";
import type { DumpFormat } from "../../../api/queries/dumpImport";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Progress } from "../../../components/ui/Progress";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { clearStoredUpload, discardUpload, readStoredUpload, writeStoredUpload } from "./storedUpload";
import type { StoredUpload } from "./storedUpload";
import { uploadApi } from "./uploadApi";
import { DUMP_EXTENSIONS, DumpUploader, UploadMismatchError, fileProblem } from "./uploader";
import type { FileProblem, UploadSnapshot } from "./uploader";

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
        <InlineMessage live>
          {upload.retryInMs !== null && upload.retryInMs >= 1000
            ? t("dumpImport.upload.waiting", { seconds: Math.round(upload.retryInMs / 1000), attempt: upload.attempt })
            : t("dumpImport.upload.waitingNow", { attempt: upload.attempt })}
        </InlineMessage>
      ) : null}
      {upload.phase === "paused" ? <InlineMessage live>{t("dumpImport.upload.paused", { received: formatBytes(upload.received, t.locale) })}</InlineMessage> : null}
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

export interface DumpFile {
  upload: UploadSnapshot;
  /** The upload is going, or paused half-way: the dialog around it must not be closed on it without a word. */
  transferring: boolean;
  /** The size limit the server allows, in bytes. */
  limit: number;
  /** Whether the server says the engine can load the file: undefined until it has been asked. */
  loadable: boolean | undefined;
  /** Throws away what was uploaded and not used, on the server. */
  discard: () => void;
  /** Forget the file, so another can be chosen. */
  another: () => void;
  field: ReactNode;
}

/**
 * The upload of one dump and what the server found it to be. `engine` is the engine key the dump is for (the server judges
 * whether it can load there), `inspect` says when to ask (only while the dump is being chosen: once the import runs it consumes
 * the upload, and asking about a file that is gone is an error nobody can act on), `focusRef` is where a refusal to go on puts
 * the focus.
 */
export function useDumpFile({ enabled, engine, engineLabel, inspect }: { enabled: boolean; engine: string | null; engineLabel: string; inspect: boolean }): DumpFile & {
  fileRef: RefObject<HTMLInputElement | null>;
} {
  const t = useT();
  const limitQuery = useQuery({ ...importLimitQuery(), enabled });
  const limit = limitQuery.data ?? DEFAULT_IMPORT_MAX_BYTES;
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  // Read once, when the dialog is opened (it is drawn only while open).
  const [stored, setStored] = useState<StoredUpload | null>(() => readStoredUpload());
  const fileRef = useRef<HTMLInputElement>(null);
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
    enabled: enabled && stored !== null && upload.phase === "idle",
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

  const inspection = useQuery({ ...dumpInspectionQuery(upload.uploadId ?? "", engine ?? ""), enabled: upload.phase === "done" && upload.uploadId !== null && engine !== null && inspect });
  const loadable = inspection.data?.loadable ?? undefined;

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

  const sizeLimit = formatBytes(limit, t.locale);
  const field = (
    <div className="flex min-w-0 flex-col gap-4">
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
          <Field label={t("dumpImport.file.label")} description={t("dumpImport.file.help", { limit: sizeLimit })} error={upload.phase === "failed" ? null : fileError}>
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
    </div>
  );

  return {
    upload,
    transferring,
    limit,
    loadable,
    fileRef,
    field,
    discard: () => {
      // A dump that was not imported does not stay on the server's disk.
      if (upload.uploadId !== null && upload.phase !== "idle") void uploader.cancel();
    },
    another: () => {
      void uploader.cancel();
      setFile(null);
    },
  };
}

/** After an import ends: what was uploaded is spent, and what the browser kept of it goes. */
export function spendUpload(): void {
  clearStoredUpload();
}
