import { Input as BaseInput } from "@base-ui/react/input";
import { FileArchive } from "lucide-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { ChangeEvent } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Field } from "../../../components/ui/Field";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Progress } from "../../../components/ui/Progress";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { DumpUploader } from "../../databases/import/uploader";
import type { UploadSnapshot } from "../../databases/import/uploader";
import { ARCHIVE_CHUNK_BYTES, ARCHIVE_ENDINGS, isArchiveName, makeSourceUploadApi } from "./sourceUploadApi";
import { RotateCw, Upload } from "lucide-react";

/** What a finished upload hands to the form that wanted a source. */
export interface UploadedArchive {
  /** `upload:<id>`: what an application's source is set to. */
  source: string;
  fileName: string;
  /** Files the archive held. */
  files: number;
}

interface Session {
  uploader: DumpUploader;
  finished: ReturnType<typeof makeSourceUploadApi>["finished"];
}

function newSession(): Session {
  const { api, finished } = makeSourceUploadApi();
  return { uploader: new DumpUploader(api, { chunkBytes: ARCHIVE_CHUNK_BYTES }), finished };
}

/** The uploader's snapshot, as a React value. */
function useSnapshot(uploader: DumpUploader): UploadSnapshot {
  const subscribe = useCallback((listener: () => void) => uploader.subscribe(listener), [uploader]);
  return useSyncExternalStore(subscribe, () => uploader.snapshot);
}

/**
 * Sending the code of an application as an archive, from the browser: pick a .zip or a tarball
 * and it is sent in chunks that survive a dropped connection, then extracted once on the server
 * with the checks every archive gets. What it hands back (`upload:<id>`) is a source like any
 * other. An archive the server refuses (a path out of the project, a link that points out, no
 * files) is shown with the server's own words and nothing is kept.
 */
export function ArchiveUpload({ onUploaded, onCleared, disabled = false }: { onUploaded: (done: UploadedArchive) => void; onCleared?: () => void; disabled?: boolean }) {
  const t = useT();
  const [session, setSession] = useState<Session>(newSession);
  const upload = useSnapshot(session.uploader);
  const [nameError, setNameError] = useState<string | null>(null);
  const handed = useRef<string | null>(null);

  useEffect(() => {
    if (upload.phase !== "done" || upload.uploadId === null || handed.current === upload.uploadId) return;
    const answer = session.finished();
    if (answer?.source == null) return;
    handed.current = upload.uploadId;
    onUploaded({ source: answer.source, fileName: upload.fileName, files: answer.files ?? 0 });
  }, [upload.phase, upload.uploadId, upload.fileName, session, onUploaded]);

  const onFile = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file === undefined) return;
    if (!isArchiveName(file.name)) {
      setNameError(t("archiveUpload.extension", { name: file.name }));
      return;
    }
    if (file.size <= 0) {
      setNameError(t("archiveUpload.empty", { name: file.name }));
      return;
    }
    setNameError(null);
    void session.uploader.start(file);
  };

  const again = (): void => {
    setSession(newSession());
    setNameError(null);
    handed.current = null;
    onCleared?.();
  };

  const working = upload.phase === "starting" || upload.phase === "uploading" || upload.phase === "waiting" || upload.phase === "completing";
  const percent = upload.size > 0 ? Math.min(100, Math.floor((upload.received / upload.size) * 100)) : 0;

  if (upload.phase === "done") {
    return (
      <Notice
        tone="success"
        live
        title={t("archiveUpload.done", { name: upload.fileName })}
        action={
          <Button size="sm" icon={<Upload aria-hidden="true" />} onClick={again}>
            {t("archiveUpload.another")}
          </Button>
        }
      >
        {t("archiveUpload.doneBody", { files: session.finished()?.files ?? 0 })}
      </Notice>
    );
  }
  if (working || upload.phase === "paused") {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 items-center gap-2 text-13">
          <FileArchive aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
          <Mono tone="default" truncate>
            {upload.fileName}
          </Mono>
          <span className="shrink-0 text-fg-muted">{formatBytes(upload.size, t.locale)}</span>
        </div>
        <Progress value={percent} label={upload.phase === "completing" ? t("archiveUpload.completing") : t("archiveUpload.uploading")} />
        <p className="text-12 text-fg-muted tabular-nums">{t("archiveUpload.progress", { received: formatBytes(upload.received, t.locale), size: formatBytes(upload.size, t.locale) })}</p>
        {upload.phase === "waiting" ? (
          <Notice tone="warning" live>
            {t("archiveUpload.waiting", { attempt: upload.attempt })}
          </Notice>
        ) : null}
        {upload.phase === "paused" ? (
          <Notice tone="warning" live>
            {t("archiveUpload.paused", { received: formatBytes(upload.received, t.locale) })}
          </Notice>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {upload.phase === "waiting" || upload.phase === "paused" ? (
            <Button size="sm" icon={<RotateCw aria-hidden="true" />} onClick={() => session.uploader.retryNow()}>
              {t("common.words.retry")}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={() => void session.uploader.cancel()}>
            {t("archiveUpload.cancel")}
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <Field label={t("archiveUpload.label")} description={t("archiveUpload.help")} error={upload.phase === "failed" ? undefined : (nameError ?? undefined)}>
        <BaseInput
          type="file"
          accept={ARCHIVE_ENDINGS.join(",")}
          disabled={disabled}
          onChange={onFile}
          className="max-w-full rounded-control text-13 text-fg-muted file:mr-3 file:h-control-md file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover"
        />
      </Field>
      {upload.phase === "cancelled" ? <p className="text-13 text-fg-muted">{t("archiveUpload.cancelled")}</p> : null}
      {upload.phase === "failed" ? <ErrorBlock live compact error={upload.error} title={t("archiveUpload.failed")} /> : null}
    </div>
  );
}
