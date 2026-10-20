import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { acknowledgeAlert, alertKeys, handBackAlert } from "../../api/queries/alerts";
import type { OverviewAttentionItem } from "../../api/queries/overview";
import { Button, buttonClassName } from "../../components/ui/Button";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { attentionAction } from "../overview/attentionTarget";
import type { AlertAction, OpenAction } from "../overview/attentionTarget";

/** The button of an item that opens a screen: its word, and the same word read with what it is about. */
function wordsOf(t: T, action: OpenAction): { label: string; aria: string } {
  const { name } = action;
  switch (action.kind) {
    case "viewLog":
      return { label: t("overview.attention.viewLog"), aria: t("overview.attention.viewLogAria", { domain: name }) };
    case "diagnose":
      return { label: t("overview.attention.diagnose"), aria: t("overview.attention.diagnoseAria", { domain: name }) };
    case "renew":
      return { label: t("overview.attention.certificate"), aria: t("overview.attention.certificateAria", { domain: name }) };
    case "openService":
      return { label: t("overview.attention.openService"), aria: t("overview.attention.openServiceAria", { name }) };
    case "openFinding":
      return { label: t("overview.attention.openFinding"), aria: t("overview.attention.openFindingAria", { name }) };
    case "openProbes":
      return { label: t("inbox.actions.openProbes"), aria: t("inbox.actions.openProbesAria", { name }) };
    case "review":
      return { label: t("inbox.actions.review"), aria: t("inbox.actions.reviewAria", { id: name }) };
    case "openUpdates":
      return { label: t("inbox.actions.openUpdates"), aria: t("inbox.actions.openUpdatesAria") };
    case "reviewRecipe":
      return { label: t("inbox.actions.reviewRecipe"), aria: t("inbox.actions.reviewRecipeAria", { domain: name }) };
  }
}

/**
 * Takes a firing alert, or hands it back. It stops the rule's reminders and tells the channels who
 * is on it, and it never resolves the alert: the row changes in place, which is the answer, so
 * there is no toast for it (a toast is for what is not on screen).
 */
function AlertButton({ action }: { action: AlertAction }) {
  const t = useT();
  const queryClient = useQueryClient();
  const reopening = action.kind === "reopen";
  const change = useMutation({
    mutationFn: () => (reopening ? handBackAlert(action.rule, action.subject) : acknowledgeAlert(action.rule, action.subject)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: alertKeys.rules }),
    onError: (error) => {
      reportActionError(t(reopening ? "inbox.actions.reopenFailed" : "inbox.actions.acknowledgeFailed"), error);
    },
  });
  return (
    <Button
      size="sm"
      variant={reopening ? "ghost" : "secondary"}
      loading={change.isPending}
      aria-label={t(reopening ? "inbox.actions.reopenAria" : "inbox.actions.acknowledgeAria", { subject: action.name })}
      onClick={() => {
        change.mutate();
      }}
    >
      {t(reopening ? "inbox.actions.reopen" : "inbox.actions.acknowledge")}
    </Button>
  );
}

export interface AttentionActionButtonProps {
  item: OverviewAttentionItem;
  /** Called once the button's job is done: a link was followed. */
  onNavigate?: (() => void) | undefined;
}

/** The one button of a row that needs attention: the fix, or the screen that fixes it (2.7). */
export function AttentionActionButton({ item, onNavigate }: AttentionActionButtonProps) {
  const t = useT();
  const action = attentionAction(item);
  if (action === null) return null;
  if (action.mode === "alert") return <AlertButton action={action} />;
  const { label, aria } = wordsOf(t, action);
  const { to, params, search } = action.target;
  return (
    <Link
      to={to as never}
      {...(params !== undefined ? { params: params as never } : {})}
      {...(search !== undefined ? { search: search as never } : {})}
      aria-label={aria}
      {...(onNavigate ? { onClick: onNavigate } : {})}
      className={buttonClassName("secondary", "sm")}
    >
      {label}
    </Link>
  );
}
