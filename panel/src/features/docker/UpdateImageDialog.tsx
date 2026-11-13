/**
 * Updating an image, said before it is done. What an application runs is updated by the application's update, which has the health gate
 * and the way back, so this shows the plan the server drew (what moves, in which steps, what is missing before it can go ahead) and
 * starts that update. A new major version is a decision the operator takes in words, and a database's also needs a copy that was proven
 * to restore: until there is one, the dialog says which and offers nothing else. What the server says cannot be updated from here is not
 * offered; its reason is in the dialog only when somebody opened it for that image.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { dockerKeys } from "../../api/queries/docker";
import type { DockerImage } from "../../api/queries/docker";
import { dockerImageUpdateQuery } from "../../api/queries/dockerChanges";
import type { DockerImageUpdate } from "../../api/queries/dockerChanges";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import type { Accepted } from "../databases/jobs";
import { imageName } from "./model";

const STEPS = ["pull", "copy_databases", "recreate_behind_health", "keep_previous", "roll_back_on_failure"] as const;

/** Whether the plan can go ahead from here once the person has said what it asks for. */
export function canGoAhead(plan: Pick<DockerImageUpdate, "apply" | "needs">, acceptedMajor: boolean): boolean {
  if (!plan.apply) return false;
  if (plan.needs === "verified_copy") return false;
  if (plan.needs === "accept_major") return acceptedMajor;
  return true;
}

export interface UpdateImageDialogProps {
  image: DockerImage | null;
  onClose: () => void;
  /** Called with the job the server queued, so the page that asked follows it. */
  onQueued: (accepted: Accepted) => void;
}

export function UpdateImageDialog({ image, onClose, onQueued }: UpdateImageDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [major, setMajor] = useState(false);
  const plan = useQuery({ ...dockerImageUpdateQuery(image?.id ?? ""), enabled: image !== null });
  if (image === null) return null;
  const data = plan.data;
  const facts = (update: DockerImageUpdate): KeyValueItem[] => [
    { label: t("docker.update.reference"), value: update.reference ?? imageName(image), mono: true },
    ...(update.current_version !== null && update.current_version !== undefined && update.current_version !== "" ? [{ label: t("docker.update.runs"), value: update.current_version, mono: true }] : []),
    ...(update.latest_version !== null && update.latest_version !== undefined && update.latest_version !== "" ? [{ label: t("docker.update.latest"), value: update.latest_version, mono: true }] : []),
    ...(update.checked_at !== null && update.checked_at !== undefined ? [{ label: t("docker.update.looked"), value: <RelativeTime value={update.checked_at} />, mono: false, copy: false as const }] : []),
    ...((update.images?.length ?? 0) > 1 ? [{ label: t("docker.update.moves"), value: (update.images ?? []).join(", "), mono: true }] : []),
  ];
  return (
    <ConfirmDialog
      friction="simple"
      open
      onOpenChange={(open) => {
        if (!open) {
          setMajor(false);
          onClose();
        }
      }}
      size="md"
      title={t("docker.update.title", { name: imageName(image) })}
      description={data?.app !== null && data?.app !== undefined ? t("docker.update.descriptionApp", { app: data.app }) : t("docker.update.description")}
      actionLabel={t("common.words.update")}
      ready={data !== undefined && canGoAhead(data, major)}
      {...(node !== null ? { server: node } : {})}
      onConfirm={async () => {
        const accepted = await request("post", "/api/docker/images/{image_id}/update", { params: { image_id: image.id }, body: { accept_major: major } });
        onQueued(accepted);
        void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
      }}
    >
      <QueryState
        query={plan}
        sentences={{ loading: t("docker.update.loading"), failed: t("docker.update.failed"), refreshFailed: t("docker.update.failed") }}
        skeleton={<Skeleton className="h-32 w-full" />}
      >
        {(update) => (
          <div className="flex min-w-0 flex-col gap-4">
            <KeyValueList items={facts(update)} />
            {update.steps !== undefined && update.steps.length > 0 ? (
              <ol aria-label={t("docker.update.stepsLabel")} className="flex list-decimal flex-col gap-1 pl-5 text-13 text-fg">
                {update.steps.filter((step): step is (typeof STEPS)[number] => (STEPS as readonly string[]).includes(step)).map((step) => (
                  <li key={step}>{t(`docker.update.steps.${step}`)}</li>
                ))}
              </ol>
            ) : null}
            {update.needs === "verified_copy" ? (
              <Notice tone="warning" title={t("docker.update.needsCopy.title")}>
                {t("docker.update.needsCopy.body")} {update.backup?.via !== undefined && update.backup.via !== "" ? <Mono>{update.backup.via}</Mono> : null}
              </Notice>
            ) : null}
            {update.needs === "accept_major" || update.major ? (
              <Checkbox label={t("docker.update.acceptMajor")} description={t("docker.update.acceptMajorHelp")} checked={major} onCheckedChange={setMajor} />
            ) : null}
            {!update.apply && update.via !== "" ? (
              <Notice title={t("docker.update.notHere")}>
                <Mono>{update.via}</Mono>
              </Notice>
            ) : null}
          </div>
        )}
      </QueryState>
    </ConfirmDialog>
  );
}
