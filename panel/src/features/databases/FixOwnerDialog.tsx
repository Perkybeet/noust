import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { databaseKeys } from "../../api/queries/databases";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";

export interface FixOwnerDialogProps {
  engine: string;
  name: string;
  /** The role it is given: the account Noust provisioned for it. */
  owner: string;
  onClose: () => void;
}

/**
 * Giving a PostgreSQL database, and everything its owner holds in it, to the account its
 * application signs in as: what migrations on PostgreSQL 15 and later need. The exact
 * statements are shown before anything runs; nothing changes an owner on its own.
 */
export function FixOwnerDialog({ engine, name, owner, onClose }: FixOwnerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const plan = useQuery({
    queryKey: [...databaseKeys.database(engine, name), "fix-owner", owner],
    queryFn: ({ signal }) => request("get", "/api/databases/databases/{engine}/{name}/fix-owner", { params: { engine, name }, query: { owner }, signal }),
  });
  const apply = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/{engine}/{name}/fix-owner", { params: { engine, name }, body: { owner } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.database(engine, name) });
      toast.success(t("databases.fixOwner.done", { name, owner }));
      onClose();
    },
  });
  const nothing = plan.data?.statements.length === 0;
  return (
    <Dialog
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      size="lg"
      title={t("databases.fixOwner.title", { owner })}
      description={t("databases.fixOwner.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("databases.common.cancel")}</Button>
          <Button variant="primary" loading={apply.isPending} disabled={plan.data === undefined || nothing} onClick={() => apply.mutate()}>
            {t("databases.fixOwner.apply")}
          </Button>
        </>
      }
    >
      {plan.isError ? (
        <ErrorBlock compact error={plan.error} title={t("databases.fixOwner.planFailed")} onRetry={() => void plan.refetch()} />
      ) : plan.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">{t("databases.fixOwner.loading")}</span>
          <Skeleton className="h-4 w-1/2" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <KeyValueList
            items={[
              { label: t("databases.fixOwner.current"), value: plan.data.current_owner ?? null },
              { label: t("databases.fixOwner.next"), value: plan.data.new_owner },
              { label: t("databases.fixOwner.objects"), value: t("databases.fixOwner.objectCount", { count: plan.data.objects.length }), mono: false, copy: false },
            ]}
          />
          {nothing ? (
            <p className="text-14 text-fg-muted">{t("databases.fixOwner.nothing")}</p>
          ) : (
            <SystemOutput label={t("databases.fixOwner.statements")} maxHeight="max-h-72">
              {plan.data.statements.join("\n")}
            </SystemOutput>
          )}
          {apply.isError ? <ErrorBlock live compact error={apply.error} title={t("databases.fixOwner.failed")} /> : null}
        </div>
      )}
    </Dialog>
  );
}
