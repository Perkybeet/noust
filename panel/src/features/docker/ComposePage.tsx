/**
 * Editing the Compose document of a stack, with a net: the text is the person's, and it is judged before anything runs. Judging is what
 * Compose resolves the text to (an `include`, an `extends` or a `volumes_from` cannot hide a privileged service), what it would change
 * (which services are recreated, which are not touched, whether a volume would go) from Compose's own rehearsal, and whether the images
 * it brings in exist. Applying is the application's update, behind its health gate, and when the stack does not come up the document
 * before is put back. Every saved text is a version, who saved it, when and why, and putting one back is an edit like any other.
 * Reading the document is a reveal (it can hold a value somebody typed into it) and is recorded.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { dockerKeys } from "../../api/queries/docker";
import { dockerChangeKeys, dockerComposeQuery, dockerComposeVersionsQuery } from "../../api/queries/dockerChanges";
import type { DockerComposeCheck, DockerComposeVersion } from "../../api/queries/dockerChanges";
import { DetailPage } from "../../components/page/DetailPage";
import { FileEditorPage } from "../../components/page/FileEditorPage";
import { useCan } from "../../components/page/permissions";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { DiffLines } from "../../components/ui/DiffLines";
import { ICONS } from "../../components/ui/icons";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { useNode } from "../../nodes/useNode";
import { ConfigEditor } from "../domains/ConfigEditor";
import type { Accepted } from "../databases/jobs";
import { useJobInHand } from "../jobs/useJobInHand";
import { reportActionError } from "../apps/useAppActions";
import { changedLines, diffLinesOf } from "./composeDiff";
import { COMPOSE_WHY_CODES } from "./model";
import { DOCKER_CAPABILITY } from "./SummaryPage";

/** What judging a draft found, drawn beside the editor: what was checked, what is wrong (verbatim), and what it would change. */
function Verdict({ result, name }: { result: DockerComposeCheck; name: string }) {
  const t = useT();
  const lines = diffLinesOf(result.diff);
  const effects = result.effects;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {(result.problems ?? []).length > 0 ? (
        <div role="group" aria-label={t("docker.compose.problems")} className="flex min-w-0 flex-col gap-3">
          {(result.problems ?? []).map((problem, index) => (
            <div key={`${problem.code}-${String(index)}`} className="flex min-w-0 flex-col gap-1.5">
              <InlineMessage tone="error">
                {problem.message}
                {problem.line !== null && problem.line !== undefined ? ` (${t("docker.compose.line", { line: problem.line })})` : ""}
              </InlineMessage>
              {problem.output !== "" ? <SystemOutput label={t("docker.compose.outputLabel", { name })}>{problem.output}</SystemOutput> : null}
            </div>
          ))}
        </div>
      ) : null}
      {(result.checks ?? []).length > 0 ? (
        <ul aria-label={t("docker.compose.checks")} className="flex flex-col gap-1.5 text-13">
          {(result.checks ?? []).map((check) => (
            <li key={check.name} className="flex items-start gap-2">
              {check.ok ? <ICONS.success aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-ok" /> : <ICONS.error aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fail" />}
              <span className="text-fg">
                {t(`docker.compose.check.${check.name === "compose" || check.name === "privileges" || check.name === "images" || check.name === "variables" ? check.name : "compose"}`)}
                {check.detail !== "" ? <span className="text-fg-muted">{`: ${check.detail}`}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {result.valid && !result.changed ? <p className="text-13 text-fg-muted">{t("docker.compose.unchanged")}</p> : null}
      {result.valid && lines.length > 0 ? <DiffLines lines={lines} label={t("docker.compose.diffLabel", { name })} labels={{ added: t("docker.compose.added"), removed: t("docker.compose.removed") }} /> : null}
      {effects !== null && effects !== undefined && (effects.changes ?? []).length > 0 ? (
        <ul aria-label={t("docker.compose.effects")} className="flex flex-col gap-1 text-13 text-fg">
          {(effects.changes ?? []).map((change, index) => (
            <li key={`${change.kind}-${change.name}-${String(index)}`}>
              <Mono>{change.service !== "" ? change.service : change.name}</Mono> {change.action}
            </li>
          ))}
        </ul>
      ) : null}
      {effects?.removes_volumes === true ? <InlineMessage tone="warning">{t("docker.compose.removesVolumes")}</InlineMessage> : null}
    </div>
  );
}

function History({ name, onRestore }: { name: string; onRestore: (version: DockerComposeVersion) => void }) {
  const t = useT();
  const versions = useQuery(dockerComposeVersionsQuery(name));
  const columns: Column<DockerComposeVersion>[] = [
    { mono: true, id: "version", header: t("docker.compose.history.version"), card: "title", width: "w-24", cell: (row) => `#${String(row.version)}` },
    { id: "when", header: t("docker.compose.history.when"), card: "status", width: "w-40", cell: (row) => <RelativeTime value={row.created_at} /> },
    { id: "who", header: t("docker.compose.history.who"), card: "meta", hideBelow: "sm", cell: (row) => row.actor ?? "" },
    { id: "reason", header: t("docker.compose.history.reason"), card: "meta", cell: (row) => row.reason },
  ];
  return (
    <SurfaceSection title={t("docker.compose.history.title")} icon={OBJECT_ICONS.deployment} description={t("docker.compose.history.description")} flush>
      <DataTable
        caption={t("docker.compose.history.caption", { name })}
        columns={columns}
        rows={versions.data?.items}
        getRowId={(row) => String(row.version)}
        embedded
        skeletonRows={2}
        rowActions={(row) => (
          <Button size="sm" variant="ghost" onClick={() => onRestore(row)}>
            {t("docker.compose.history.restore")}
          </Button>
        )}
      />
    </SurfaceSection>
  );
}

export function ComposePage({ name }: { name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  // Reading the document is a reveal and applying it changes Docker: whoever may do only one of them is told, and nothing is read.
  const can = useCan();
  const mayEdit = can("docker.manage") && can("secrets.reveal");
  const source = useQuery({ ...dockerComposeQuery(name), enabled: mayEdit });
  const [draft, setDraft] = useState<string | null>(null);
  const [result, setResult] = useState<DockerComposeCheck | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState<DockerComposeVersion | null>(null);
  const job = useJobInHand(() => {
    void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
    void queryClient.invalidateQueries({ queryKey: dockerChangeKeys.compose(name) });
    void queryClient.invalidateQueries({ queryKey: dockerChangeKeys.versions(name) });
  });
  const data = source.data;
  const text = draft ?? data?.text ?? "";
  const check = useMutation({
    mutationFn: (body: string) => request("post", "/api/docker/stacks/{name}/compose/check", { params: { name }, body: { text: body } }),
    onSuccess: (checked) => setResult(checked),
    onError: (error) => reportActionError(t("docker.compose.checkFailed"), error),
  });
  const queued = (accepted: unknown): void => {
    job.track(accepted as Accepted, { running: t("docker.compose.running"), succeeded: t("docker.compose.succeeded"), failed: t("docker.compose.jobFailed") });
  };
  const changes = data?.text === undefined || data.text === null ? 0 : changedLines(data.text, text);
  const whyCode = data !== undefined ? COMPOSE_WHY_CODES.find((code) => code === data.why_code) ?? null : null;
  const header = {
    title: t("docker.compose.title", { name }),
    mono: false,
    description: t("docker.compose.description"),
  };
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      {data !== undefined && data.editable && data.text !== null && data.text !== undefined ? (
        <FileEditorPage
          header={header}
          {...(job.notice !== null ? { notice: job.notice } : {})}
          bar={{
            changes,
            onDiscard: () => {
              setDraft(null);
              setResult(null);
            },
            onTest: () => check.mutate(text),
            onTestAndSave: () => {
              check.mutate(text, {
                onSuccess: (checked) => {
                  if (checked.valid && checked.changed) setSaving(true);
                },
              });
            },
            testing: check.isPending,
          }}
        >
          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            <ConfigEditor className="h-editor" value={text} onChange={setDraft} label={t("docker.compose.editorLabel", { name })} />
            <div className="flex min-w-0 flex-col gap-4">
              <SurfaceSection title={t("docker.compose.verdict")} icon={OBJECT_ICONS.diagnose} description={t("docker.compose.verdictLine")}>
                {result === null ? <p className="text-13 text-fg-muted">{t("docker.compose.notJudged")}</p> : <Verdict result={result} name={name} />}
              </SurfaceSection>
              <History name={name} onRestore={setRestoring} />
            </div>
          </div>
        </FileEditorPage>
      ) : (
        // Nothing to edit: no bar of an editor to test and save what is not there, only what the person is told.
        <DetailPage header={{ title: header.title, mono: false }}>
          {mayEdit ? (
            <QueryState
              query={source}
              sentences={{ loading: t("docker.compose.loading"), failed: t("docker.compose.failed"), refreshFailed: t("docker.compose.failed") }}
              skeleton={<Skeleton className="h-editor w-full" />}
            >
              {(plain) => (
                <Notice tone="info" title={t("docker.compose.notEditable")}>
                  {whyCode !== null ? t(`docker.compose.why.${whyCode}`) : plain.why}
                </Notice>
              )}
            </QueryState>
          ) : (
            <Notice tone="info" title={t("docker.compose.needs.title")}>
              {t("docker.compose.needs.body")}
            </Notice>
          )}
        </DetailPage>
      )}
      <ConfirmDialog
        friction="simple"
        open={saving}
        onOpenChange={setSaving}
        title={t("docker.compose.apply.title", { name })}
        description={t("docker.compose.apply.description")}
        actionLabel={t("docker.compose.apply.action")}
        ready={reason.trim() !== ""}
        {...(node !== null ? { server: node } : {})}
        onConfirm={async () => {
          queued(await request("put", "/api/docker/stacks/{name}/compose", { params: { name }, body: { text, reason: reason.trim() } }));
          setDraft(null);
          setResult(null);
          setReason("");
        }}
      >
        <Field label={t("docker.compose.apply.reason")} description={t("docker.compose.apply.reasonHelp")}>
          <Input value={reason} onChange={(event) => setReason(event.target.value)} autoComplete="off" />
        </Field>
      </ConfirmDialog>
      <ConfirmDialog
        friction="simple"
        open={restoring !== null}
        onOpenChange={(open) => {
          if (!open) setRestoring(null);
        }}
        title={t("docker.compose.restore.title", { version: restoring?.version ?? 0 })}
        description={t("docker.compose.restore.description")}
        actionLabel={t("docker.compose.restore.action")}
        {...(node !== null ? { server: node } : {})}
        onConfirm={async () => {
          if (restoring === null) return;
          queued(await request("post", "/api/docker/stacks/{name}/compose/versions/{version}/restore", { params: { name, version: restoring.version }, body: { reason: t("docker.compose.restore.reason", { version: restoring.version }) } }));
        }}
      />
    </NodeCapabilityGate>
  );
}
