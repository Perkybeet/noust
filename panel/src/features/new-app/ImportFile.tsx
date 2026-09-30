import { Input as BaseInput } from "@base-ui/react/input";
import { FileCheck2 } from "lucide-react";
import type { ChangeEvent } from "react";

import type { AppExportDocument } from "../../api/queries/appImport";
import { CommandHint } from "../../components/page/CommandHint";
import { Card } from "../../components/ui/Card";
import { Field } from "../../components/ui/Field";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import type { AppTypeOption } from "./wizard";
import { typeName } from "./wizard";

export interface LoadedExport {
  fileName: string;
  document: AppExportDocument;
}

/** The id of the file control, so the wizard's Continue can point at it when no file is read yet. */
export const EXPORT_FILE_INPUT = "new-app-export-file";

export interface ImportFileProps {
  loaded: LoadedExport | null;
  /** Why the chosen file cannot be imported, in the console's words. */
  problem: string | null;
  types: readonly AppTypeOption[];
  onFile: (file: File) => void;
}

/**
 * The export to import: a file chosen here and read in the browser, checked just enough to say
 * clearly when it is not an export or is too new, and named once it is read. The wizard's own
 * Continue goes on from here.
 */
export function ImportFile({ loaded, problem, types, onFile }: ImportFileProps) {
  const t = useT();
  const onChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file !== undefined) onFile(file);
  };

  return (
    <div className="flex flex-col gap-6">
      <Field label={t("newApp.importApp.file")} description={t("newApp.importApp.fileDescription")} error={problem}>
        <BaseInput
          id={EXPORT_FILE_INPUT}
          type="file"
          accept=".json,application/json"
          onChange={onChange}
          className="max-w-full rounded-control text-13 text-fg-muted file:mr-3 file:h-control-md file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover"
        />
      </Field>

      {loaded !== null && problem === null ? (
        <Card padding="sm">
          <div role="status" className="flex min-w-0 items-start gap-3">
            <FileCheck2 aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
            <div className="flex min-w-0 flex-col gap-0.5 text-13">
              <p className="font-medium text-fg">{t.rich("newApp.importApp.loaded", { file: <Mono>{loaded.fileName}</Mono> })}</p>
              <p className="text-fg-muted">
                {t("newApp.importApp.loadedDetail", { domain: loaded.document.app.domain, type: typeName(types, loaded.document.app.app_type) })}
              </p>
            </div>
          </div>
        </Card>
      ) : null}

      <CommandHint command="noust app import app.wasm-app.json --domain example.com" label={t("newApp.source.terminal")} />
    </div>
  );
}
