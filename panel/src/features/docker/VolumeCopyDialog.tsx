/**
 * Copying a volume now, said as what it is: the console does not take the copy, the machine does, with the command the server says. The
 * copy can be taken of any volume whoever's it is, so the dialog is the same for an application's data, an engine's and what nobody of
 * Noust made; what it names is the volume, how long ago it was last copied and the command, set apart so it can be copied.
 */

import type { DockerVolume } from "../../api/queries/docker";
import { CommandHint } from "../../components/page/CommandHint";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";
import { volumeCopyOf } from "./model";

export function VolumeCopyDialog({ volume, onClose }: { volume: DockerVolume | null; onClose: () => void }) {
  const t = useT();
  if (volume === null) return null;
  const copy = volumeCopyOf(volume);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("docker.volumes.copy.title", { name: volume.name })}
      description={t("docker.volumes.copy.description")}
      footer={<Button onClick={onClose}>{t("common.words.close")}</Button>}
    >
      <div className="flex min-w-0 flex-col gap-3">
        {copy.lastAt === null ? (
          <p className="text-13 text-fg-muted">{t("docker.volumes.copy.never")}</p>
        ) : (
          <p className="text-13 text-fg-muted">{t.rich("docker.volumes.copy.last", { when: <RelativeTime key="when" value={copy.lastAt} /> })}</p>
        )}
        <CommandHint command={copy.via} reason="solo-terminal" />
      </div>
    </Dialog>
  );
}
