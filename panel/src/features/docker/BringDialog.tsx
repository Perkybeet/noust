/**
 * Bringing a stack nobody of Noust made to Noust, without touching it. Noust registers the stack as it runs: no container is recreated,
 * restarted or changed, and its Compose file is not rewritten. The dialog says first what the server found (the folder, the file, how many
 * containers, jobs that ended, volumes and networks), what would not come back after a restart, and what it checked (that the folder is on
 * this server and that Compose, rehearsing the start the way Noust would, changes nothing). The only thing the person types is the name the
 * stack will have among the applications. A stack that cannot be brought says why, with the server's own words and the command that opens it.
 *
 * A stack that runs with what gives this server to a container is shown line by line, and bringing it is a decision of its own: a box
 * beside the lines, offered to whoever holds the root-equivalent permission, and the plan is read again with it accepted. What is
 * accepted is the lines in front of the person: the request says which they are (their digest), and if the Compose file says something
 * else by the time it is made, the server answers with the lines as they are, the dialog shows those and the box is empty again.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";
import { dockerKeys } from "../../api/queries/docker";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { QueryState } from "../../components/page/QueryState";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { ServerText } from "../../components/ui/ServerText";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { acceptedLines, hostAccessOf } from "../../lib/hostAccess";
import type { HostAccess } from "../../lib/hostAccess";
import { codedAt } from "../../lib/serverSentence";
import { useNode } from "../../nodes/useNode";
import { HostAccessLines } from "./HostAccessLines";

type Plan = ResponseOf<"/api/docker/stacks/{name}/bring/preview", "post">;

/** What looking at the stack came to: what would be registered, or the lines of its Compose file that give this server to a container. */
type Looked = { kind: "plan"; plan: Plan } | { kind: "host_access"; access: HostAccess };

export interface BringDialogProps {
  /** The stack to bring; null keeps the dialog closed. */
  stack: string | null;
  onClose: () => void;
}

/** What would be registered, with the name to give it and what the stack would not bring back after a restart. */
function PlanBody({ plan, name, onName }: { plan: Plan; name: string; onName: (name: string) => void }) {
  const t = useT();
  const counts = plan.counts ?? {};
  const rehearsal = rehearsalOf(plan);
  const facts: KeyValueItem[] = [
    { label: t("docker.bring.folder"), value: plan.folder, mono: true },
    { label: t("docker.bring.file"), value: plan.compose_file, mono: true },
    {
      label: t("docker.bring.has"),
      value: t("docker.bring.counts", {
        containers: t("docker.bring.countContainers", { count: counts["containers"] ?? 0 }),
        jobs: t("docker.bring.countJobs", { count: counts["jobs"] ?? 0 }),
        volumes: t("docker.bring.countVolumes", { count: counts["volumes"] ?? 0 }),
        networks: t("docker.bring.countNetworks", { count: counts["networks"] ?? 0 }),
      }),
      mono: false,
      copy: false,
    },
  ];
  return (
    <>
      <KeyValueList items={facts} />
      <Field label={t("docker.bring.name")} description={t("docker.bring.nameHelp")}>
        <Input value={name} placeholder={plan.name} onChange={(event) => onName(event.target.value)} autoComplete="off" spellCheck={false} mono />
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
          {(plan.warnings ?? []).map((warning, index) => (
            // What the person is asked to accept is said in the console's language: the server sends each sentence with its code, and the
            // English only for one it has not given a code yet.
            <li key={warning}>
              <ServerText coded={codedAt(plan.warnings_coded, index)} text={warning} />
            </li>
          ))}
        </ul>
      ) : null}
      {/* Compose's own words, as it rehearsed the start the way Noust would: shown when the server brings them, and nothing is drawn for words it did not. */}
      {rehearsal.trim() !== "" ? (
        <Disclosure label={t("docker.bring.rehearsal")}>
          <SystemOutput label={t("docker.bring.rehearsalLabel", { name: plan.name })}>{rehearsal}</SystemOutput>
        </Disclosure>
      ) : null}
    </>
  );
}

/** What Compose said when the plan was made, as it came; empty when the server did not bring it (an older one sends no such field). */
function rehearsalOf(plan: Plan): string {
  const sent = (plan as { rehearsal?: unknown }).rehearsal;
  return typeof sent === "string" ? sent : "";
}

