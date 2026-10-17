/**
 * Actions on the cloud accounts, the boot scripts and the machines: one mutation per API call.
 * Everything that spends or destroys is sudo mode, which the API client's "Confirm it's you" flow
 * handles without special-casing here. A token is in the request body once and in nothing after:
 * these hooks never keep it, and a failure shows the provider's own words, never the token.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { fleetKeys } from "../fleet/data";
import { nodeKeys } from "../fleet/nodes";
import { cloudKeys } from "./data";
import type { ScriptKind } from "./data";

export interface AddAccountInput {
  name: string;
  provider: string;
  token: string;
  note: string;
}

export interface SaveScriptInput {
  name: string;
  kind: ScriptKind;
  content: string;
  description: string;
}

export function useCloudActions() {
  const t = useT();
  const queryClient = useQueryClient();

  const refreshAccounts = (): void => {
    void queryClient.invalidateQueries({ queryKey: cloudKeys.accounts });
  };
  const refreshScripts = (): void => {
    void queryClient.invalidateQueries({ queryKey: cloudKeys.scripts });
  };
  const refreshMachines = (): void => {
    void queryClient.invalidateQueries({ queryKey: cloudKeys.machines });
    void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
    void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
  };

  // No onError on the ones a dialog shows where they were started; the toast is for the rest.
  const addAccount = useMutation({
    mutationFn: (input: AddAccountInput) =>
      request("post", "/api/fleet/cloud/accounts", {
        body: { name: input.name, provider: input.provider, token: input.token, note: input.note === "" ? null : input.note, verify: true },
      }),
    onSuccess: (account) => {
      toast.success(t("cloud.accounts.addedToast", { name: account.name }));
      refreshAccounts();
    },
  });

  const replaceToken = useMutation({
    mutationFn: ({ name, token }: { name: string; token: string }) =>
      request("put", "/api/fleet/cloud/accounts/{name}/token", { params: { name }, body: { token, verify: true } }),
    onSuccess: (account) => {
      toast.success(t("cloud.accounts.tokenReplacedToast", { name: account.name }));
      refreshAccounts();
    },
  });

  const verifyAccount = useMutation({
    mutationFn: (name: string) => request("post", "/api/fleet/cloud/accounts/{name}/verify", { params: { name } }),
    onSuccess: (account) => {
      toast.success(t("cloud.accounts.verifiedToast", { name: account.name }));
      refreshAccounts();
    },
    onError: (error, name) => {
      reportActionError(t("cloud.accounts.verifyFailedToast", { name }), error);
    },
  });

  const removeAccount = useMutation({
    mutationFn: ({ name, force }: { name: string; force: boolean }) =>
      request("delete", "/api/fleet/cloud/accounts/{name}", { params: { name }, query: { force } }),
    onSuccess: (_result, { name }) => {
      toast.success(t("cloud.accounts.removedToast", { name }));
      refreshAccounts();
    },
  });

  const saveScript = useMutation({
    mutationFn: (input: SaveScriptInput) =>
      request("put", "/api/fleet/cloud/scripts/{name}", {
        params: { name: input.name },
        body: { kind: input.kind, content: input.content, description: input.description },
      }),
    onSuccess: (script) => {
      toast.success(t("cloud.scripts.savedToast", { name: script.name }));
      refreshScripts();
      void queryClient.invalidateQueries({ queryKey: cloudKeys.script(script.name) });
    },
  });

  const deleteScript = useMutation({
    mutationFn: (name: string) => request("delete", "/api/fleet/cloud/scripts/{name}", { params: { name } }),
    onSuccess: (_result, name) => {
      toast.success(t("cloud.scripts.deletedToast", { name }));
      refreshScripts();
    },
  });

  const powerOn = useMutation({
    mutationFn: (node: string) => request("post", "/api/fleet/cloud/servers/{node}/power-on", { params: { node } }),
    onSuccess: (machine) => {
      toast.success(t("cloud.machines.poweredOnToast", { name: machine.node }));
      refreshMachines();
    },
    onError: (error, node) => {
      reportActionError(t("cloud.machines.powerOnFailedToast", { name: node }), error);
    },
  });

  const unlinkMachine = useMutation({
    mutationFn: (node: string) => request("delete", "/api/fleet/cloud/servers/{node}/link", { params: { node } }),
    onSuccess: (_result, node) => {
      toast.success(t("cloud.machines.unlinkedToast", { name: node }));
      refreshMachines();
    },
    onError: (error, node) => {
      reportActionError(t("cloud.machines.unlinkFailedToast", { name: node }), error);
    },
  });

  const deleteMachine = useMutation({
    mutationFn: ({ node, removeNode }: { node: string; removeNode: boolean }) =>
      request("delete", "/api/fleet/cloud/servers/{node}", { params: { node }, query: { confirm: node, remove_node: removeNode } }),
    onSuccess: (result) => {
      const warnings = result.warnings ?? [];
      toast.success(t("cloud.machines.deletedToast", { name: result.node }), warnings.length > 0 ? { description: warnings.join(" ") } : {});
      refreshMachines();
    },
  });

  return { addAccount, replaceToken, verifyAccount, removeAccount, saveScript, deleteScript, powerOn, unlinkMachine, deleteMachine };
}
