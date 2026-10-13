/**
 * What the instances' section and the engines' menu share: the job in hand (an instance being
 * made, upgraded, removed or moved, followed in the page's notice slot) and the two dialogs that
 * start one, so a host engine's "Move to a newer version" and an instance's open the same thing.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { databaseKeys } from "../../../api/queries/databases";
import { instanceKeys } from "../../../api/queries/databaseInstances";
import type { DbInstance } from "../../../api/queries/databaseInstances";
import { jobKeys, useFollowedJob } from "../../../api/queries/jobs";
import { useT } from "../../../i18n";
import { DatabaseJobProgress } from "../jobs";
import { CreateInstanceDialog } from "./CreateInstanceDialog";
import { MigrateDialog } from "./MigrateDialog";
import type { MigrationSource } from "./MigrateDialog";
import { TlsDialog } from "./TlsDialog";

export interface InstanceJobWords {
  running: string;
  succeeded: string;
  failed: string;
}

export interface InstanceActions {
  /** The job in hand, once queued; null when none. */
  notice: ReactNode;
  /** Dialogs to render once, anywhere in the page. */
  dialogs: ReactNode;
  /** Whether a job is being followed: another is not offered until it is dismissed. */
  busy: boolean;
  openCreate: () => void;
  openMigrate: (source: MigrationSource) => void;
  openTls: (instance: DbInstance) => void;
  /** Follow a job a menu action queued (upgrade, remove, start, rollback). */
  follow: (jobId: string, words: InstanceJobWords) => void;
}

export function useInstanceActions(): InstanceActions {
  const t = useT();
  const queryClient = useQueryClient();
  const followed = useFollowedJob();
  const [words, setWords] = useState<InstanceJobWords | null>(null);
  const [creating, setCreating] = useState(false);
  const [migrating, setMigrating] = useState<MigrationSource | null>(null);
  const [securing, setSecuring] = useState<DbInstance | null>(null);

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: instanceKeys.all });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.engines });
    void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
  };
  const follow = (jobId: string, next: InstanceJobWords): void => {
    setWords(next);
    followed.follow(jobId);
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
  };

  const job = followed.job;
  const notice =
    job !== null && words !== null ? (
      <DatabaseJobProgress
        job={job}
        words={words}
        onDismiss={() => {
          followed.dismiss();
          setWords(null);
          refresh();
        }}
      />
    ) : null;

  const dialogs = (
    <>
      <CreateInstanceDialog
        open={creating}
        onOpenChange={setCreating}
        onQueued={(jobId, name) =>
          follow(jobId, {
            running: t("databases.newInstance.making", { name }),
            succeeded: t("databases.newInstance.made", { name }),
            failed: t("databases.newInstance.makeFailed", { name }),
          })
        }
      />
      {migrating !== null ? (
        <MigrateDialog
          source={migrating}
          onOpenChange={(open) => (open ? undefined : setMigrating(null))}
          onQueued={(jobId, label) =>
            follow(jobId, {
              running: t("databases.migrate.moving", { engine: label }),
              succeeded: t("databases.migrate.moved", { engine: label }),
              failed: t("databases.migrate.moveFailed", { engine: label }),
            })
          }
        />
      ) : null}
      {securing !== null ? <TlsDialog instance={securing} onOpenChange={(open) => (open ? undefined : setSecuring(null))} onQueued={follow} /> : null}
    </>
  );

  return { notice, dialogs, busy: job !== null && words !== null, openCreate: () => setCreating(true), openMigrate: setMigrating, openTls: setSecuring, follow };
}
