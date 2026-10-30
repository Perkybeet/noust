import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { databaseKeys } from "../../api/queries/databases";
import type { Database } from "../../api/queries/databases";
import { Button } from "../../components/ui/Button";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

/**
 * Adds a database the engine holds to Noust (`POST .../adopt`): from then on it is listed as Noust's, can be linked
 * to an application and is backed up with it. It is not a different kind of database, so the button is only on the
 * row of one Noust does not hold yet, where its backup would be, and what changes is the row itself.
 */
export function AddToNoust({ database }: { database: Database }) {
  const t = useT();
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/{engine}/{name}/adopt", { params: { engine: database.engine, name: database.name } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.policies });
    },
    onError: (error) => reportActionError(t("databases.listRow.addFailed", { name: database.name }), error),
  });
  return (
    <Button size="sm" loading={add.isPending} onClick={() => add.mutate()}>
      {t("databases.listRow.addToNoust")}
    </Button>
  );
}
