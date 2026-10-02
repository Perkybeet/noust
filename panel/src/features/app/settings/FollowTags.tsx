/**
 * Deploying by tag (spec 3.2, section 1.9; `PATCH /api/apps/{domain}/follow-tags`, behind sudo
 * mode). An app can follow its branch, as always, or the tags that match a pattern (`v*`),
 * newest by semantic version and never backwards: deploy on push then deploys a published
 * release or a pushed tag, and ignores pushes to the branch. Saving the pattern deploys
 * nothing; the next tag does.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import type { App } from "../../../api/queries/apps";
import { appKeys } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { splitErrors } from "../../settings/formErrors";
import { useSudoFirst } from "./formParts";
import { settingsKeys } from "./queries";

/** Follows a pattern, or the branch again with null; what the app and the webhook say follows. */
function useFollow(domain: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pattern: string | null) => request("patch", "/api/apps/{domain}/follow-tags", { params: { domain }, body: { pattern } }),
    onSuccess: (result) => {
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, follow_tags: result.follow_tags ?? null } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      void queryClient.invalidateQueries({ queryKey: settingsKeys.webhook(domain) });
    },
  });
}

function FollowTagsDialog({ app, onOpenChange }: { app: App; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const domain = app.domain;
  const follow = useFollow(domain);
  const [pattern, setPattern] = useState(app.follow_tags ?? "v*");
  const [submitted, setSubmitted] = useState(false);
  const empty = pattern.trim() === "";
  const split = splitErrors(follow.error, ["follow_tags", "pattern"] as const, "pattern");
  const error = (submitted && empty ? t("appSettings.tags.required") : undefined) ?? split.fields.pattern ?? split.fields.follow_tags;
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (empty || follow.isPending) return;
    follow.mutate(pattern.trim(), {
      onSuccess: (result) => {
        announce(t("appSettings.tags.followed", { domain, pattern: result.follow_tags ?? pattern.trim() }));
        onOpenChange(false);
      },
    });
  };
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && follow.isPending) return;
        onOpenChange(next);
      }}
      title={app.follow_tags ? t("appSettings.tags.changeTitle") : t("appSettings.tags.followTitle")}
      description={t("appSettings.tags.dialogDescription")}
      footer={
        <>
          <Button disabled={follow.isPending} onClick={() => onOpenChange(false)}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form="follow-tags" variant="primary" loading={follow.isPending}>
            {t("appSettings.tags.action")}
          </Button>
        </>
      }
    >
      <form id="follow-tags" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("appSettings.tags.label")} description={t("appSettings.tags.fieldDescription")} error={error}>
          <Input mono value={pattern} onValueChange={(value: string) => setPattern(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {follow.isError && split.form !== null ? <ErrorBlock live compact error={split.form} title={t("appSettings.tags.notFollowed", { domain })} /> : null}
      </form>
    </Dialog>
  );
}

/** Beside the tags in General: follow a pattern, or change it and follow the branch again. */
export function FollowTagsActions({ app }: { app: App }) {
  const t = useT();
  const { node } = useNode();
  const sudoFirst = useSudoFirst();
  const domain = app.domain;
  const unfollow = useFollow(domain);
  const [open, setOpen] = useState(false);
  const [stopping, setStopping] = useState(false);
  const following = Boolean(app.follow_tags);
  return (
    <span className="flex flex-wrap items-center gap-2">
      <Button size="sm" onClick={() => sudoFirst(() => setOpen(true), t("appSettings.tags.notFollowed", { domain }))}>
        {following ? t("appSettings.tags.change") : t("appSettings.tags.follow")}
      </Button>
      {following ? (
        <Button size="sm" onClick={() => sudoFirst(() => setStopping(true), t("appSettings.tags.notStopped", { domain }))}>
          {t("appSettings.tags.stop")}
        </Button>
      ) : null}
      {/* Mounted per opening, so the field starts from the pattern followed now. */}
      {open ? <FollowTagsDialog app={app} onOpenChange={setOpen} /> : null}
      <ConfirmDialog
        open={stopping}
        onOpenChange={setStopping}
        friction="simple"
        destructive={false}
        server={node}
        title={t("appSettings.tags.stopTitle", { domain })}
        description={t("appSettings.tags.stopDescription")}
        actionLabel={t("appSettings.tags.stopAction")}
        onConfirm={async () => {
          await unfollow.mutateAsync(null);
          announce(t("appSettings.tags.stopped", { domain }));
        }}
      />
    </span>
  );
}
