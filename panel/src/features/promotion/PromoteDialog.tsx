import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { appKeys } from "../../api/queries/apps";
import { promotionQuery } from "../../api/queries/promotion";
import type { PromotionTarget } from "../../api/queries/promotion";
import { announce } from "../../app/Announcer";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

function targetLabel(target: PromotionTarget): string {
  const where = target.environment ? ` (${target.environment})` : "";
  return `${target.domain}${where}`;
}

function targetHint(t: T, target: PromotionTarget): string {
  return target.release ? t("promotion.promote.targetRuns", { release: target.release }) : t("promotion.promote.targetEmpty");
}

interface FormProps {
  domain: string;
  formId: string;
  release: string;
  commit: string | null;
  targets: readonly PromotionTarget[];
  onPending: (pending: boolean) => void;
  onDone: () => void;
}

function PromoteForm({ domain, formId, release, commit, targets, onPending, onDone }: FormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState(targets[0]?.domain ?? "");
  const [allowSchema, setAllowSchema] = useState(false);

  const promote = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/promote", {
        params: { domain },
        body: { target, release, schema_changed_ok: allowSchema },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(target) });
      announce(t("promotion.promote.queued", { release, target }));
      toast.success(t("promotion.promote.queued", { release, target }));
      onDone();
    },
    onSettled: () => onPending(false),
  });

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (promote.isPending || target === "") return;
    onPending(true);
    promote.mutate();
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <KeyValueList
        items={[
          { label: t("promotion.promote.release"), value: release },
          ...(commit ? [{ label: t("promotion.promote.commit"), value: commit.slice(0, 7), copy: commit }] : []),
        ]}
      />
      <Field label={t("promotion.promote.targetLabel")} description={t("promotion.promote.targetDescription")} nativeLabel={false}>
        <Select
          aria-label={t("promotion.promote.targetLabel")}
          value={target}
          options={targets.map((option) => ({ value: option.domain, label: targetLabel(option), hint: targetHint(t, option) }))}
          onValueChange={setTarget}
        />
      </Field>
      <Disclosure label={t("promotion.promote.moreOptions")}>
        <Checkbox label={t("promotion.promote.schemaLabel")} description={t("promotion.promote.schemaDescription")} checked={allowSchema} onCheckedChange={setAllowSchema} />
      </Disclosure>
      {promote.isError ? <ErrorBlock live compact error={promote.error} title={t("promotion.promote.failed")} /> : null}
    </form>
  );
}

export interface PromoteDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Puts the release that serves an app on its twin in another environment of the same project,
 * without building it again (spec B-78). Only the apps of the project, built from the same
 * repository and of the same type, are offered; the server decides again, and what the target
 * served is put back if the release does not pass its startup check.
 */
export function PromoteDialog({ domain, open, onOpenChange }: PromoteDialogProps) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  const promotion = useQuery(promotionQuery(domain, open));
  const data = promotion.data;
  const release = data?.release ?? null;
  const ready = data !== undefined && release !== null && data.targets.length > 0;
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
      size="md"
      title={t("promotion.promote.title", { domain })}
      description={t("promotion.promote.description", { domain })}
      footer={
        <>
          <Button disabled={pending} onClick={() => onOpenChange(false)}>
            {t("variables.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending} disabled={!ready}>
            {t("promotion.promote.submit")}
          </Button>
        </>
      }
    >
      {promotion.isPending ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">{t("promotion.promote.loading", { domain })}</span>
          <Skeleton className="h-16 w-full rounded-control" />
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      ) : promotion.isError ? (
        <ErrorBlock compact error={promotion.error} title={t("promotion.promote.loadFailed", { domain })} onRetry={() => void promotion.refetch()} retrying={promotion.isRefetching} />
      ) : data === undefined ? null : release === null ? (
        <Notice tone="warning" title={t("promotion.promote.noRelease", { domain })}>
          {t("promotion.promote.noReleaseHint")}
        </Notice>
      ) : data.targets.length === 0 ? (
        <Notice title={t("promotion.promote.noTargetsTitle")}>{t("promotion.promote.noTargetsHint")}</Notice>
      ) : (
        <PromoteForm domain={domain} formId={formId} release={release} commit={data.commit ?? null} targets={data.targets} onPending={setPending} onDone={() => onOpenChange(false)} />
      )}
    </Dialog>
  );
}
