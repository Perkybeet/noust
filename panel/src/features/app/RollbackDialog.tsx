import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import { rollbackPointsQuery } from "../../api/queries/apps";
import type { Job } from "../../api/queries/jobs";
import { deploymentsQuery } from "../../api/queries/deployments";
import { rolloutQuery } from "../../api/queries/rollout";
import { ErrorBlock } from "../../components/page/QueryState";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatDateTime, parseTimestamp } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { useAppActions } from "../apps/useAppActions";
import { rollbackTargets, servingDeployment } from "./rollback";
import type { BackupTarget, Target } from "./rollback";
import { schemaChangeRefusal } from "./schemaChange";
import { useSchemaChangeConfirmation } from "./SchemaChangeDialog";

/** The newest deployments the app's history keeps: the store prunes beyond twenty. */
const HISTORY = 20;

/** What the dialog is opened on: the version asked for, or nothing for the one before the live one. */
export interface RollbackChoice {
  /** A deployment of the app, by its number. */
  deployment?: number;
  /** A backup of an app kept in a single folder, by its id. */
  backup?: string;
}

const key = (target: Target): string => `${target.kind}:${String(target.id)}`;

function whenWords(t: T, when: string | null): string | null {
  const moment = parseTimestamp(when);
  return moment === null ? null : formatDateTime(moment, t.locale);
}

function optionOf(t: T, target: Target): SelectOption {
  const when = whenWords(t, target.when);
  if (target.kind === "backup") {
    return {
      value: key(target),
      label: t("appPages.rollback.backupOptionLabel", { when: when ?? target.id }),
      hint: `${target.description}, ${formatBytes(target.sizeBytes, t.locale)}`,
    };
  }
  const detail = [target.commit, when].filter((part): part is string => part !== null).join(" · ");
  return {
    value: key(target),
    label: target.message !== null ? t("appPages.rollback.optionLabel", { id: String(target.id), message: target.message }) : t("appPages.rollback.optionLabelBare", { id: String(target.id) }),
    ...(detail !== "" ? { hint: detail } : {}),
  };
}

/** What going back to this target puts back, in the words of how the backend does it. */
function goesBack(t: T, target: Target, serving: number | null): ReactNode {
  if (target.kind === "backup") return t.rich("appPages.rollback.goesBackup", { backup: <Mono>{target.id}</Mono> });
  const version = <Mono>{target.commit !== null ? `#${String(target.id)} (${target.commit})` : `#${String(target.id)}`}</Mono>;
  if (target.method === "snapshot" && target.snapshot !== null) {
    return t.rich("appPages.rollback.goesSnapshot", { backup: <Mono>{target.snapshot}</Mono>, version });
  }
  if (target.method === "rebuild" && target.commit !== null) return t.rich("appPages.rollback.goesRebuild", { commit: <Mono>{target.commit}</Mono> });
  return serving !== null
    ? t.rich("appPages.rollback.goesInstant", { version, serving: <Mono>{`#${String(serving)}`}</Mono> })
    : t.rich("appPages.rollback.goesInstantNoServing", { version });
}

function Consequence({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="text-12 text-fg-faint">{term}</dt>
      <dd className="min-w-0 text-13 text-pretty text-fg">{children}</dd>
    </div>
  );
}

export interface RollbackDialogProps {
  domain: string;
  /** The app's deploy layout: `releases`, or `inplace` for an app kept in a single folder. */
  layout: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Follows the job the rollback queues; without it, a toast says it was queued. */
  onJobQueued?: (job: Job) => void;
  /** What to open on. Without it: the version before the one that serves. */
  choice?: RollbackChoice;
}

/**
 * Puts an earlier version of the app back, and says what that does before it does it. One
 * question for every way back (a release, the images a stack kept, a commit built again, a
 * backup): the version, what goes back, what stays as it is, and whether automatic deploys will
 * pause. Past a change to the database's schema it hands over to the question that names it.
 */
