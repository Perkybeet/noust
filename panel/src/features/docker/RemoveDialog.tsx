/**
 * The one question asked before something of Docker's is removed, with the friction its reach deserves (docs/DESIGN.md 6.2): a
 * container, an image or a network can be made again, so one question; a volume holds data that is gone for good, so its name is
 * typed, and the dialog says what is inside, how much it holds, whose it is and who uses it, because the name is not what is lost.
 */

import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { Can } from "../../components/page/permissions";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { useRemove } from "./actions";
import type { RemoveTarget } from "./actions";
import { OwnerCell } from "./pieces";
import type { DockerOwner } from "../../api/queries/docker";

/** What is about to be removed: the target the server is asked to remove, and what the dialog says of it. */
export interface RemoveSubject {
  target: RemoveTarget;
  /** The name as it is shown (a container's, an image's reference, a volume's), which a volume's confirmation is typed from. */
  name: string;
  owner: DockerOwner;
  /** The size it holds, when it is known. */
  sizeBytes?: number | null | undefined;
  /** The containers that use it, for a volume, an image or a network. */
  usedBy?: readonly string[] | undefined;
  /** A container that runs is stopped first: said, because that interrupts what it serves. */
  running?: boolean | undefined;
  /** A volume Docker made for a container that did not name one. */
  anonymous?: boolean | undefined;
}

export function RemoveDialog({ subject, onClose }: { subject: RemoveSubject | null; onClose: () => void }) {
  const t = useT();
  const { node } = useNode();
  const remove = useRemove();
  // The dialog stays mounted while it closes, so what it says is the last thing it was asked about.
  if (subject === null) return null;
  const kind = subject.target.kind;
  const size = typeof subject.sizeBytes === "number" ? formatBytes(subject.sizeBytes, t.locale) : null;
  const used = subject.usedBy ?? [];
  const facts: KeyValueItem[] = [
    { label: t("docker.remove.owner"), value: <OwnerCell owner={subject.owner} /> },
    ...(size !== null ? [{ label: t("docker.remove.size"), value: size, mono: true }] : []),
    ...(kind === "volume" ? [{ label: t("docker.remove.usedBy"), value: used.length === 0 ? t("docker.remove.usedByNobody") : used.join(", "), mono: used.length > 0 }] : []),
  ];
  const description =
    kind === "volume"
      ? t("docker.remove.volume.description", { size: size ?? "" })
      : kind === "container"
        ? subject.running === true
          ? t("docker.remove.container.descriptionRunning")
          : t("docker.remove.container.description")
        : kind === "image"
          ? t("docker.remove.image.description")
          : t("docker.remove.network.description");
  const common = {
    open: true,
    onOpenChange: (open: boolean) => {
      if (!open) onClose();
    },
    title: t(`docker.remove.${kind}.title`, { name: subject.name }),
    destructive: true,
    ...(node !== null ? { server: node } : {}),
    actionLabel: t(`docker.remove.${kind}.action`),
    description,
    // A refusal is shown in the dialog, verbatim, and the dialog stays open: it is not said a second time as a toast.
    onConfirm: async () => {
      await remove.mutateAsync(subject.target.kind === "container" ? { ...subject.target, force: subject.running === true } : subject.target);
    },
  } as const;
  // What it is, whose it is and who uses it are the dialog's body, apart from its question: the question is a paragraph and a list does not go in one.
  const body = <KeyValueList items={facts} />;
  return (
    <Can permission="docker.manage">
      {kind === "volume" ? (
        <ConfirmDialog {...common} friction="type" confirmText={subject.name} size="md">
          {body}
        </ConfirmDialog>
      ) : (
        <ConfirmDialog {...common} friction="simple" size="sm">
          {body}
        </ConfirmDialog>
      )}
    </Can>
  );
}
