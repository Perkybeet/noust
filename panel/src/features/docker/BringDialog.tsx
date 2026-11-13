/**
 * Bringing a stack nobody of Noust made to Noust, without touching it. Noust registers the stack as it runs: no container is recreated,
 * restarted or changed, and its Compose file is not rewritten. The dialog says first what the server found (the folder, the file, how many
 * containers, jobs that ended, volumes and networks), what would not come back after a restart, and what it checked (that the folder is on
 * this server and that Compose, rehearsing the start the way Noust would, changes nothing). The only thing the person types is the name the
 * stack will have among the applications. A stack that cannot be brought says why, with the server's own words and the command that opens it.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { dockerKeys } from "../../api/queries/docker";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

export interface BringDialogProps {
  /** The stack to bring; null keeps the dialog closed. */
  stack: string | null;
  onClose: () => void;
}

export function BringDialog({ stack, onClose }: BringDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  // What would be registered, asked once when the dialog opens and never applied: `preview` changes nothing.
  const preview = useQuery({
    queryKey: ["docker", "stack", stack, "bring-preview"],
    queryFn: () => request("post", "/api/docker/stacks/{name}/bring", { params: { name: stack ?? "" }, body: { preview: true, accept_recreate: false } }),
    enabled: stack !== null,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    // A preview is a root-equivalent request: it is asked when the dialog opens and not again because the window was focused.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (stack === null) return null;
  const typed = name.trim();
  return (
    <ConfirmDialog
      friction="simple"
      open
      onOpenChange={(open) => {
        if (!open) {
          setName("");
          onClose();
        }
      }}
      size="md"
      title={t("docker.bring.title", { name: stack })}
      description={t("docker.bring.description")}
      actionLabel={t("docker.bring.action")}
      ready={preview.data !== undefined}
      {...(node !== null ? { server: node } : {})}
      onConfirm={async () => {
        await request("post", "/api/docker/stacks/{name}/bring", { params: { name: stack }, body: { preview: false, accept_recreate: false, ...(typed !== "" ? { name: typed } : {}) } });
        await queryClient.invalidateQueries({ queryKey: dockerKeys.all });
      }}
    >
      <QueryState
        query={preview}
        sentences={{ loading: t("docker.bring.loading"), failed: t("docker.bring.failed"), refreshFailed: t("docker.bring.failed") }}
        skeleton={<Skeleton className="h-32 w-full" />}
      >
        {(plan) => {
          const counts = plan.counts ?? {};
          const facts: KeyValueItem[] = [
            { label: t("docker.bring.folder"), value: plan.folder, mono: true },
            { label: t("docker.bring.file"), value: plan.compose_file, mono: true },
            {
              label: t("docker.bring.has"),
              value: t("docker.bring.counts", { containers: counts["containers"] ?? 0, jobs: counts["jobs"] ?? 0, volumes: counts["volumes"] ?? 0, networks: counts["networks"] ?? 0 }),
              mono: false,
              copy: false,
            },
          ];
          return (
            <div className="flex min-w-0 flex-col gap-4">
              <KeyValueList items={facts} />
              <Field label={t("docker.bring.name")} description={t("docker.bring.nameHelp")}>
                <Input value={name} placeholder={plan.name} onChange={(event) => setName(event.target.value)} autoComplete="off" spellCheck={false} mono />
              </Field>
              {(plan.restart_loss ?? []).length > 0 ? (
                <Notice tone="warning" title={t("docker.bring.lostTitle")}>
                  {t("docker.bring.lostBody")}{" "}
                  {(plan.restart_loss ?? []).map((lost, index) => (
                    <span key={lost.container}>
                      {index > 0 ? ", " : ""}
                      <Mono>{lost.container}</Mono>
                    </span>
                  ))}
                </Notice>
              ) : null}
              {(plan.warnings ?? []).length > 0 ? (
                <ul className="flex flex-col gap-1 text-13 text-fg-muted">
                  {(plan.warnings ?? []).map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        }}
      </QueryState>
    </ConfirmDialog>
  );
}
