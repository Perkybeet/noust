import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { appQuery, deletionPlanQuery } from "../../../api/queries/apps";
import type { App, DeletionPlan } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { GuardedButton, ReadOnlyLine } from "../../../components/page/permissions";
import { ErrorBlock } from "../../../components/page/QueryState";
import { DangerAction, DangerZone } from "../../../components/page/DangerZone";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonSection } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { isAdopted } from "../adopted";
import { DeleteAppDialog } from "../DeleteAppDialog";
import { NOTHING_CHOSEN, deleteOptionsOf, dropsOf, keptOf } from "../deletion";
import type { DeleteChoice } from "../deletion";
import { useSudoFirst } from "../settings/formParts";

/** The reason a database stays, in a sentence: others use it, or this application only uses it. */
function KeptDatabases({ plan }: { plan: DeletionPlan }) {
  const t = useT();
  const list = new Intl.ListFormat(t.locale, { style: "long", type: "conjunction" });
  const kept = keptOf(plan);
  if (kept.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1 text-13 text-fg-muted">
      {kept.map((database) => (
        <li key={`${database.engine}:${database.name}`}>
          {database.shared_with.length > 0
            ? t.rich("appPages.deleteApp.databaseKeptShared", { name: <Mono key="name" tone="muted">{database.name}</Mono>, apps: list.format(database.shared_with) })
            : t.rich("appPages.deleteApp.databaseKeptUsed", { name: <Mono key="name" tone="muted">{database.name}</Mono> })}
        </li>
      ))}
    </ul>
  );
}

/** The boxes: one for each thing of the application that holds data, none ticked. */
function Choices({ app, plan, choice, onChange }: { app: App; plan: DeletionPlan; choice: DeleteChoice; onChange: (choice: DeleteChoice) => void }) {
  const t = useT();
  const set = (patch: Partial<DeleteChoice>): void => {
    onChange({ ...choice, ...patch });
  };
  const drops = dropsOf(plan);
  const path = plan.path === "" ? app.path : plan.path;
  return (
    <div className="flex flex-col gap-3">
      <Checkbox
        label={t(plan.keeps_directory ? "appPages.deleteApp.folderLabel" : "appPages.deleteApp.filesLabel")}
        description={
          plan.keeps_directory
            ? t.rich("appPages.deleteApp.folderDescription", { path: <Mono key="path" tone="muted">{path}</Mono> })
            : path
              ? t.rich("appPages.deleteApp.filesDescription", { path: <Mono key="path" tone="muted">{path}</Mono> })
              : t("appPages.deleteApp.filesDescriptionNoPath")
        }
        checked={choice.files}
        onCheckedChange={(files) => {
          set({ files });
        }}
      />
      {plan.has_volumes ? (
        <Checkbox
          label={t("appPages.deleteApp.volumesLabel")}
          description={t("appPages.deleteApp.volumesDescription")}
          checked={choice.volumes}
          onCheckedChange={(volumes) => {
            set({ volumes });
          }}
        />
      ) : null}
      {drops.length > 0 ? (
        <Checkbox
          label={t("appPages.deleteApp.databasesLabel", {
            count: drops.length,
            names: new Intl.ListFormat(t.locale, { style: "long", type: "conjunction" }).format(drops.map((database) => database.name)),
          })}
          description={t("appPages.deleteApp.databasesDescription")}
          checked={choice.databases}
          onCheckedChange={(databases) => {
            set({ databases });
          }}
        />
      ) : null}
      <Checkbox
        label={t("appPages.deleteApp.certificateLabel")}
        description={t("appPages.deleteApp.certificateDescription")}
        checked={choice.certificate}
        onCheckedChange={(certificate) => {
          set({ certificate });
        }}
      />
      <KeptDatabases plan={plan} />
    </div>
  );
}

function DeleteBody({ app }: { app: App }) {
  const t = useT();
  const domain = app.domain;
  const adopted = isAdopted(app);
  const plan = useQuery({ ...deletionPlanQuery(domain), enabled: !adopted });
  const sudoFirst = useSudoFirst();
  const [choice, setChoice] = useState<DeleteChoice>(NOTHING_CHOSEN);
  const [asking, setAsking] = useState(false);
  const Delete = ICONS.delete;

  const start = (): void => {
    // Sudo mode first, so "Confirm it's you" never opens on top of the typed confirmation.
    sudoFirst(() => {
      setAsking(true);
    }, t("appSettings.danger.startFailed", { domain }));
  };

  if (adopted) {
    // Forgetting a site Noust adopted removes nothing it is made of: no choices, only the record.
    return (
      <>
        <ReadOnlyLine permission="apps.manage" />
        <DangerZone title={t("runtime.adopted.stopManaging")}>
          <DangerAction
            title={t("runtime.adopted.stopManagingSettings")}
            description={
              app.path
                ? t.rich("runtime.adopted.stopManagingSettingsDescription", { path: <Mono key="path" tone="muted">{app.path}</Mono> })
                : t("runtime.adopted.stopManagingSettingsDescriptionNoPath")
            }
            action={
              <GuardedButton permission="apps.manage" variant="danger" icon={<Delete aria-hidden="true" />} onClick={start}>
                {t("runtime.adopted.stopManagingButton")}
              </GuardedButton>
            }
          />
        </DangerZone>
        <DeleteAppDialog app={app} open={asking} onOpenChange={setAsking} />
      </>
    );
  }

  if (plan.isPending) {
    return (
      <LoadingRegion label={t("appPages.deleteApp.page.reading", { domain })}>
        <SkeletonSection rows={3} />
      </LoadingRegion>
    );
  }
  if (plan.isError) {
    return <ErrorBlock error={plan.error} title={t("appPages.deleteApp.page.readFailed", { domain })} onRetry={() => void plan.refetch()} retrying={plan.isRefetching} />;
  }
  const options = deleteOptionsOf(choice, plan.data);
  return (
    <>
      <ReadOnlyLine permission="apps.manage" />
      <DangerZone title={t("appPages.deleteApp.page.title")} description={t("appPages.deleteApp.page.line")}>
        <div className="flex flex-col gap-5 px-5 py-4">
          <Choices app={app} plan={plan.data} choice={choice} onChange={setChoice} />
          <div>
            <GuardedButton permission="apps.manage" variant="danger" icon={<Delete aria-hidden="true" />} onClick={start}>
              {t("appPages.deleteApp.page.button")}
            </GuardedButton>
          </div>
        </div>
      </DangerZone>
      <DeleteAppDialog app={app} open={asking} onOpenChange={setAsking} choice={choice} plan={plan.data} options={options} />
    </>
  );
}

/**
 * Deleting an application, alone at the end of its column (item 120: exporting it left Danger). What is deleted is
 * one box for each thing that holds data (its files, the volumes of its containers, its own databases, its
 * certificate), none ticked, read from what the server says deleting it would touch: an adopted stack's folder says
 * it is not Noust's and goes only because the box is ticked, and a database other applications use is said to stay.
 * Pressing the button asks who you are, then asks for the domain, with what goes and what stays in the question.
 */
export function DeletePage({ domain }: { domain: string }) {
  const t = useT();
  const app = useQuery(appQuery(domain));
  useDocumentTitle(t("appPages.config.documentTitle", { section: t("common.words.delete"), domain }), 1);
  if (app.data === undefined) {
    return app.isError ? null : (
      <LoadingRegion label={t("appPages.config.loading", { section: t("common.words.delete") })}>
        <SkeletonSection rows={3} />
      </LoadingRegion>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <DeleteBody app={app.data} />
    </div>
  );
}
