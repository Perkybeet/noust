import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Disclosure } from "../../../components/ui/Disclosure";
import { Field } from "../../../components/ui/Field";
import { Notice } from "../../../components/ui/Notice";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import { splitErrors } from "../../settings/formErrors";
import { pasteDockerfile } from "./sourceUploadApi";
import type { UploadedArchive } from "./ArchiveUpload";

/**
 * A Dockerfile typed or pasted in, for an application that has no repository. It becomes a
 * project of that one file on the server, so the build context holds nothing else: a `COPY` finds
 * nothing, and the Dockerfile fetches what it needs itself (`ADD` of a URL, `RUN git clone`, a
 * base image that has the program). What it hands back (`upload:<id>`) is a source like any other.
 */
export function PasteDockerfile({ onStored, onCleared, disabled = false }: { onStored: (done: UploadedArchive) => void; onCleared?: () => void; disabled?: boolean }) {
  const t = useT();
  const [text, setText] = useState("");
  const [stored, setStored] = useState(false);
  const store = useMutation({
    mutationFn: () => pasteDockerfile(text),
    onSuccess: (result) => {
      setStored(true);
      onStored({ source: result.source ?? "", fileName: result.file_name, files: result.files ?? 1 });
    },
  });
  const split = splitErrors(store.error, ["content"] as const);

  const edit = (): void => {
    setStored(false);
    store.reset();
    onCleared?.();
  };

  return (
    <Disclosure label={t("pasteDockerfile.open")}>
      {stored ? (
        <Notice
          tone="success"
          live
          title={t("pasteDockerfile.stored")}
          action={
            <Button size="sm" onClick={edit}>
              {t("pasteDockerfile.edit")}
            </Button>
          }
        >
          {t("pasteDockerfile.storedBody")}
        </Notice>
      ) : (
        // Not a form: this sits inside the source step's own, and forms do not nest.
        <div className="flex flex-col gap-3">
          <Field label={t("pasteDockerfile.label")} description={t("pasteDockerfile.help")} error={split.fields.content}>
            <Textarea mono rows={10} value={text} placeholder={"FROM node:22-alpine\nRUN apk add --no-cache git\nCMD [\"node\", \"-v\"]"} disabled={disabled} onChange={(event) => setText(event.target.value)} spellCheck={false} />
          </Field>
          {store.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("pasteDockerfile.failed")} /> : null}
          <div>
            <Button size="sm" loading={store.isPending} disabled={disabled || text.trim() === ""} onClick={() => store.mutate()}>
              {t("pasteDockerfile.use")}
            </Button>
          </div>
        </div>
      )}
    </Disclosure>
  );
}
