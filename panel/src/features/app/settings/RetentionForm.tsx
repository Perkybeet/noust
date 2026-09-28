import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import type { ResponseOf } from "../../../api/client";
import { ElevationCancelledError } from "../../../api/errors";
import { appKeys } from "../../../api/queries/apps";
import type { App } from "../../../api/queries/apps";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { translate } from "../../../i18n/translate";
import { reportActionError } from "../../apps/useAppActions";
import { useConfirmItsYou } from "../useDeleteApp";
import { RETENTION_MAX, RETENTION_MIN, parseRetention } from "./healthCheck";
import { PANEL } from "./panel";

type RetentionResult = ResponseOf<"/api/apps/{domain}/releases/retention", "patch">;

/** What saving did: how many it keeps now and which releases it removed, by id. */
export function retentionOutcome(result: Pick<RetentionResult, "keep_releases" | "pruned">, locale: Locale = getLocale()): string {
  const kept = translate(locale, "appSettings.releases.releaseCount", { count: result.keep_releases });
  if (result.pruned.length === 0) return translate(locale, "appSettings.retention.outcomeNoneRemoved", { kept });
  const removed = translate(locale, "appSettings.releases.releaseCount", { count: result.pruned.length });
  return translate(locale, "appSettings.retention.outcomeRemoved", { kept, removed, list: result.pruned.join(", ") });
}

/**
 * How many releases an app on releases keeps. Saving prunes at once rather than at the next
 * deploy, which deletes release directories, so it asks who it is first; the release serving
 * and the one before it are always kept, whatever the number.
 */
export function RetentionForm({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const headingId = useId();
  const queryClient = useQueryClient();
  const confirmItsYou = useConfirmItsYou();
  const current = String(app.keep_releases);
  const [baseline, setBaseline] = useState(current);
  const [draft, setDraft] = useState(current);
  const [submitted, setSubmitted] = useState(false);
  const [saved, setSaved] = useState<RetentionResult | null>(null);

  if (current !== baseline) {
    setBaseline(current);
    if (draft.trim() === baseline) setDraft(current);
  }

  const parsed = parseRetention(draft, t.locale);
  const dirty = draft.trim() !== current;

  const save = useMutation({
    mutationFn: (keep: number) => request("patch", "/api/apps/{domain}/releases/retention", { params: { domain }, body: { keep } }),
    onSuccess: (result) => {
      setSaved(result);
      setSubmitted(false);
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, keep_releases: result.keep_releases } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain) });
      toast.success(t("appSettings.retention.savedToast", { domain }));
    },
  });

  // The store's refusal of a number is about this one field: it goes under it.
  const refusal =
    save.isError && isApiError(save.error) && save.error.status === 400
      ? save.error.hint
        ? `${save.error.detail}. ${save.error.hint}`
        : save.error.detail
      : undefined;
  const fieldError = submitted && parsed.error !== null ? parsed.error : refusal;

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (parsed.keep === null || !dirty || save.isPending) return;
    const keep = parsed.keep;
    confirmItsYou().then(
      () => {
        save.mutate(keep);
      },
      (error: unknown) => {
        if (!(error instanceof ElevationCancelledError)) reportActionError(t("appSettings.retention.notSavedFor", { domain }), error);
      },
    );
  };

  return (
    <section aria-labelledby={headingId} className={`${PANEL} flex flex-col gap-4 px-4 py-4 sm:px-5`}>
      <header className="flex flex-col gap-1">
        <h3 id={headingId} className="text-14 font-medium text-fg">
          {t("appSettings.retention.title")}
        </h3>
        <p className="text-13 text-pretty text-fg-muted">{t("appSettings.retention.description")}</p>
      </header>
      <form onSubmit={submit} noValidate className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <Field
            label={t("appSettings.retention.keepLabel")}
            description={t("appSettings.retention.keepDescription", { min: RETENTION_MIN, max: RETENTION_MAX })}
            error={fieldError}
            className="w-36"
          >
            <Input
              mono
              inputMode="numeric"
              autoComplete="off"
              suffix={t("appSettings.retention.unitSuffix")}
              value={draft}
              onValueChange={(value: string) => {
                setDraft(value);
                setSaved(null);
                if (save.isError) save.reset();
              }}
            />
          </Field>
          <Button type="submit" variant="primary" disabled={!dirty} loading={save.isPending} className="sm:mt-6">
            {t("appSettings.save")}
          </Button>
        </div>
        {save.isError && fieldError === undefined ? <ErrorBlock live compact error={save.error} title={t("appSettings.retention.saveFailed")} /> : null}
        {saved !== null ? (
          <p role="status" className="flex items-start gap-2 rounded-control border border-ok/40 bg-ok-soft px-3 py-2.5 text-13 text-pretty text-fg">
            <CircleCheck aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-ok" />
            <span>{retentionOutcome(saved, t.locale)}</span>
          </p>
        ) : null}
      </form>
    </section>
  );
}
