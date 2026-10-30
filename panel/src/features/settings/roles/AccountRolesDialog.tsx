import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { ApprovalPendingError } from "../../../api/errors";
import { rolesQuery, setAccountRoles } from "../../../api/queries/roles";
import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { FieldGroup } from "../../../components/ui/Field";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { accountKeys } from "../accounts/api";
import type { Account } from "../accounts/api";
import { roleDescription, roleLabel } from "./roleModel";

/** The roles an account holds, the one it is known by first: what `roles` says, or its one role on a server that sends only that. */
export function rolesOf(account: Pick<Account, "role" | "roles">): string[] {
  return account.roles !== undefined && account.roles.length > 0 ? [...account.roles] : [account.role];
}

/**
 * Giving a person one role or several (item 96). A person can do what all their roles hold together, so
 * the list is every role there is, ticked where they hold it; the first one they hold names them in the
 * lists. The server judges the combination (the last owner, roles a framework keeps apart) and says why in
 * its own words, so nothing is guessed here beyond "at least one". Where approvals apply, the change is a
 * request another person decides, which the console handles itself.
 */
export function AccountRolesDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const roles = useQuery(rolesQuery());
  const held = rolesOf(account);
  const [chosen, setChosen] = useState<readonly string[]>(held);
  const [tried, setTried] = useState(false);

  const save = useMutation({
    mutationFn: () => setAccountRoles(account.username, chosen.length > 0 ? [...chosen] : [...held], held),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: accountKeys.list });
      void queryClient.invalidateQueries({ queryKey: ["roles"] });
      onClose();
    },
    onError: (error) => {
      // Waiting for, or refused by, a second person: the approval dialog already said so.
      if (error instanceof ApprovalPendingError) onClose();
    },
  });
  const failure = save.error instanceof ApprovalPendingError ? null : save.error;

  const toggle = (name: string, on: boolean): void => {
    setChosen((previous) => (on ? (previous.includes(name) ? previous : [...previous, name]) : previous.filter((other) => other !== name)));
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={t("roles.account.dialogTitle", { name: account.username })}
      description={t("roles.account.dialogDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("roles.account.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (save.isPending) return;
          // The button is never off: it says what is missing, beside the list.
          if (chosen.length === 0) {
            setTried(true);
            return;
          }
          save.mutate();
        }}
      >
        <QueryState query={roles} label={t("roles.loading")} skeleton={<Skeleton className="h-40 w-full rounded-card" />}>
          {(data) => (
            <FieldGroup legend={t("roles.column.role")} description={t("roles.account.first")} {...(tried && chosen.length === 0 ? { error: t("roles.account.atLeastOne") } : {})}>
              <div className="flex flex-col gap-3">
                {data.roles.map((role) => (
                  <Checkbox
                    key={role.name}
                    checked={chosen.includes(role.name)}
                    onCheckedChange={(on) => {
                      toggle(role.name, on);
                    }}
                    label={roleLabel(t, role)}
                    description={roleDescription(t, role)}
                  />
                ))}
              </div>
            </FieldGroup>
          )}
        </QueryState>
        {failure !== null ? <ErrorBlock live compact error={failure} title={t("roles.account.failed")} /> : null}
      </form>
    </Dialog>
  );
}
