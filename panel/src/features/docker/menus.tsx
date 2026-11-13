/**
 * The menu at the end of a row of the zone: a container's, an image's, a volume's or a network's. What it offers is what the
 * server's `can` says works there (`model.ts` turns it into verbs), and what the owner does is one line that leads to the owner,
 * never a disabled copy of each verb (`ObjectMenu`, docs/DESIGN.md PM-1).
 */

import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import type { DockerContainer, DockerImage, DockerNetwork, DockerOwner, DockerStack, DockerVolume } from "../../api/queries/docker";
import { ObjectMenu } from "../../components/page/ObjectMenu";
import { Can, useCan } from "../../components/page/permissions";
import type { ObjectAction, ViaOwner } from "../../components/page/ObjectMenu";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS, VERB_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useContainerVerb, useStackVerb } from "./actions";
import { containerVerbs, imageVerbs, networkVerbs, ownerTarget, stackVerbs, volumeVerbs } from "./model";
import type { Verbs } from "./model";
import type { RemoveSubject } from "./RemoveDialog";

/** The one line that says the owner does the rest, with the words that fit what the owner is. */
function viaOwnerOf(owner: DockerOwner, t: T): ViaOwner | undefined {
  const target = ownerTarget(owner);
  if (target === undefined) return undefined;
  const database = owner.kind === "database";
  return {
    label: t("docker.menu.changeFrom", { name: owner.subject }),
    description: database ? t("docker.menu.viaEngine") : t("docker.menu.viaApplication"),
    icon: database ? <OBJECT_ICONS.database /> : <OBJECT_ICONS.app />,
    target,
  };
}

/** A container's menu: logs, a console, start, stop, restart and remove, as far as the server says they work for whose it is. */
export function ContainerMenu({ container, onRemove }: { container: DockerContainer; onRemove: (subject: RemoveSubject) => void }) {
  const t = useT();
  const navigate = useNavigate();
  const { node } = useNode();
  const start = useContainerVerb("start");
  const stop = useContainerVerb("stop");
  const restart = useContainerVerb("restart");
  const [stopping, setStopping] = useState(false);
  // What changes a container takes managing Docker; reading its logs does not. The server says it again, by whose the container is.
  const can = useCan();
  const manage = can("docker.manage");
  const run = (verb: "start" | "restart"): void => ({ start, restart })[verb].mutate(container.name);
  const verbs = containerVerbs(container);
  const open = (search?: { console: true }): void => {
    void navigate({ to: "/server/docker/containers/$ref", params: { ref: container.name }, ...(search !== undefined ? { search } : {}) });
  };
  const make = (verb: Verbs["direct"][number]): ObjectAction | null => {
    switch (verb) {
      case "logs":
        return { id: verb, label: t("docker.menu.logs"), icon: <OBJECT_ICONS.log />, onSelect: () => open() };
      case "console":
        return { id: verb, label: t("docker.menu.console"), icon: <OBJECT_ICONS.terminal />, onSelect: () => open({ console: true }) };
      case "restart":
        return { id: verb, label: t("common.words.restart"), icon: <VERB_ICONS.rebuild />, onSelect: () => run("restart") };
      case "stop":
        return { id: verb, label: t("common.words.stop"), icon: <VERB_ICONS.turnOff />, onSelect: () => setStopping(true) };
      case "start":
        return { id: verb, label: t("common.words.start"), icon: <VERB_ICONS.turnOn />, onSelect: () => run("start") };
      case "remove":
        return {
          id: verb,
          label: t("docker.menu.remove"),
          icon: <ICONS.delete />,
          destructive: true,
          onSelect: () => onRemove({ target: { kind: "container", ref: container.name, force: false }, name: container.name, owner: container.owner, running: container.state === "running" || container.state === "restarting" }),
        };
      default:
        // Updating is an image's verb: a container has none of it.
        return null;
    }
  };
  const actions = verbs.direct.filter((verb) => verb === "logs" || manage).map(make).filter((action): action is ObjectAction => action !== null);
  const via = verbs.viaOwner ? viaOwnerOf(container.owner, t) : undefined;
  return (
    <>
      <ObjectMenu name={container.name} actions={actions} viaOwner={via} />
      <Can permission="docker.manage">
        <ConfirmDialog
          friction="simple"
          open={stopping}
          onOpenChange={setStopping}
          title={t("docker.stop.title", { name: container.name })}
          description={t("docker.stop.description")}
          actionLabel={t("common.words.stop")}
          {...(node !== null ? { server: node } : {})}
          onConfirm={async () => {
            await stop.mutateAsync(container.name);
          }}
        />
      </Can>
    </>
  );
}

