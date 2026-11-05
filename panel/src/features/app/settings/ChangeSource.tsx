import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { changeSource, planSourceChange, sourceKeys } from "../../../api/queries/sources";
import type { SourceChange, SourcePlan } from "../../../api/queries/sources";
import { announce } from "../../../app/Announcer";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { Subsection } from "../../../components/page/Subsection";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Notice } from "../../../components/ui/Notice";
import { useT } from "../../../i18n";
import { reportActionError } from "../../apps/useAppActions";
import { ArchiveUpload } from "../upload/ArchiveUpload";
import { splitErrors } from "../../settings/formErrors";
import { BranchActions } from "./BranchPin";
import { FollowTagsActions } from "./FollowTags";
import { Mono } from "../../../components/ui/Mono";
import { SETTINGS_ICONS } from "./objectIcons";

/** The application types that run as a stack or a container: their source is a checkout or an image. */
const OWN_FILES = new Set(["docker-compose", "container"]);

/** Why a source can or cannot be changed here: only an application on releases, built from a source Noust fetches. */
export function canChangeSource(app: {
  layout?: string | null;
  app_type?: string | null;
  source?: string | null;
  preview_parent?: string | null;
}): "yes" | "inPlace" | "type" | "preview" | "push" {
  if (app.preview_parent) return "preview";
  if (OWN_FILES.has(app.app_type ?? "")) return "type";
  if ((app.source ?? "").startsWith("push:")) return "push";
  return app.layout === "releases" ? "yes" : "inPlace";
}

