/**
 * A container application's settings as the rows of a summary: how its image is made, where it
 * listens, what it keeps, and the registry account it pulls with. Only what the operator set is
 * said; a secret is said to exist and never shown.
 */

import type { KeyValueItem } from "../../components/page/KeyValueList";
import type { T } from "../../i18n";
import { joinList } from "../new-app/wizard";
import { DEFAULT_PORTS, RESTART_LABELS } from "./model";
import type { ContainerDraft } from "./model";

/** The rows that say how the image comes to exist and how it runs. */
export function containerSummary(t: T, draft: ContainerDraft): KeyValueItem[] {
  const items: KeyValueItem[] = [];
  if (draft.method === "image") {
    items.push({ label: t("containers.summary.builtFrom"), value: t("containers.settings.fromImage"), mono: false, copy: false });
  } else if (draft.method === "railpack") {
    items.push({ label: t("containers.summary.builtFrom"), value: t("containers.settings.fromRailpack"), mono: false, copy: false });
  } else {
    items.push({
      label: t("containers.summary.builtFrom"),
      value: t("containers.settings.fromDockerfile"),
      mono: false,
      copy: false,
      hint: t("containers.summary.dockerfileIn", {
        dockerfile: draft.dockerfile.trim() === "" ? t("containers.summary.foundDockerfile") : draft.dockerfile.trim(),
        context: draft.context.trim() === "" ? "." : draft.context.trim(),
      }),
    });
  }
  const port = draft.containerPort.trim();
  items.push({
    label: t("containers.summary.port"),
    value: port === "" ? t("containers.summary.portDefault", { port: String(DEFAULT_PORTS[draft.method]) }) : port,
    mono: port !== "",
    copy: false,
  });
  const volumes = draft.volumes.map((row) => row.value.trim()).filter((path) => path !== "");
  items.push({
    label: t("containers.summary.volumes"),
    value: volumes.length > 0 ? joinList(volumes, t.locale) : t("containers.summary.noVolumes"),
    mono: volumes.length > 0,
    copy: false,
  });
  items.push({ label: t("containers.summary.restart"), value: t(RESTART_LABELS[draft.restartPolicy]), mono: false, copy: false });
  if (draft.method !== "image") {
    const args = draft.buildArgs.filter((row) => row.name.trim() !== "").length;
    const secrets = draft.buildSecrets.filter((row) => row.secretId.trim() !== "").length;
    if (args > 0 || secrets > 0) {
      items.push({
        label: t("containers.summary.build"),
        value: t("containers.summary.buildCounts", { args: String(args), secrets: String(secrets) }),
        mono: false,
        copy: false,
      });
    }
  } else if (draft.registryUsername.trim() !== "") {
    const server = draft.registryServer.trim();
    items.push({
      label: t("containers.summary.account"),
      value: server === "" ? draft.registryUsername.trim() : `${draft.registryUsername.trim()} @ ${server}`,
      hint: t("containers.summary.passwordNotShown"),
      copy: false,
    });
  }
  return items;
}