/** What an image's, a volume's or a network's menu draws: remove, and the owner's line. */
function removeOnly(verbs: Verbs, name: string, subject: RemoveSubject, owner: DockerOwner, onRemove: (subject: RemoveSubject) => void, t: T): ReactNode {
  const actions: ObjectAction[] = verbs.direct.includes("remove")
    ? [{ id: "remove", label: t("docker.menu.remove"), icon: <ICONS.delete />, destructive: true, onSelect: () => onRemove(subject) }]
    : [];
  return <ObjectMenu name={name} actions={actions} viaOwner={verbs.viaOwner ? viaOwnerOf(owner, t) : undefined} />;
}

/** An image's menu: update with a net, bring its stack to Noust first, remove; and the owner's line where an application keeps it. */
export function ImageMenu({
  image,
  onRemove,
  onUpdate,
  onBring,
}: {
  image: DockerImage;
  onRemove: (subject: RemoveSubject) => void;
  onUpdate: (image: DockerImage) => void;
  onBring: (stack: string) => void;
}) {
  const t = useT();
  const name = image.tags?.[0] ?? image.id.slice(0, 19);
  const verbs = imageVerbs(image);
  const actions: ObjectAction[] = verbs.direct.flatMap((verb): ObjectAction[] => {
    switch (verb) {
      case "update":
        return [{ id: verb, label: t("docker.menu.update"), icon: <VERB_ICONS.refresh />, description: t("docker.menu.updateDescription"), onSelect: () => onUpdate(image) }];
      case "bring": {
        const stack = image.update?.stack;
        return typeof stack === "string" && stack !== ""
          ? [{ id: verb, label: t("docker.menu.bring"), icon: <OBJECT_ICONS.compose />, description: t("docker.menu.bringDescription"), onSelect: () => onBring(stack) }]
          : [];
      }
      case "remove":
        return [
          {
            id: verb,
            label: t("docker.menu.remove"),
            icon: <ICONS.delete />,
            destructive: true,
            onSelect: () => onRemove({ target: { kind: "image", ref: image.id }, name, owner: image.owner, sizeBytes: image.size_bytes, usedBy: image.used_by }),
          },
        ];
      default:
        return [];
    }
  });
  return <ObjectMenu name={name} actions={actions} viaOwner={verbs.viaOwner ? viaOwnerOf(image.owner, t) : undefined} />;
}

export function VolumeMenu({ volume, onRemove }: { volume: DockerVolume; onRemove: (subject: RemoveSubject) => void }) {
  const t = useT();
  return removeOnly(
    volumeVerbs(volume),
    volume.name,
    { target: { kind: "volume", ref: volume.name }, name: volume.name, owner: volume.owner, sizeBytes: volume.size_bytes, usedBy: volume.used_by, anonymous: volume.anonymous },
    volume.owner,
    onRemove,
    t,
  );
}

/** A stack's menu: start, stop and restart as a whole, for what nobody of Noust made. An application's stack leads to the application. */
export function StackMenu({ stack }: { stack: DockerStack }) {
  const t = useT();
  const { node } = useNode();
  const start = useStackVerb("start");
  const stop = useStackVerb("stop");
  const restart = useStackVerb("restart");
  const [stopping, setStopping] = useState(false);
  const can = useCan();
  const manage = can("docker.manage");
  const run = (verb: "start" | "restart"): void => ({ start, restart })[verb].mutate(stack.name);
  const verbs = stackVerbs(stack);
  const actions: ObjectAction[] = manage
    ? verbs.direct.flatMap((verb): ObjectAction[] => {
        switch (verb) {
          case "restart":
            return [{ id: verb, label: t("common.words.restart"), icon: <VERB_ICONS.rebuild />, onSelect: () => run("restart") }];
          case "stop":
            return [{ id: verb, label: t("common.words.stop"), icon: <VERB_ICONS.turnOff />, onSelect: () => setStopping(true) }];
          case "start":
            return [{ id: verb, label: t("common.words.start"), icon: <VERB_ICONS.turnOn />, onSelect: () => run("start") }];
          default:
            return [];
        }
      })
    : [];
  return (
    <>
      <ObjectMenu name={stack.name} actions={actions} viaOwner={verbs.viaOwner ? viaOwnerOf(stack.owner, t) : undefined} />
      <Can permission="docker.manage">
        <ConfirmDialog
          friction="simple"
          open={stopping}
          onOpenChange={setStopping}
          title={t("docker.stack.stopTitle", { name: stack.name })}
          description={t("docker.stack.stopDescription", { count: stack.running })}
          actionLabel={t("common.words.stop")}
          {...(node !== null ? { server: node } : {})}
          onConfirm={async () => {
            await stop.mutateAsync(stack.name);
          }}
        />
      </Can>
    </>
  );
}

export function NetworkMenu({ network, onRemove }: { network: DockerNetwork; onRemove: (subject: RemoveSubject) => void }) {
  const t = useT();
  return removeOnly(networkVerbs(network), network.name, { target: { kind: "network", ref: network.id }, name: network.name, owner: network.owner, usedBy: network.containers }, network.owner, onRemove, t);
}
