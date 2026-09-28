import { CircleAlert, FileCheck2 } from "lucide-react";
import { useId } from "react";
import type { ChangeEvent } from "react";

import type { AppExportDocument } from "../../api/queries/appImport";
import { CommandHint } from "../../components/page/CommandHint";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import type { AppTypeOption } from "./wizard";
import { typeName } from "./wizard";

export interface LoadedExport {
  fileName: string;
  document: AppExportDocument;
}

export interface ImportFileProps {
  loaded: LoadedExport | null;
  /** Why the chosen file cannot be imported, in the console's words. */
  problem: string | null;
  types: readonly AppTypeOption[];
  onFile: (file: File) => void;
  onContinue: () => void;
}

/**
 * The export to import: a file chosen here and read in the browser, checked just enough to say
 * clearly when it is not an export or is too new, and named once it is read.
 */
export function ImportFile({ loaded, problem, types, onFile, onContinue }: ImportFileProps) {
  const t = useT();
  const inputId = useId();
  const descriptionId = useId();
  const errorId = useId();
  const onChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.target.files?.[0];
    if (file !== undefined) onFile(file);
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1.5">
        <label htmlFor={inputId} className="text-13 font-medium text-fg">
          {t("newApp.importApp.file")}
        </label>
        <input
          id={inputId}
          type="file"
          accept=".json,application/json"
          onChange={onChange}
          aria-describedby={problem !== null ? `${descriptionId} ${errorId}` : descriptionId}
          aria-invalid={problem !== null}
          className={cx(
            "max-w-full rounded-control text-13 text-fg-muted",
            "file:mr-3 file:cursor-pointer file:rounded-control file:border file:border-border-strong file:bg-surface file:px-3 file:py-1 file:text-13 file:font-medium file:text-fg hover:file:bg-surface-hover",
            "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus",
          )}
        />
        <p id={descriptionId} className="text-12 text-pretty text-fg-muted">
          {t("newApp.importApp.fileDescription")}
        </p>
        {problem !== null ? (
          <p id={errorId} role="alert" className="flex items-start gap-1.5 text-13 text-fail">
            <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            <span>{problem}</span>
          </p>
        ) : null}
      </div>

      {loaded !== null && problem === null ? (
        <div role="status" className="flex min-w-0 items-start gap-3 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
          <FileCheck2 aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
          <div className="flex min-w-0 flex-col gap-0.5">
            <p className="text-13 font-medium text-fg">
              {t.rich("newApp.importApp.loaded", {
                file: (
                  <code translate="no" className="text-12">
                    {loaded.fileName}
                  </code>
                ),
              })}
            </p>
            <p className="text-13 text-fg-muted">
              {t("newApp.importApp.loadedDetail", { domain: loaded.document.app.domain, type: typeName(types, loaded.document.app.app_type) })}
            </p>
          </div>
        </div>
      ) : null}

      {loaded !== null && problem === null ? (
        <div>
          <Button variant="primary" onClick={onContinue}>
            {t("newApp.source.continue")}
          </Button>
        </div>
      ) : null}

      <CommandHint command="wasm app import app.wasm-app.json --domain example.com" label={t("newApp.source.terminal")} />
    </div>
  );
}
