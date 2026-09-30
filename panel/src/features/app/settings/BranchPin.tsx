/**
 * Pinning the branch an application deploys from (`PATCH /api/apps/{domain}/branch`, behind
 * sudo mode). With a branch pinned, deploy on push ignores every other branch and each update
 * builds it; unpinned, any push deploys. The branch is checked on the remote when it is pinned
 * and nothing is rebuilt until the next update, so pinning is a saved value, not a deploy.
 *
 * Offered where the question comes up: General, beside the branch, and the deploy-on-push
 * warning that any push deploys.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { request } from "../../../api/client";
import type { App } from "../../../api/queries/apps";
import { appKeys } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { useSudoFirst } from "./formParts";
import { settingsKeys } from "./queries";

/** Pins a branch, or unpins with null; what the app and the webhook's status say follows. */
function usePin(domain: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (branch: string | null) => request("patch", "/api/apps/{domain}/branch", { params: { domain }, body: { branch } }),
    onSuccess: (result) => {
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, branch: result.branch ?? null } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      void queryClient.invalidateQueries({ queryKey: settingsKeys.webhook(domain) });
    },
  });
}

function PinBranchDialog({ app, open, onOpenChange }: { app: App; open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const domain = app.domain;
  const pin = usePin(domain);
  const [branch, setBranch] = useState(app.branch ?? "main");
  const [submitted, setSubmitted] = useState(false);
  const empty = branch.trim() === "";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (empty || pin.isPending) return;
    pin.mutate(branch.trim(), {
      onSuccess: (result) => {
        announce(t("appSettings.branch.pinned", { domain, branch: result.branch ?? branch.trim() }));
        onOpenChange(false);
      },
    });
  };
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pin.isPending) return;
        onOpenChange(next);
      }}
      title={app.branch ? t("appSettings.branch.changeTitle") : t("appSettings.branch.pinTitle")}
      description={t("appSettings.branch.dialogDescription")}
      footer={
        <>
          <Button disabled={pin.isPending} onClick={() => onOpenChange(false)}>
            {t("appSettings.cancel")}
          </Button>
          <Button type="submit" form="pin-branch" variant="primary" loading={pin.isPending}>
            {t("appSettings.branch.action")}
          </Button>
        </>
      }
    >
      <form id="pin-branch" onSubmit={submit} noValidate className="flex flex-col gap-4">
        <Field
          label={t("appSettings.branch.label")}
          description={t("appSettings.branch.fieldDescription")}
          error={submitted && empty ? t("appSettings.branch.required") : undefined}
        >
          <Input mono value={branch} onValueChange={(value: string) => setBranch(value)} autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {pin.isError ? <ErrorBlock live compact error={pin.error} title={t("appSettings.branch.notPinned", { domain })} /> : null}
      </form>
    </Dialog>
  );
}

export interface BranchPinActions {
  /** Opens "Pin the branch" (or "Change the branch"), after "Confirm it's you" when needed. */
  pin: () => void;
  /** Asks once, then unpins. */
  unpin: () => void;
  /** The dialogs those open, rendered where the caller renders them. */
  dialogs: ReactNode;
}

/** The actions on an app's deploy branch, and their dialogs. */
export function useBranchPin(app: App): BranchPinActions {
  const t = useT();
  const { node } = useNode();
  const sudoFirst = useSudoFirst();
  const domain = app.domain;
  const unpinning = usePin(domain);
  const [pinOpen, setPinOpen] = useState(false);
  const [unpinOpen, setUnpinOpen] = useState(false);
  return {
    pin: () => {
      sudoFirst(() => setPinOpen(true), t("appSettings.branch.notPinned", { domain }));
    },
    unpin: () => {
      sudoFirst(() => setUnpinOpen(true), t("appSettings.branch.notUnpinned", { domain }));
    },
    dialogs: (
      <>
        {/* Mounted per opening, so the field starts from the branch pinned now. */}
        {pinOpen ? <PinBranchDialog app={app} open onOpenChange={setPinOpen} /> : null}
        <ConfirmDialog
          open={unpinOpen}
          onOpenChange={setUnpinOpen}
          friction="simple"
          destructive={false}
          server={node}
          title={t("appSettings.branch.unpinTitle", { domain })}
          description={t("appSettings.branch.unpinDescription")}
          actionLabel={t("appSettings.branch.unpinAction")}
          onConfirm={async () => {
            await unpinning.mutateAsync(null);
            announce(t("appSettings.branch.unpinned", { domain }));
          }}
        />
      </>
    ),
  };
}

/** Beside the branch in General: pin one, or change and unpin the one pinned. */
export function BranchActions({ app }: { app: App }) {
  const t = useT();
  const { pin, unpin, dialogs } = useBranchPin(app);
  return (
    <span className="flex flex-wrap items-center gap-2">
      {app.branch ? (
        <>
          <Button size="sm" onClick={pin}>
            {t("appSettings.branch.change")}
          </Button>
          <Button size="sm" onClick={unpin}>
            {t("appSettings.branch.unpin")}
          </Button>
        </>
      ) : (
        <Button size="sm" onClick={pin}>
          {t("appSettings.branch.pin")}
        </Button>
      )}
      {dialogs}
    </span>
  );
}
