import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import type { RecordConflict } from "./dnsConflict";

/**
 * The question Noust asks before it touches a DNS record somebody else made: it names the
 * record and what it holds, says that only its content changes, and does nothing until the
 * operator says so. Declining leaves the record as it was.
 */
export function DnsConflictDialog({
  conflict,
  onClose,
  onReplace,
}: {
  conflict: RecordConflict | null;
  onClose: () => void;
  /** Replaces the record, naming it and what it held when the operator looked. */
  onReplace: (conflict: RecordConflict) => Promise<void>;
}) {
  const t = useT();
  const { node } = useNode();
  return (
    <ConfirmDialog
      open={conflict !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      friction="simple"
      server={node}
      title={conflict === null ? "" : t("network.dnsConflict.title", { name: conflict.hostname })}
      description={
        conflict === null
          ? ""
          : t.rich("network.dnsConflict.description", {
              current: <Mono>{conflict.current}</Mono>,
              wanted: <Mono>{conflict.wanted}</Mono>,
              id: <Mono>{conflict.record_id}</Mono>,
            })
      }
      actionLabel={t("network.dnsConflict.replace")}
      onConfirm={async () => {
        if (conflict !== null) await onReplace(conflict);
      }}
    />
  );
}
