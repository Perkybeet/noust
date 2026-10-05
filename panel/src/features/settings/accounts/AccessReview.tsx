import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck } from "lucide-react";
import { useId, useState } from "react";

import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Textarea } from "../../../components/ui/Textarea";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { accessReviewQuery, attestAccessReview, trailKeys } from "../../audit/api";
import type { AccessReview } from "../../audit/api";

/** The newest attestation on record: when, and by whom, as the audit log wrote it. */
export function lastAttestation(review: Pick<AccessReview, "reviews">): { when: string | null; who: string | null } {
  const [latest] = review.reviews ?? [];
  if (latest === undefined) return { when: null, who: null };
  const when = typeof latest["ts"] === "string" ? latest["ts"] : typeof latest["timestamp"] === "string" ? latest["timestamp"] : null;
  const actor = latest["actor"];
  const who =
    typeof actor === "string" ? actor : typeof actor === "object" && actor !== null && typeof (actor as { name?: unknown }).name === "string" ? (actor as { name: string }).name : null;
  return { when, who };
}

function AttestDialog({ t, review, onClose }: { t: T; review: AccessReview; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [notes, setNotes] = useState("");
  const formId = useId();
  const attest = useMutation({
    mutationFn: () => attestAccessReview(review.digest, notes.trim()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: trailKeys.accessReview });
      toast.success(t("accounts.review.recordedToast"));
      onClose();
    },
  });
  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !attest.isPending) onClose();
      }}
      size="md"
      title={t("accounts.review.dialogTitle")}
      description={t("accounts.review.dialogDescription")}
      footer={
        <>
          <Button disabled={attest.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={attest.isPending}>
            {t("accounts.review.submit")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (!attest.isPending) attest.mutate();
        }}
      >
        <KeyValueList
          items={[
            { label: t("accounts.review.accounts"), value: formatCount(review.accounts.length, t.locale), copy: false },
            { label: t("accounts.review.conflicts"), value: formatCount(review.conflicts?.length ?? 0, t.locale), copy: false },
            { label: t("accounts.review.exceptions"), value: formatCount(review.exceptions?.length ?? 0, t.locale), copy: false },
            ...(review.tokens_without_owner !== null && review.tokens_without_owner !== undefined
              ? [{ label: t("accounts.review.orphanTokens"), value: formatCount(review.tokens_without_owner, t.locale), copy: false as const }]
              : []),
            { label: t("accounts.review.digest"), value: review.digest },
          ]}
        />
        <Field label={t("accounts.review.notes")} optional description={t("accounts.review.notesHint")}>
          <Textarea
            rows={3}
            maxLength={4000}
            value={notes}
            onChange={(event) => {
              setNotes(event.target.value);
            }}
          />
        </Field>
        {attest.isError ? <ErrorBlock live compact error={attest.error} title={t("accounts.review.failed")} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The periodic access review (ENS op.acc.4.4): a security officer reads who holds which role
 * and records that they did, over the exact list they saw. The record is the evidence.
 */
export function AccessReviewSection({ canManage }: { canManage: boolean }) {
  const t = useT();
  const query = useQuery(accessReviewQuery());
  const [attesting, setAttesting] = useState(false);
  const last = query.data !== undefined ? lastAttestation(query.data) : null;
  return (
    <Section
      title={t("accounts.review.title")}
      description={t("accounts.review.description")}
      loading={last === null && !query.isError}
      {...(canManage && query.data !== undefined
        ? {
            actions: (
              <Button size="sm" icon={<ClipboardCheck aria-hidden="true" />} onClick={() => setAttesting(true)}>
                {t("accounts.review.record")}
              </Button>
            ),
          }
        : {})}
    >
      {query.isError ? (
        <ErrorBlock compact error={query.error} title={t("accounts.review.loadFailed")} onRetry={() => void query.refetch()} />
      ) : (
        <Card padding="sm">
          {last === null ? (
            <KeyValueListSkeleton rows={2} />
          ) : (
            <KeyValueList
              items={[
                {
                  label: t("accounts.review.last"),
                  value: last.when === null ? t("accounts.review.never") : <RelativeTime value={last.when} />,
                  mono: false,
                  copy: false,
                },
                ...(last.who !== null ? [{ label: t("accounts.review.by"), value: last.who }] : []),
              ]}
            />
          )}
        </Card>
      )}
      {attesting && query.data !== undefined ? <AttestDialog t={t} review={query.data} onClose={() => setAttesting(false)} /> : null}
    </Section>
  );
}
