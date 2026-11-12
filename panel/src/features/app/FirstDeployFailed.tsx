import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { dismissFirstDeploy, firstDeployKeys } from "../../api/queries/firstDeploys";
import type { FirstDeploy } from "../../api/queries/firstDeploys";
import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Can, GuardedButton } from "../../components/page/permissions";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button, buttonClassName } from "../../components/ui/Button";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { leadingSentence, serverSentence } from "../../lib/serverSentence";
import { useTypeName } from "../apps/data";
import { reportActionError } from "../apps/useAppActions";
import { saveDraft } from "../new-app/draft";
import { shortSource } from "../new-app/shortSource";
import { retryOf, sourceHidesASecret } from "./firstDeployRetry";

/**
 * The page of a domain whose first deploy failed. A first deploy that fails undoes every step it took, the
 * application's own row included, so what an application's page would show is gone and the operator, who is looking at
 * this page, would be told there is nothing here. This says what happened instead: when, in the system's own words
 * (the fix, when there is one, above them), and the two things to do about it, try again with what had been filled in
 * or put it away. The server keeps the failure for two weeks, and what needs attention lists it until then.
 */
export function FirstDeployFailed({ domain, attempt, header }: { domain: string; attempt: FirstDeploy; header: PageHeaderProps }) {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const typeName = useTypeName();

  const dismiss = useMutation({
    mutationFn: () => dismissFirstDeploy(attempt.job_id),
    onSuccess: async () => {
      // The page itself changes (it is the list of applications now), so there is nothing to announce.
      await queryClient.invalidateQueries({ queryKey: firstDeployKeys.all });
      void navigate({ to: "/apps" });
    },
    onError: (error) => {
      reportActionError(t("appPages.firstFailed.dismissFailed"), error);
    },
  });

  const retry = (): void => {
    const again = retryOf(attempt);
    saveDraft(again.draft);
    void navigate({ to: "/apps/new", search: again.search as never });
  };

  const error = attempt.error ?? "";
  const coded = attempt.error_coded ?? null;
  // The fix of Noust's own sentence, in this console's language, above what the system said; a tool's own error has none.
  const fix = serverSentence(t.locale, coded, null, "hint");
  const type = attempt.app_type !== null && attempt.app_type !== undefined && attempt.app_type !== "auto" ? typeName(attempt.app_type) : null;
  const what =
    attempt.origin === "recipe" && attempt.recipe !== null && attempt.recipe !== undefined
      ? t("appPages.firstFailed.recipe", { name: attempt.recipe })
      : attempt.source !== null && attempt.source !== undefined && attempt.source !== ""
        ? shortSource(attempt.source)
        : null;
  const facts = [type, what, attempt.branch !== null && attempt.branch !== undefined && attempt.branch !== "" ? t("appPages.firstFailed.branch", { branch: attempt.branch }) : null].filter((part) => part !== null);

  return (
    <DetailPage
      header={{
        ...header,
        status: <StatusPill state="failed" label={t("appPages.firstFailed.state")} />,
        statusBelow: true,
        ...(facts.length > 0
          ? {
              meta: (
                <span className="flex max-w-full min-w-0 items-center gap-x-3">
                  {facts.map((fact) => (
                    <span key={fact} className="min-w-0 truncate" translate="no">
                      {fact}
                    </span>
                  ))}
                </span>
              ),
            }
          : {}),
        // Putting it away and deploying again are what whoever may manage applications does: the failure is read by anyone who
        // may read them, and a role that cannot sees the way on, locked, and why.
        secondaryActions: (
          <Can permission="apps.manage">
            <Button
              loading={dismiss.isPending}
              onClick={() => {
                dismiss.mutate();
              }}
            >
              {t("appPages.firstFailed.dismiss")}
            </Button>
          </Can>
        ),
        primaryAction: (
          <GuardedButton permission="apps.manage" variant="primary" onClick={retry}>
            {t("appPages.firstFailed.retry")}
          </GuardedButton>
        ),
      }}
    >
      <div className="flex min-w-0 max-w-measure flex-col gap-4">
        <ErrorBlock
          title={t("appPages.firstFailed.title", { domain })}
          error={{ detail: leadingSentence(t.locale, coded, error) }}
          {...(fix !== "" ? { hint: fix } : {})}
          action={
            <Link to="/activity/tasks/$id" params={{ id: attempt.job_id }} className={buttonClassName("secondary", "sm")}>
              {t("appPages.firstFailed.viewLog")}
            </Link>
          }
        />
        <p className="text-13 text-pretty text-fg-muted">
          {attempt.finished_at !== null && attempt.finished_at !== undefined ? (
            <>
              {t.rich("appPages.firstFailed.when", { time: <RelativeTime key="time" value={attempt.finished_at} /> })}{" "}
            </>
          ) : null}
          {t("appPages.firstFailed.nothing")}
        </p>
        {sourceHidesASecret(attempt) ? <p className="text-13 text-pretty text-fg-muted">{t("appPages.firstFailed.sourceMasked")}</p> : null}
      </div>
    </DetailPage>
  );
}
