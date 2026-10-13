import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../../api/client";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { MAX_TAGS, tagsOf, tagsProblem, tagsText } from "./appTags";

/** The tags an app carries, as chips, or "None". */
export function AppTagList({ tags }: { tags: readonly string[] }) {
  const t = useT();
  if (tags.length === 0) return <span>{t("appTags.row.none")}</span>;
  return (
    <ul aria-label={t("appTags.row.label")} className="flex min-w-0 flex-wrap items-center gap-1.5">
      {tags.map((tag) => (
        <li key={tag}>
          <Badge mono>{tag}</Badge>
        </li>
      ))}
    </ul>
  );
}

function TagsForm({ app, formId, onPending, onDone }: { app: App; formId: string; onPending: (pending: boolean) => void; onDone: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const [text, setText] = useState(tagsText(app.tags ?? []));
  const [submitted, setSubmitted] = useState(false);
  const problem = tagsProblem(text, t.locale);

  const save = useMutation({
    mutationFn: (tags: string[]) => request("put", "/api/apps/{domain}/tags", { params: { domain: app.domain }, body: { tags } }),
    onSuccess: (result) => {
      queryClient.setQueryData<App>(appKeys.detail(app.domain), (known) => (known ? { ...known, tags: result.tags } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      void queryClient.invalidateQueries({ queryKey: ["tags"] });
      announce(t("appTags.dialog.saved", { domain: app.domain }));
      onDone();
    },
    onSettled: () => onPending(false),
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (problem !== null || save.isPending) return;
    onPending(true);
    save.mutate(tagsOf(text));
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field label={t("appTags.dialog.label")} description={t("appTags.dialog.fieldDescription")} error={submitted ? problem : null} optional>
        <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder={t("appTags.dialog.placeholder")} value={text} onValueChange={setText} />
      </Field>
      {save.isError ? <ErrorBlock live compact error={save.error} title={t("appTags.dialog.failed", { domain: app.domain })} /> : null}
    </form>
  );
}

/** The button that edits an app's tags, and the dialog it opens. Saved at once; nothing is deployed. */
export function AppTagsActions({ app }: { app: App }) {
  const t = useT();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  return (
    <>
      <Button size="sm" variant="ghost" onClick={() => setOpen(true)}>
        {t("appTags.row.edit")}
      </Button>
      {open ? (
        <Dialog
          open
          onOpenChange={(next) => {
            if (!next && pending) return;
            setOpen(next);
          }}
          size="sm"
          title={t("appTags.dialog.title", { domain: app.domain })}
          description={t("appTags.dialog.description", { max: MAX_TAGS })}
          footer={
            <>
              <Button disabled={pending} onClick={() => setOpen(false)}>
                {t("variables.cancel")}
              </Button>
              <Button type="submit" form={formId} variant="primary" loading={pending}>
                {t("appTags.dialog.submit")}
              </Button>
            </>
          }
        >
          <TagsForm app={app} formId={formId} onPending={setPending} onDone={() => setOpen(false)} />
        </Dialog>
      ) : null}
    </>
  );
}
