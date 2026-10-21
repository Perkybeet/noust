import { useState } from "react";
import type { ReactNode } from "react";

import type { ApiError } from "../../../api/errors";
import type { Release } from "../../../api/queries/apps";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { FrozenDeployDialog } from "../../apps/FrozenDeployDialog";
import { isDeploysFrozen, reportActionError } from "../../apps/useAppActions";
import { schemaChangeRefusal } from "../schemaChange";
import { useSchemaChangeConfirmation } from "../SchemaChangeDialog";
import { usePromoteRelease } from "../useStagedRelease";
import { shortCommit } from "./words";

export interface PromoteBuild {
  /** Asks whether to make a build that waits the one that serves. */
  ask: (release: Release) => void;
  /** The questions it opens: the confirmation, a freeze's, and a schema change's. */
  dialogs: ReactNode;
}

/**
 * Making a build that waits serve ("Build without serving", then this): one question, then the
 * release that was built goes live behind the startup check, with its migrations, and nothing is
 * built again. A freeze of deploys and a change to the database's schema each ask once more, in
 * their own words, and the same call is sent again confirmed.
 */
export function usePromoteBuild(domain: string): PromoteBuild {
  const t = useT();
  const { node } = useNode();
  const schema = useSchemaChangeConfirmation(domain);
  const promote = usePromoteRelease(domain);
  const [chosen, setChosen] = useState<Release | null>(null);
  // A freeze refused making it serve: asked again, saying it must go ahead, if the operator is sure.
  const [frozen, setFrozen] = useState<{ refusal: ApiError; id: string } | null>(null);
  const commit = shortCommit(chosen?.commit);

  const dialogs = (
    <>
      <ConfirmDialog
        open={chosen !== null}
        onOpenChange={(open) => {
          if (!open) setChosen(null);
        }}
        friction="simple"
        destructive={false}
        server={node}
        title={t("rollout.releases.promoteTitle", { id: chosen?.id ?? "" })}
        description={commit ? t("rollout.releases.promoteDescriptionCommit", { commit }) : t("rollout.releases.promoteDescription")}
        actionLabel={t("rollout.releases.promote")}
        onConfirm={async () => {
          if (chosen === null) return;
          const id = chosen.id;
          try {
            await promote.mutateAsync({ id });
          } catch (error) {
            if (isDeploysFrozen(error)) {
              setFrozen({ refusal: error, id });
              return;
            }
            // Past a schema change this question closes and the one that names them opens.
            if (!schema.intercept(schemaChangeRefusal(error), () => promote.mutateAsync({ id, schemaChangedOk: true }))) throw error;
          }
        }}
      />
      <FrozenDeployDialog
        domain={domain}
        refusal={frozen?.refusal ?? null}
        pending={promote.isPending}
        onDeploy={() => {
          if (frozen === null) return;
          promote.mutate(
            { id: frozen.id, frozenOk: true },
            {
              onSuccess: () => setFrozen(null),
              onError: (error) => {
                setFrozen(null);
                reportActionError(t("rollout.releases.promoteFailed", { id: frozen.id }), error);
              },
            },
          );
        }}
        onClose={() => setFrozen(null)}
      />
      {schema.dialog}
    </>
  );
  return { ask: setChosen, dialogs };
}