export function BringDialog({ stack, onClose }: BringDialogProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  // The lines the person ticked the box for, by their digest: the box is ticked only while the lines on screen are those.
  const [acceptedDigest, setAcceptedDigest] = useState<string | null>(null);
  // The lines as the server last said them, when they are not the ones the first look listed: the file changed since.
  const [relisted, setRelisted] = useState<HostAccess | null>(null);
  // What would be registered, asked once when the dialog opens: the preview is its own route, which only rehearses Compose's start and
  // takes no sudo mode, so looking asks nobody who they are. Who they are is asked when the stack is brought, below. A stack that runs
  // with what gives this server to a container is not a failure to look at: it is the lines, and the decision about them.
  const looked = useQuery({
    queryKey: ["docker", "stack", stack, "bring-preview"],
    queryFn: async (): Promise<Looked> => {
      try {
        return { kind: "plan", plan: await request("post", "/api/docker/stacks/{name}/bring/preview", { params: { name: stack ?? "" }, body: { accept_recreate: false, accept_host_access: false, host_access_digest: "" } }) };
      } catch (error) {
        const access = hostAccessOf(error);
        if (access === null) throw error;
        return { kind: "host_access", access };
      }
    },
    enabled: stack !== null,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    // Compose's rehearsal runs once, not again because the window was focused.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const listed = looked.data?.kind === "host_access" ? looked.data.access : null;
  const access = relisted ?? listed;
  const accepted = access !== null && acceptedDigest === access.digest;
  // Once the lines are accepted the plan is read again with that said, which is what shows what would be registered; the first answer
  // keeps the lines, which stay on screen beside the plan because they are what is being accepted.
  const accepting = useQuery({
    queryKey: ["docker", "stack", stack, "bring-preview", "host-access", access?.digest ?? ""],
    queryFn: async () => {
      try {
        return await request("post", "/api/docker/stacks/{name}/bring/preview", { params: { name: stack ?? "" }, body: { accept_recreate: false, accept_host_access: true, host_access_digest: access?.digest ?? "" } });
      } catch (error) {
        // The file gives this server something else than it did when the lines were listed: they are listed again, as they are now.
        const now = hostAccessOf(error);
        if (now === null) throw error;
        setRelisted(now);
        return null;
      }
    },
    enabled: stack !== null && access !== null && accepted,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  if (stack === null) return null;
  const typed = name.trim();
  const plan = looked.data?.kind === "plan" ? looked.data.plan : accepted ? (accepting.data ?? undefined) : undefined;
  return (
    <ConfirmDialog
      friction="simple"
      open
      onOpenChange={(open) => {
        if (!open) {
          setName("");
          setAcceptedDigest(null);
          setRelisted(null);
          onClose();
        }
      }}
      size="md"
      title={t("docker.bring.title", { name: stack })}
      description={t("docker.bring.description")}
      actionLabel={t("docker.bring.action")}
      // Nothing is brought before there is a plan to read and, for a stack that runs with lines of the server, until they are accepted.
      ready={plan !== undefined && (access === null || accepted)}
      {...(node !== null ? { server: node } : {})}
      onConfirm={async () => {
        try {
          await request("post", "/api/docker/stacks/{name}/bring", { params: { name: stack }, body: { preview: false, accept_recreate: false, accept_host_access: accepted, ...acceptedLines(access, accepted), ...(typed !== "" ? { name: typed } : {}) } });
        } catch (error) {
          // The lines changed between the look and this: nothing was brought, and the lines of now are in front of the person.
          const now = hostAccessOf(error);
          if (now !== null) setRelisted(now);
          throw error;
        }
        await queryClient.invalidateQueries({ queryKey: dockerKeys.all });
      }}
    >
      <QueryState
        query={looked}
        sentences={{ loading: t("docker.bring.loading"), failed: t("docker.bring.failed"), refreshFailed: t("docker.bring.failed") }}
        skeleton={<Skeleton className="h-32 w-full" />}
      >
        {(found) => (
          <div className="flex min-w-0 flex-col gap-4">
            {found.kind === "host_access" && access !== null ? <HostAccessLines access={access} accepted={accepted} onAcceptedChange={(ticked) => setAcceptedDigest(ticked ? access.digest : null)} /> : null}
            {found.kind === "plan" ? <PlanBody plan={found.plan} name={name} onName={setName} /> : null}
            {found.kind === "host_access" && accepted ? (
              <QueryState query={accepting} sentences={{ loading: t("docker.bring.loading"), failed: t("docker.bring.failed"), refreshFailed: t("docker.bring.failed") }} skeleton={<Skeleton className="h-32 w-full" />}>
                {(wouldRegister) => (wouldRegister === null ? null : <PlanBody plan={wouldRegister} name={name} onName={setName} />)}
              </QueryState>
            ) : null}
          </div>
        )}
      </QueryState>
    </ConfirmDialog>
  );
}
