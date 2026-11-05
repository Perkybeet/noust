import { useNavigate } from "@tanstack/react-router";

import type { App, DeletionPlan } from "../../api/queries/apps";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { isAdopted } from "./adopted";
import { dropsOf } from "./deletion";
import type { DeleteChoice, DeleteOptions } from "./deletion";
import { useDeleteApp } from "./useDeleteApp";

/**
 * What a deletion removes and what it keeps, as two sentences the question says before the domain is typed. The
 * service and the site always go and backups always stay; everything else is what the boxes of the page said, so the
 * question is the plan and never a promise the choice does not keep.
 */
export function deletionSentences(t: T, choice: DeleteChoice, plan: Pick<DeletionPlan, "keeps_directory" | "has_volumes" | "databases">): { removes: string; keeps: string } {
  const list = new Intl.ListFormat(t.locale, { style: "long", type: "conjunction" });
  const drops = dropsOf(plan).length > 0;
  const removes = [
    t("appPages.deleteApp.things.service"),
    t("appPages.deleteApp.things.site"),
    ...(choice.files ? [t(plan.keeps_directory ? "appPages.deleteApp.things.folder" : "appPages.deleteApp.things.files")] : []),
    ...(choice.volumes && plan.has_volumes ? [t("appPages.deleteApp.things.volumes")] : []),
    ...(choice.databases && drops ? [t("appPages.deleteApp.things.databases")] : []),
    ...(choice.certificate ? [t("appPages.deleteApp.things.certificate")] : []),
  ];
  const keeps = [
    ...(choice.files ? [] : [t(plan.keeps_directory ? "appPages.deleteApp.things.folder" : "appPages.deleteApp.things.files")]),
    ...(plan.has_volumes && !choice.volumes ? [t("appPages.deleteApp.things.volumes")] : []),
    ...(plan.databases.length > 0 && !(choice.databases && drops) ? [t("appPages.deleteApp.things.databases")] : []),
    ...(choice.certificate ? [] : [t("appPages.deleteApp.things.certificate")]),
    t("appPages.deleteApp.things.backups"),
  ];
  return { removes: t("appPages.deleteApp.removes", { things: list.format(removes) }), keeps: t("appPages.deleteApp.keeps", { things: list.format(keeps) }) };
}

export interface DeleteAppDialogProps {
  app: Pick<App, "domain" | "app_type">;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** What the page of deleting it ticked, and what the server said deleting it would touch. */
  choice?: DeleteChoice;
  plan?: Pick<DeletionPlan, "path" | "keeps_directory" | "has_volumes" | "databases">;
  options?: DeleteOptions;
}

/**
 * Deleting an application, with the friction losing it deserves: the domain typed out. What goes with it besides
 * the service and the site is chosen on the application's own page (`pages/DeletePage`), where each thing that holds
 * data has its box, none ticked; this question only says, from those boxes, what will go and what will stay, and
 * asks for the name. Queued as a job; the page goes back to the list.
 */
export function DeleteAppDialog({ app, open, onOpenChange, choice, plan, options }: DeleteAppDialogProps) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const domain = app.domain;
  const remove = useDeleteApp(domain);

  const queued = (): void => {
    // The page is about to go; the toast is what stays to say the job is on its way.
    toast.info(t("appPages.actions.deletionQueued", { domain }), { description: t("appPages.actions.deletionQueuedDescription") });
    void navigate({ to: "/apps" });
  };

  if (isAdopted(app)) {
    // Forgetting a site Noust adopted removes nothing it is made of: no choices, only the record.
    return (
      <ConfirmDialog
        open={open}
        onOpenChange={onOpenChange}
        friction="type"
        server={node}
        title={t("runtime.adopted.stopManagingTitle", { domain })}
        description={t("runtime.adopted.stopManagingSummary", { domain })}
        confirmText={domain}
        actionLabel={t("runtime.adopted.stopManagingAction")}
        onConfirm={async () => {
          await remove.mutateAsync({ removeFiles: false, removeSsl: false, removeVolumes: false, removeDatabases: false, removeAdoptedDirectory: null });
          queued();
        }}
      />
    );
  }

  const sentences = choice !== undefined && plan !== undefined ? deletionSentences(t, choice, plan) : null;
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      friction="type"
      server={node}
      title={t("appPages.deleteApp.title", { domain })}
      description={
        sentences === null ? null : (
          <span className="flex flex-col gap-1">
            <span>{sentences.removes}</span>
            <span>{sentences.keeps}</span>
          </span>
        )
      }
      confirmText={domain}
      actionLabel={t("appPages.deleteApp.action")}
      onConfirm={async () => {
        if (options === undefined) return;
        await remove.mutateAsync(options);
        queued();
      }}
    />
  );
}
