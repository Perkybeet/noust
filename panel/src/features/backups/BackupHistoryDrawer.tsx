import { Play } from "lucide-react";

import { CommandHint } from "../../components/page/CommandHint";
import { Button } from "../../components/ui/Button";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { BackupsTable } from "./BackupsTable";
import type { BackupActionHandlers } from "./BackupsTable";
import type { CoverageRow } from "./coverage";

export interface BackupHistoryDrawerProps extends BackupActionHandlers {
  /** The application whose backups are shown; closed when null. */
  row: CoverageRow | null;
  /** The domain asked for, while its row is not known yet (the list is still loading). */
  domain: string | null;
  loading: boolean;
  onClose: () => void;
  onBackUp: (domain: string) => void;
}

/**
 * One application's backups with the list still behind it: when each was made, what it holds,
 * its size and its last integrity check, and what can be done with each.
 */
export function BackupHistoryDrawer({ row, domain, loading, onClose, onBackUp, ...handlers }: BackupHistoryDrawerProps) {
  const t = useT();
  const name = row?.domain ?? domain ?? "";
  const backups = row?.backups ?? [];
  return (
    <Drawer
      open={domain !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("backups.history.title", { domain: name })}
      description={
        row === null && loading
          ? t("backups.history.loading")
          : t("backups.history.description", { count: backups.length, size: formatBytes(row?.size ?? 0, t.locale) })
      }
      {...(row?.deployed === true
        ? {
            footer: (
              <Button icon={<Play aria-hidden="true" />} onClick={() => onBackUp(name)}>
                {t("backups.history.backUpNow")}
              </Button>
            ),
          }
        : {})}
    >
      {domain === null ? null : (
        <div className="flex flex-col gap-4">
          <BackupsTable
            backups={backups}
            caption={t("backups.history.caption", { domain: name })}
            loading={loading && row === null}
            skeletonRows={3}
            empty={<EmptyState variant="inline" title={t("backups.history.empty", { domain: name })} />}
            {...handlers}
          />
          <CommandHint command={`noust backup list ${name}`} label={t("backups.common.fromTerminal")} />
        </div>
      )}
    </Drawer>
  );
}