/** The plan as a list of facts: from, to, the branch and what the remote's head is. */
function PlanSummary({ plan }: { plan: SourcePlan }) {
  const t = useT();
  const branch = `${plan.current_branch ?? "-"} -> ${plan.target_branch ?? "-"}`;
  return (
    <div className="flex flex-col gap-3">
      <KeyValueList
        items={[
          { label: t("changeSource.plan.from"), value: plan.current === "" ? t("changeSource.none") : plan.current },
          { label: t("changeSource.plan.to"), value: plan.target },
          { label: t("changeSource.plan.branch"), value: branch },
          ...(plan.head ? [{ label: t("changeSource.plan.head"), value: plan.head.slice(0, 12) }] : []),
        ]}
      />
      {!plan.changed ? <InlineMessage tone="info">{t("changeSource.plan.unchanged")}</InlineMessage> : null}
      {plan.warnings.length > 0 ? (
        <Notice tone="warning" title={t("changeSource.plan.warnings")}>
          <ul className="flex list-disc flex-col gap-1 pl-4">
            {plan.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}

/**
 * The dialog: the new source and branch, checked against the remote before anything is written,
 * then confirmed. Editing a field throws the check away, so what is confirmed is what was shown.
 */
function ChangeDialog({
  domain,
  branch,
  open,
  onOpenChange,
  onChanged,
}: {
  domain: string;
  branch: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: (done: SourceChange) => void;
}) {
  const t = useT();
  const [source, setSource] = useState("");
  const [wanted, setWanted] = useState("");
  const [ask, setAsk] = useState(true);
  const [submitted, setSubmitted] = useState(false);
  const [plan, setPlan] = useState<SourcePlan | null>(null);

  // An uploaded archive has no branch, and a typed one would be refused.
  const uploaded = source.trim().startsWith("upload:");
  const body = () => ({ source: source.trim(), ...(wanted.trim() !== "" && !uploaded ? { branch: wanted.trim() } : {}), check: ask });
  const check = useMutation({ mutationFn: () => planSourceChange(domain, body()), onSuccess: setPlan });
  const apply = useMutation({ mutationFn: () => changeSource(domain, body()), onSuccess: onChanged });
  const busy = check.isPending || apply.isPending;
  const error = apply.isError ? apply.error : check.isError ? check.error : null;
  const split = splitErrors(error, ["source", "branch"] as const);
  const empty = source.trim() === "";

  const edited = (): void => {
    setPlan(null);
    check.reset();
    apply.reset();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (empty || busy) return;
    if (plan === null) check.mutate();
    else if (plan.changed) apply.mutate();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && busy) return;
        onOpenChange(next);
      }}
      title={t("changeSource.dialog.title", { domain })}
      description={t("changeSource.dialog.description")}
      footer={
        <>
          <Button disabled={busy} onClick={() => onOpenChange(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form="change-source" variant="primary" loading={busy} disabled={plan !== null && !plan.changed}>
            {plan === null ? t("changeSource.dialog.check") : t("changeSource.dialog.apply")}
          </Button>
        </>
      }
    >
      <form id="change-source" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t("changeSource.dialog.source")}
          description={t("changeSource.dialog.sourceDescription")}
          error={submitted && empty ? t("changeSource.dialog.required") : split.fields.source}
        >
          <Input
            mono
            value={source}
            placeholder="https://git.example.org/team/shop.git"
            onValueChange={(value: string) => {
              setSource(value);
              edited();
            }}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
          />
        </Field>
        {uploaded ? null : (
          <Field label={t("changeSource.dialog.branch")} optional description={t("changeSource.dialog.branchDescription", { branch: branch ?? "-" })} error={split.fields.branch} className="sm:max-w-64">
            <Input
              mono
              value={wanted}
              placeholder={branch ?? "main"}
              onValueChange={(value: string) => {
                setWanted(value);
                edited();
              }}
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </Field>
        )}
        {uploaded ? null : (
          <Checkbox
            label={t("changeSource.dialog.ask")}
            description={t("changeSource.dialog.askDescription")}
            checked={ask}
            onCheckedChange={(next) => {
              setAsk(next);
              edited();
            }}
          />
        )}
        <Subsection title={t("changeSource.dialog.uploadTitle")} description={t("changeSource.dialog.uploadDescription")}>
          <ArchiveUpload
            disabled={busy}
            onUploaded={(done) => {
              setSource(done.source);
              setPlan(null);
              check.reset();
              apply.reset();
            }}
            onCleared={() => {
              setSource("");
              edited();
            }}
          />
        </Subsection>
        {plan !== null ? <PlanSummary plan={plan} /> : null}
        {split.form !== null ? <ErrorBlock live compact error={split.form} title={apply.isError ? t("changeSource.dialog.notChanged", { domain }) : t("changeSource.dialog.notChecked")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The two rows that say how a git source is followed: the branch (pinned or not, with the way to pin or change it)
 * and the tags it deploys. Pinned or not is the question the row is for, so the way to answer it is in it.
 */
function branchRows(t: ReturnType<typeof useT>, app: App): KeyValueItem[] {
  return [
    {
      label: t("changeSource.branch"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          {app.branch ? <Mono>{app.branch}</Mono> : <span>{t("appSettings.general.noBranch")}</span>}
          <BranchActions app={app} />
        </span>
      ),
      mono: false,
      copy: typeof app.branch === "string" && app.branch !== "" ? app.branch : false,
      ...(app.branch ? {} : { hint: t("appSettings.general.noBranchHint") }),
    },
    {
      label: t("appSettings.general.tags"),
      value: (
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          {app.follow_tags ? <Mono>{app.follow_tags}</Mono> : <span>{t("appSettings.general.noTags")}</span>}
          <FollowTagsActions app={app} />
        </span>
      ),
      mono: false,
      copy: typeof app.follow_tags === "string" && app.follow_tags !== "" ? app.follow_tags : false,
      hint: app.follow_tags ? t("appSettings.general.tagsHint") : t("appSettings.general.noTagsHint"),
    },
  ];
}

/**
 * Where the code comes from: the repository or directory an application is built from, and a way
 * to point it at another one without creating it again (a repository that moved, or a team that
 * changed code host). The new source is checked against the remote first and what the change does
 * not carry is listed; nothing is built until the application is updated. The same as
 * `noust app source change`.
 */
export function ChangeSourceCard({ app }: { app: App }) {
  const t = useT();
  const queryClient = useQueryClient();
  const domain = app.domain;
  const fit = canChangeSource(app);
  const [open, setOpen] = useState(false);
  const [changed, setChanged] = useState<SourceChange | null>(null);
  const build = useMutation({
    mutationFn: () => request("post", "/api/jobs/update", { body: { domain, force: true } }),
    onSuccess: () => announce(t("changeSource.done.queued", { domain })),
    onError: (error: unknown) => reportActionError(t("changeSource.done.queueFailed", { domain }), error),
  });
  const title = t("changeSource.title");
  const description = t("changeSource.description");
  const recorded = app.source ?? "";
  // Only a git source has branches to pin and tags to follow; a local directory has neither.
  const git = recorded !== "" && !recorded.startsWith("/");

  // The branch it deploys from and the tags it follows are said, and pinned or followed, where the source is said: also for a
  // stack or a container built from a repository, which cannot change its source here but can pin its branch.
  const branchAndTags: KeyValueItem[] = git && changed === null ? branchRows(t, app) : [];

  if (fit !== "yes") {
    return (
      <Card title={title} icon={SETTINGS_ICONS.source} description={description}>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">
          {fit === "inPlace"
            ? t("changeSource.unsupportedInPlace")
            : fit === "preview"
              ? t("changeSource.unsupportedPreview")
              : fit === "push"
                ? t("changeSource.unsupportedPush")
                : t("changeSource.unsupportedType", { type: app.app_type ?? "" })}
        </p>
        {branchAndTags.length > 0 ? <KeyValueList items={branchAndTags} /> : null}
      </Card>
    );
  }
  return (
    <Card title={title} icon={SETTINGS_ICONS.source} description={description}>
      <KeyValueList
        items={[
          { label: t("changeSource.current"), value: changed?.target ?? (recorded === "" ? t("changeSource.none") : recorded) },
          ...(changed !== null || !git ? [{ label: t("changeSource.branch"), value: changed ? (changed.target_branch ?? "-") : (app.branch ?? "-") }] : branchAndTags),
        ]}
      />
      {changed !== null ? (
        <Notice
          tone="info"
          title={t("changeSource.done.title", { domain })}
          action={
            <Button size="sm" loading={build.isPending} onClick={() => build.mutate()}>
              {t("common.words.update")}
            </Button>
          }
        >
          {t("changeSource.done.body")}
        </Notice>
      ) : null}
      <div>
        <Button icon={<SETTINGS_ICONS.edit aria-hidden="true" />} size="sm" onClick={() => setOpen(true)}>
          {t("changeSource.open")}
        </Button>
      </div>
      {/* Mounted per opening, so each change starts from empty fields and no stale check. */}
      {open ? (
        <ChangeDialog
          domain={domain}
          branch={changed?.target_branch ?? app.branch ?? null}
          open
          onOpenChange={setOpen}
          onChanged={(done) => {
            setChanged(done);
            setOpen(false);
            void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
            void queryClient.invalidateQueries({ queryKey: sourceKeys.deployKey(domain) });
            announce(t("changeSource.changed", { domain, source: done.target }));
          }}
        />
      ) : null}
    </Card>
  );
}