export function RollbackDialog({ domain, layout, open, onOpenChange, onJobQueued, choice }: RollbackDialogProps) {
  const t = useT();
  const { node } = useNode();
  const history = useQuery({ ...deploymentsQuery({ domain, limit: HISTORY }), enabled: open });
  // A single folder keeps backups as well; they are another thing to go back to, listed after the deployments.
  const inPlace = layout !== "releases";
  const points = useQuery({ ...rollbackPointsQuery(domain), enabled: open && inPlace });
  const rollout = useQuery({ ...rolloutQuery(domain), retry: false, enabled: open });
  const { rollbackToDeployment, rollbackToBackup } = useAppActions(domain, onJobQueued ? { onJobQueued } : {});
  const schema = useSchemaChangeConfirmation(domain);

  const deployments = useMemo(() => history.data?.items ?? [], [history.data]);
  const targets = useMemo<Target[]>(() => {
    const backups: BackupTarget[] = (points.data?.items ?? []).map((point) => ({
      kind: "backup",
      id: point.id,
      commit: point.git_commit?.slice(0, 7) ?? null,
      when: point.created_at,
      description: point.description,
      sizeBytes: point.size_bytes,
    }));
    return [...rollbackTargets(deployments), ...backups];
  }, [deployments, points.data]);
  const serving = servingDeployment(deployments)?.id ?? null;

  // What the operator picked in this opening: each opening starts from what it was asked on.
  const [session, setSession] = useState<{ open: boolean; picked: string | null }>({ open, picked: null });
  if (session.open !== open) setSession({ open, picked: null });
  const picked = session.picked;
  const setPicked = (value: string): void => setSession({ open, picked: value });
  // What was asked for, else the version before the live one (the newest that can be gone back to).
  const asked = choice?.deployment !== undefined ? `deployment:${String(choice.deployment)}` : choice?.backup !== undefined ? `backup:${choice.backup}` : null;
  const chosen = targets.find((target) => key(target) === (picked ?? asked)) ?? targets[0] ?? null;

  const loading = history.isPending || (inPlace && points.isPending);
  // The deployments and the backups are two lists of one question: when only one cannot be read, the other is still there to choose from.
  const backupsFailed = inPlace && points.isError;
  const loadError = targets.length > 0 ? null : history.isError ? history.error : backupsFailed ? points.error : null;
  const pauses = rollout.data !== undefined && rollout.data.pause_on_rollback && rollout.data.paused === null;
  const schemaAhead = chosen?.kind === "deployment" ? chosen.schemaBetween : [];

  let body: ReactNode;
  if (loadError !== null) {
    body = <ErrorBlock compact error={loadError} title={t("appPages.rollback.listError")} />;
  } else if (loading) {
    body = (
      <LoadingRegion label={t("appPages.rollback.loading")} className="flex flex-col gap-3">
        <Skeleton className="h-control-md rounded-control" />
        <Skeleton className="h-16 rounded-control" />
      </LoadingRegion>
    );
  } else if (chosen === null) {
    body = (
      <EmptyState
        variant="inline"
        title={inPlace ? t("appPages.rollback.nothingInPlace") : t("appPages.rollback.nothing")}
        {...(inPlace
          ? {
              action: (
                <TextLink to="/backups" size="ui">
                  {t("appPages.rollback.openBackups")}
                </TextLink>
              ),
            }
          : {})}
      />
    );
  } else {
    body = (
      <>
        <Field label={t("appPages.rollback.versionLabel")}>
          <Select
            value={key(chosen)}
            options={targets.map((target) => optionOf(t, target))}
            onValueChange={setPicked}
            aria-label={t("appPages.rollback.versionLabel")}
            className="w-full"
          />
        </Field>
        <dl className="flex flex-col gap-3">
          <Consequence term={t("appPages.rollback.whatGoesBack")}>{goesBack(t, chosen, serving)}</Consequence>
          <Consequence term={t("appPages.rollback.whatStays")}>{t("appPages.rollback.staysBody")}</Consequence>
          {pauses ? (
            <Consequence term={t("appPages.rollback.automaticDeploys")}>
              {t("appPages.rollback.pausesBody")}{" "}
              <TextLink to="/apps/$domain/settings/deploys" params={{ domain }} size="inline">
                {t("appPages.rollback.pausesLink")}
              </TextLink>
            </Consequence>
          ) : null}
        </dl>
        {schemaAhead.length > 0 ? (
          <InlineMessage>{t("appPages.rollback.schemaAhead", { count: schemaAhead.length, ids: schemaAhead.map((id) => `#${String(id)}`).join(", ") })}</InlineMessage>
        ) : null}
        {history.isError ? <InlineMessage tone="info">{t("appPages.rollback.listError")}</InlineMessage> : null}
        {backupsFailed ? <InlineMessage tone="info">{t("appPages.deployments.releases.backupsLoadError")}</InlineMessage> : null}
      </>
    );
  }

  const confirm = async (): Promise<void> => {
    if (chosen === null) return;
    const go = (schemaChangedOk: boolean): Promise<unknown> =>
      chosen.kind === "deployment" ? rollbackToDeployment.mutateAsync({ id: chosen.id, schemaChangedOk }) : rollbackToBackup.mutateAsync({ id: chosen.id, schemaChangedOk });
    try {
      await go(false);
    } catch (error) {
      // Past a schema change this question closes and the one that names the deployments opens.
      if (!schema.intercept(schemaChangeRefusal(error), () => go(true))) throw error;
    }
  };

  return (
    <>
      <ConfirmDialog
        open={open}
        onOpenChange={onOpenChange}
        friction="simple"
        destructive={false}
        size="md"
        server={node}
        title={t("appPages.rollback.title", { domain })}
        description={t("appPages.rollback.description")}
        actionLabel={chosen === null ? t("appPages.rollback.actionNone") : chosen.kind === "deployment" ? t("appPages.rollback.action", { id: String(chosen.id) }) : t("appPages.rollback.actionBackup")}
        ready={!loading && loadError === null && chosen !== null}
        onConfirm={confirm}
      >
        {body}
      </ConfirmDialog>
      {schema.dialog}
    </>
  );
}
