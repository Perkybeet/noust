import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { appsQuery } from "../../api/queries/apps";
import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import type { PageHeaderProps } from "../../app/PageHeader";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { APPS_TABS } from "../nav/appsTabs";
import { useOrganised } from "../teams/useOrganised";
import { useOwnerScope } from "../teams/ownership";

/** The rows of a list that has not arrived: one array, so what is memoised on it is not redone. */
const NO_APPS: readonly never[] = [];

/**
 * The views of Applications as URLs: the applications and, for a server whose people organised theirs,
 * the projects they are filed in. Read from the register, like every strip. A server nobody organised has
 * one view, and one view is no strip: nothing is drawn, and nothing is reserved for it.
 */
export function useAppsStrip(): ReactNode {
  const t = useT();
  const apps = useQuery(appsQuery());
  const { organised } = useOrganised();
  // The same count the list says: the applications of the team in view.
  const owner = useOwnerScope(apps.data?.apps ?? NO_APPS, {});
  const tabs = withCounts(tabsOf(APPS_TABS, { when: { organised } }), {
    "/apps": apps.data === undefined ? null : owner.inTeam.length,
  });
  return tabs.length > 1 ? <LinkTabs label={t("nav.landmarks.appsSections")} tabs={tabs} /> : undefined;
}

/**
 * The header of the list of applications: the title and the one sentence, and the view's own actions.
 * Where the page is in the console is the top bar's, and the server is named there and in the second
 * column, never above a title.
 */
export function appsHeader(t: T, view: Pick<PageHeaderProps, "primaryAction" | "secondaryActions"> = {}): PageHeaderProps {
  return { title: t("apps.page.title"), description: t("apps.page.description"), ...view };
}
