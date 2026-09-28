import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { serviceKeys } from "../../api/queries/services";
import { getLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { translate } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

type Verb = "start" | "stop" | "restart" | "enable" | "disable";

function callVerb(name: string, verb: Verb) {
  const params = { params: { name } };
  switch (verb) {
    case "start":
      return request("post", "/api/services/{name}/start", params);
    case "stop":
      return request("post", "/api/services/{name}/stop", params);
    case "restart":
      return request("post", "/api/services/{name}/restart", params);
    case "enable":
      return request("post", "/api/services/{name}/enable", params);
    case "disable":
      return request("post", "/api/services/{name}/disable", params);
  }
}

/** The verb's toast, in the active language - one literal key per case, so tsc checks each. */
function successToast(verb: Verb, name: string): string {
  const locale = getLocale();
  switch (verb) {
    case "start":
      return translate(locale, "services.actions.startedToast", { name });
    case "stop":
      return translate(locale, "services.actions.stoppedToast", { name });
    case "restart":
      return translate(locale, "services.actions.restartedToast", { name });
    case "enable":
      return translate(locale, "services.actions.enabledToast", { name });
    case "disable":
      return translate(locale, "services.actions.disabledToast", { name });
  }
}

function failureTitle(verb: Verb, name: string): string {
  const locale = getLocale();
  switch (verb) {
    case "start":
      return translate(locale, "services.actions.startFailed", { name });
    case "stop":
      return translate(locale, "services.actions.stopFailed", { name });
    case "restart":
      return translate(locale, "services.actions.restartFailed", { name });
    case "enable":
      return translate(locale, "services.actions.enableFailed", { name });
    case "disable":
      return translate(locale, "services.actions.disableFailed", { name });
  }
}

/**
 * The actions on one systemd unit: start, stop, restart, enable and disable run and report
 * immediately (the endpoints are synchronous systemctl calls, not queued jobs); deleting and
 * saving the unit file need "Confirm it's you", which the API client asks for on its own.
 */
export function useServiceActions(name: string) {
  const queryClient = useQueryClient();

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: serviceKeys.detail(name) });
    void queryClient.invalidateQueries({ queryKey: serviceKeys.all });
  };

  const useVerb = (verb: Verb) =>
    useMutation({
      mutationFn: () => callVerb(name, verb),
      onSuccess: () => {
        toast.success(successToast(verb, name));
        refresh();
      },
      onError: (error) => {
        reportActionError(failureTitle(verb, name), error);
        refresh();
      },
    });

  const start = useVerb("start");
  const stop = useVerb("stop");
  const restart = useVerb("restart");
  const enable = useVerb("enable");
  const disable = useVerb("disable");

  const updateConfig = useMutation({
    mutationFn: (config: string) => request("put", "/api/services/{name}/config", { params: { name }, body: { config } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serviceKeys.config(name) });
      const locale = getLocale();
      toast.success(translate(locale, "services.actions.unitSaved", { name }), {
        description: translate(locale, "services.actions.unitSavedHint"),
      });
    },
  });

  // Silent on purpose: its result feeds the unit editor's own pass/fail block, and a save that
  // goes on to succeed already toasts through updateConfig above - a second toast here would
  // just repeat it.
  const verifyUnit = useMutation({
    mutationFn: (content: string) => request("post", "/api/services/verify", { body: { content } }),
  });

  const remove = useMutation({
    mutationFn: () => request("delete", "/api/services/{name}", { params: { name } }),
    onSuccess: () => {
      toast.success(translate(getLocale(), "services.actions.deletedToast", { name }));
      void queryClient.invalidateQueries({ queryKey: serviceKeys.all });
      // Deleted, not just stale: nothing should be able to read a cached answer for a unit
      // that no longer exists. Only once nothing is still watching it: this page's own detail
      // query is exactly that until the redirect below unmounts it, and removing an entry an
      // active observer still needs makes the query client refetch it immediately - a GET the
      // deleted unit can only answer 404, logged as a console error the same way any failed
      // request is.
      queryClient.removeQueries({ queryKey: serviceKeys.detail(name), type: "inactive" });
    },
  });

  return { start, stop, restart, enable, disable, updateConfig, verifyUnit, remove };
}
