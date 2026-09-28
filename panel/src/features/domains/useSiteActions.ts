import { useMutation, useQueryClient } from "@tanstack/react-query";

import { request } from "../../api/client";
import { siteKeys } from "../../api/queries/sites";
import { getLocale } from "../../app/locale";
import { toast } from "../../components/ui/toast";
import { translate } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";

/**
 * What can be done to a site from anywhere it is shown: enable, disable, and test and reload
 * the web server. Deleting goes through a confirmation and is called directly there.
 */
export function useSiteActions() {
  const queryClient = useQueryClient();
  const refresh = (site?: string): void => {
    void queryClient.invalidateQueries({ queryKey: siteKeys.all });
    if (site !== undefined) void queryClient.invalidateQueries({ queryKey: siteKeys.detail(site) });
  };

  const enable = useMutation({
    mutationFn: (site: string) => request("post", "/api/sites/{domain}/enable", { params: { domain: site } }),
    onSuccess: (result, site) => {
      const locale = getLocale();
      toast.success(translate(locale, "domains.siteActions.enabledToast", { site }), {
        description: translate(locale, "domains.siteActions.enabledDescription"),
      });
      refresh(result.site);
    },
    onError: (error, site) => {
      reportActionError(translate(getLocale(), "domains.siteActions.couldNotEnable", { site }), error);
      refresh(site);
    },
  });

  const disable = useMutation({
    mutationFn: (site: string) => request("post", "/api/sites/{domain}/disable", { params: { domain: site } }),
    onSuccess: (result, site) => {
      const locale = getLocale();
      toast.success(translate(locale, "domains.siteActions.disabledToast", { site }), {
        description: translate(locale, "domains.siteActions.disabledDescription"),
      });
      refresh(result.site);
    },
    onError: (error, site) => {
      reportActionError(translate(getLocale(), "domains.siteActions.couldNotDisable", { site }), error);
      refresh(site);
    },
  });

  const reload = useMutation({
    mutationFn: () => request("post", "/api/sites/reload"),
    onSuccess: (result) => {
      const locale = getLocale();
      toast.success(translate(locale, "domains.siteActions.reloadedToast", { webserver: result.webserver }), {
        description: translate(locale, "domains.siteActions.reloadedDescription"),
      });
    },
    onError: (error) => {
      reportActionError(translate(getLocale(), "domains.siteActions.couldNotReloadWebserver"), error);
    },
  });

  return { enable, disable, reload, refresh };
}
