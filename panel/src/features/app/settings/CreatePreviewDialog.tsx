import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { previewsKey, pullRequestsQuery } from "../../../api/queries/previews";
import type { PullRequest } from "../../../api/queries/previews";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ChoiceCards } from "../../../components/ui/ChoiceCards";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { previewName } from "./previews";

type Source = "pr" | "branch" | "tag";

export interface CreatePreviewDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Whether the repository is on GitHub, where the open pull requests can be read: the choice
   * starts on them there, and on a branch anywhere else, so nothing asks a host that cannot answer.
   */
  onGithub: boolean;
}

/** What is worth knowing of an open pull request before building it, in one line. */
function flags(t: T, pull: PullRequest): string {
  return [
    t("previewHand.create.prMeta", { author: pull.author, branch: pull.branch }),
    pull.draft ? t("previewHand.create.prDraft") : null,
    pull.from_fork ? t("previewHand.create.prFork") : null,
    !pull.trusted && !pull.from_fork ? t("previewHand.create.prNotTrusted") : null,
    pull.preview !== null && pull.preview !== undefined ? t("previewHand.create.prHasPreview") : null,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

interface FormProps {
  domain: string;
  onGithub: boolean;
  formId: string;
  onPending: (pending: boolean) => void;
  onDone: () => void;
}

/** Mounted fresh for every opening, so nothing of an abandoned attempt is there the next time. */
function CreatePreviewForm({ domain, onGithub, formId, onPending, onDone }: FormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [source, setSource] = useState<Source>(onGithub ? "pr" : "branch");
  const [pull, setPull] = useState<string>("");
  const [branch, setBranch] = useState("");
  const [tag, setTag] = useState("");
  const [allowFork, setAllowFork] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const pulls = useQuery({ ...pullRequestsQuery(domain), enabled: source === "pr" });

  const create = useMutation({
    mutationFn: (body: { pull_request?: number; branch?: string; tag?: string; allow_fork: boolean }) =>
      request("post", "/api/apps/{domain}/previews", { params: { domain }, body }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: previewsKey(domain) });
      announce(t("previewHand.create.queued", { name: previewName(result.preview) }));
      onDone();
    },
    onSettled: () => onPending(false),
  });

  const chosen = pulls.data?.pull_requests.find((candidate) => String(candidate.number) === pull);
  const missing =
    source === "pr" ? (chosen === undefined ? t("previewHand.create.needPr") : null) : source === "branch" ? (branch.trim() === "" ? t("previewHand.create.needBranch") : null) : tag.trim() === "" ? t("previewHand.create.needTag") : null;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (missing !== null || create.isPending) return;
    onPending(true);
    create.mutate(
      source === "pr" && chosen !== undefined
        ? { pull_request: chosen.number, allow_fork: chosen.from_fork && allowFork }
        : source === "branch"
          ? { branch: branch.trim(), allow_fork: false }
          : { tag: tag.trim(), allow_fork: false },
    );
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <SegmentedControl<Source>
        label={t("previewHand.create.sourceLegend")}
        value={source}
        options={[
          { value: "pr", label: t("previewHand.create.pr") },
          { value: "branch", label: t("previewHand.create.branch") },
          { value: "tag", label: t("previewHand.create.tag") },
        ]}
        onValueChange={(next) => {
          setSource(next);
          create.reset();
        }}
      />
      {source === "branch" ? (
        <Field label={t("previewHand.create.branchLabel")} description={t("previewHand.create.branchDescription")} error={submitted ? missing : null}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="feature/login" value={branch} onValueChange={setBranch} />
        </Field>
      ) : null}
      {source === "tag" ? (
        <Field label={t("previewHand.create.tagLabel")} description={t("previewHand.create.tagDescription")} error={submitted ? missing : null}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="v1.4.0" value={tag} onValueChange={setTag} />
        </Field>
      ) : null}
      {source === "pr" ? (
        pulls.isPending ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">{t("previewHand.create.prLoading")}</span>
            <Skeleton className="h-14 w-full rounded-control" />
            <Skeleton className="h-14 w-full rounded-control" />
          </div>
        ) : pulls.isError ? (
          <ErrorBlock compact error={pulls.error} title={t("previewHand.create.prFailedTitle")} hint={t("previewHand.create.prFailedHint")} onRetry={() => void pulls.refetch()} retrying={pulls.isRefetching} />
        ) : pulls.data.pull_requests.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("previewHand.create.prNone")}</p>
        ) : (
          <div className="flex flex-col gap-3">
            <div className="max-h-72 overflow-y-auto scroll-thin">
              <ChoiceCards
                legend={t("previewHand.create.prListLabel")}
                value={pull}
                onValueChange={setPull}
                options={pulls.data.pull_requests.map((candidate) => ({
                  value: String(candidate.number),
                  label: `#${candidate.number} ${candidate.title}`,
                  untranslated: true,
                  description: flags(t, candidate),
                }))}
              />
            </div>
            {submitted && missing !== null ? <p className="text-13 text-fail">{missing}</p> : null}
            {chosen?.from_fork === true ? (
              <Checkbox label={t("previewHand.create.allowForkLabel")} description={t("previewHand.create.allowForkDescription")} checked={allowFork} onCheckedChange={setAllowFork} />
            ) : null}
          </div>
        )
      ) : null}
      {create.isError ? <ErrorBlock live compact error={create.error} title={t("previewHand.create.failed")} /> : null}
    </form>
  );
}

/**
 * Makes a preview by hand, from a branch, a tag or one of the repository's open pull requests
 * (read when that choice is made, never before, since it asks the code host). A pull request from
 * a fork is built only when the operator says so: its code runs on this server.
 */
export function CreatePreviewDialog({ domain, open, onOpenChange, onGithub }: CreatePreviewDialogProps) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
      size="md"
      title={t("previewHand.create.title")}
      description={t("previewHand.create.description")}
      footer={
        <>
          <Button disabled={pending} onClick={() => onOpenChange(false)}>
            {t("variables.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {t("previewHand.create.submit")}
          </Button>
        </>
      }
    >
      {open ? <CreatePreviewForm domain={domain} onGithub={onGithub} formId={formId} onPending={setPending} onDone={() => onOpenChange(false)} /> : null}
    </Dialog>
  );
}
