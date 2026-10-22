import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { MAX_TAGS, tagsOf, tagsProblem, tagsText } from "./appTags";

/** The tags a database or a service carries, as chips under its name; nothing when it carries none. */
export function TagChips({ tags }: { tags: readonly string[] | undefined }) {
  const t = useT();
  if (tags === undefined || tags.length === 0) return null;
  return (
    <ul aria-label={t("appTags.resource.chips")} className="flex min-w-0 flex-wrap items-center gap-1">
      {tags.map((tag) => (
        <li key={tag}>
          <Badge mono>{tag}</Badge>
        </li>
      ))}
    </ul>
  );
}

export interface ResourceTagsDialogProps {
  /** What is being tagged, as the dialog names it: the database `shop`, the service `worker`. */
  name: string;
  /** What kind of thing it is, for the title ("database", "service"). */
  noun: string;
  /** The tags it carries now. */
  tags: readonly string[];
  /** Saves exactly these tags; the server checks them again. */
  save: (tags: string[]) => Promise<unknown>;
  /** Called once saved, to read what shows the tags again. */
  onSaved: () => void;
  onClose: () => void;
}

/**
 * Editing the tags of a database or a service: the same words and the same rules as an app's
 * (`appTags`), saved at once. The tags decide what a filter and a restart by tag reach; nothing
 * is restarted by saving them.
 */
export function ResourceTagsDialog({ name, noun, tags, save, onSaved, onClose }: ResourceTagsDialogProps) {
  const t = useT();
  const formId = useId();
  const [text, setText] = useState(tagsText(tags));
  const [submitted, setSubmitted] = useState(false);
  const problem = tagsProblem(text, t.locale);

  const mutation = useMutation({
    mutationFn: (next: string[]) => save(next),
    onSuccess: () => {
      onSaved();
      announce(t("appTags.resource.saved", { name }));
      onClose();
    },
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (problem !== null || mutation.isPending) return;
    mutation.mutate(tagsOf(text));
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !mutation.isPending) onClose();
      }}
      size="sm"
      title={t("appTags.resource.title", { noun, name })}
      description={t("appTags.resource.description", { max: MAX_TAGS })}
      footer={
        <>
          <Button disabled={mutation.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
            {t("appTags.dialog.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field label={t("appTags.dialog.label")} description={t("appTags.dialog.fieldDescription")} error={submitted ? problem : null} optional>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder={t("appTags.dialog.placeholder")} value={text} onValueChange={setText} />
        </Field>
        {mutation.isError ? <ErrorBlock live compact error={mutation.error} title={t("appTags.resource.failed", { name })} /> : null}
      </form>
    </Dialog>
  );
}
