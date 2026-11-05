import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { databaseOverviewQuery, databasesQuery } from "../../../api/queries/databases";
import type { DatabaseOverview } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { KeyValueList } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { Can, ReadOnlyLine } from "../../../components/page/permissions";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { Button } from "../../../components/ui/Button";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { DatabasePage, useDatabaseFrame } from "../DatabasePage";
import { engineFamilyOf } from "../engineKey";
import { answers } from "../engineOf";
import { can } from "../engines";
import { ExtensionsCard } from "../extensions/ExtensionsCard";
import { FixOwnerDialog } from "../FixOwnerDialog";
import { MoveDatabaseDialog } from "../instances/MoveDatabaseDialog";
import { useDatabaseJob } from "../jobs";
import { serverWithPlace } from "../servers";

/** The engines whose databases can be moved to another engine of the same kind. */
const MOVABLE: ReadonlySet<string> = new Set(["postgresql", "mysql", "mongodb"]);

/** What Noust and the engine know of the database, one fact a row. */
function aboutItems(overview: DatabaseOverview | undefined, placed: string | null, t: T): KeyValueItem[] {
  const database = overview?.database;
  const items: KeyValueItem[] = [];
  if (placed !== null) items.push({ label: t("databases.fields.engine"), value: placed, mono: false, copy: false });
  if (database?.owner) items.push({ label: t("databases.fields.owner"), value: database.owner });
  if (database?.encoding) items.push({ label: t("databases.overview.encoding"), value: database.encoding });
  if (overview !== undefined) {
    items.push({ label: t("databases.overview.port"), value: String(overview.port) }, { label: t("databases.overview.service"), value: overview.service });
  }
  if (database !== undefined) {
    items.push({
      label: t("databases.overview.tracked"),
      value: database.tracked ? t("databases.overview.trackedYes") : t("databases.overview.trackedNo"),
      mono: false,
      copy: false,
    });
  }
  return items;
}

/**
 * What belongs to the database itself and is not data, access or backup: who owns it and how it is encoded, the
 * extensions it has loaded, and moving it to another engine. These were pieces of the overview with no home of their own;
 * the overview says what is going on, and this is where it is set up.
 */
export function SettingsTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const job = useDatabaseJob();
  const frame = useDatabaseFrame();
  const { server, life } = frame;
  const running = life !== "stopped" && life !== "changing";
  const overview = useQuery({ ...databaseOverviewQuery(engine, name), retry: false, enabled: answers(frame) });
  const row = useQuery({ ...databasesQuery(), select: (list) => list.databases.find((item) => item.engine === engine && item.name === name) });
  const [fixing, setFixing] = useState(false);
  const [moving, setMoving] = useState(false);
  const data = overview.data;
  const database = data?.database ?? row.data;
  const capabilities = data?.capabilities ?? [];
  const family = engineFamilyOf(engine);
  const missing = database?.missing === true;
  const canFixOwner = family === "postgresql" && database?.username != null && database.owner != null && database.owner !== database.username;
  const movable = running && !missing && MOVABLE.has(family) && can({ capabilities }, "dump");

  const loading = frame.reading || (answers(frame) && overview.isPending);
  return (
    <DatabasePage title={t("databases.detailTabs.settings")}>
      {loading ? (
        <LoadingRegion label={t("databases.layout.loading", { name })} className="flex flex-col gap-6">
          <Skeleton className="h-40 w-full rounded-card" />
        </LoadingRegion>
      ) : (
      <div className="flex flex-col gap-6">
        <ReadOnlyLine permission="databases.manage" />
        <SurfaceSection
          title={t("databases.settings.about")}
          icon={OBJECT_ICONS.database}
          description={t("databases.settings.aboutLine")}
          actions={
            canFixOwner && database.username ? (
              <Can permission="databases.manage">
                <Button size="sm" onClick={() => setFixing(true)}>
                  {t("databases.layout.fixOwner", { user: database.username })}
                </Button>
              </Can>
            ) : undefined
          }
        >
          <KeyValueList items={aboutItems(data, server === undefined ? null : serverWithPlace(t, server), t)} />
        </SurfaceSection>

        {running && can({ capabilities }, "extensions") ? <ExtensionsCard engine={engine} name={name} /> : null}

        {movable ? (
          <SurfaceSection
            title={t("databases.settings.moveTitle")}
            icon={OBJECT_ICONS.server}
            description={t("databases.settings.moveLine")}
            actions={
              <Can permission="databases.manage">
                <Button size="sm" disabled={job.busy || data === undefined} onClick={() => setMoving(true)}>
                  {t("databases.moveDb.open")}
                </Button>
              </Can>
            }
          />
        ) : null}
      </div>
      )}
      {moving ? (
        <MoveDatabaseDialog
          engine={engine}
          name={name}
          onOpenChange={(open) => (open ? undefined : setMoving(false))}
          onQueued={(accepted) => {
            job.track(accepted, "move");
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
          }}
        />
      ) : null}
      {fixing && database?.username ? <FixOwnerDialog engine={engine} name={name} owner={database.username} onClose={() => setFixing(false)} /> : null}
    </DatabasePage>
  );
}
