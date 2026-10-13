import { SegmentedControl } from "../../components/page/SegmentedControl";
import { useT } from "../../i18n";
import { ImportFile } from "./ImportFile";
import type { ImportFileProps } from "./ImportFile";
import { ImportRemote, PLATFORM_NAME } from "./ImportRemote";
import type { ImportRemoteProps } from "./ImportRemote";

/** Where an import starts: a file `noust app export` wrote, or another platform's server. */
export type ImportFrom = "file" | ImportRemoteProps["platform"];

export interface ImportSourceProps extends ImportFileProps {
  from: ImportFrom;
  onFrom: (from: ImportFrom) => void;
  onRead: ImportRemoteProps["onRead"];
}

/**
 * The first step of an import. The file and the other platforms' servers end in the same place, an
 * export document, so the review that follows is one: this only chooses how it is obtained.
 */
export function ImportSource({ from, onFrom, onRead, ...file }: ImportSourceProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-6">
      <SegmentedControl<ImportFrom>
        label={t("newApp.importRemote.from")}
        options={[
          { value: "file", label: t("newApp.importRemote.file") },
          { value: "coolify", label: PLATFORM_NAME.coolify },
          { value: "dokploy", label: PLATFORM_NAME.dokploy },
        ]}
        value={from}
        onValueChange={onFrom}
      />
      {from === "file" ? <ImportFile {...file} /> : <ImportRemote key={from} platform={from} onRead={onRead} />}
    </div>
  );
}
