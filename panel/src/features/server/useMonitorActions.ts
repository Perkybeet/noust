import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { monitorKeys } from "../../api/queries/monitor";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { toast } from "../../components/ui/toast";

type ServiceVerb = "install" | "uninstall" | "enable" | "disable" | "start" | "stop";

function toastText(t: T, verb: ServiceVerb): string {
  switch (verb) {
    case "install":
      return t("server.monitor.toastInstalled");
    case "uninstall":
      return t("server.monitor.toastUninstalled");
    case "enable":
      return t("server.monitor.toastEnabled");
    case "disable":
      return t("server.monitor.toastDisabled");
    case "start":
      return t("server.monitor.toastStarted");
    case "stop":
      return t("server.monitor.toastStopped");
  }
}

function errorText(t: T, verb: ServiceVerb): string {
  switch (verb) {
    case "install":
      return t("server.monitor.errorInstall");
    case "uninstall":
      return t("server.monitor.errorUninstall");
    case "enable":
      return t("server.monitor.errorEnable");
    case "disable":
      return t("server.monitor.errorDisable");
    case "start":
      return t("server.monitor.errorStart");
    case "stop":
      return t("server.monitor.errorStop");
  }
}

function callVerb(verb: ServiceVerb) {
  switch (verb) {
    case "install":
      return request("post", "/api/monitor/install");
    case "uninstall":
      return request("post", "/api/monitor/uninstall");
    case "enable":
      return request("post", "/api/monitor/enable");
    case "disable":
      return request("post", "/api/monitor/disable");
    case "start":
      return request("post", "/api/monitor/start");
    case "stop":
      return request("post", "/api/monitor/stop");
  }
}

/** The resource monitor's own actions: its systemd unit, its observations, a test email. */
export function useMonitorActions() {
  const t = useT();
  const queryClient = useQueryClient();

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: monitorKeys.status });
  };

  const useVerb = (verb: ServiceVerb) =>
    useMutation({
      mutationFn: () => callVerb(verb),
      onSuccess: () => {
        toast.success(toastText(t, verb));
        refresh();
      },
      onError: (error) => {
        reportActionError(errorText(t, verb), error);
        refresh();
      },
    });

  const install = useVerb("install");
  const uninstall = useVerb("uninstall");
  const enable = useVerb("enable");
  const disable = useVerb("disable");
  const start = useVerb("start");
  const stop = useVerb("stop");

  const testEmail = useMutation({
    mutationFn: () => request("post", "/api/monitor/test-email"),
    onSuccess: () => {
      toast.success(t("server.monitor.testEmailSent"));
    },
    onError: (error) => {
      reportActionError(t("server.monitor.testEmailFailed"), error);
    },
  });

  const acknowledge = useMutation({
    mutationFn: (id: number) => request("post", "/api/monitor/observations/{observation_id}/acknowledge", { params: { observation_id: id } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: monitorKeys.all });
    },
    onError: (error) => {
      reportActionError(t("server.monitor.acknowledgeFailed"), error);
    },
  });

  return { install, uninstall, enable, disable, start, stop, testEmail, acknowledge };
}
