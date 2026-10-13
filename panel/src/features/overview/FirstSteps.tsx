import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Bell, Boxes, DatabaseBackup, HardDriveUpload, KeyRound, Stethoscope } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { configQuery } from "../../api/queries/config";
import type { Overview } from "../../api/queries/overview";
import { installCheckQuery } from "../../api/queries/system";
import { buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { ICONS } from "../../components/ui/icons";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";
import { readNotificationSettings } from "../settings/notifications";

interface Step {
  key: string;
  icon: ReactNode;
  title: PlainKey;
  description: PlainKey;
  action: PlainKey;
  to: "/apps/new" | "/backups" | "/backups/destinations" | "/settings/about" | "/settings/notifications" | "/settings/security";
  done: boolean;
}

/**
 * An empty server's Overview: what to do first, in order, each step with the one action that
 * does it and ticked once done. It replaces the figures and the charts: four empty charts are not
 * a welcome.
 */
export function FirstSteps({ overview }: { overview: Overview }) {
  const t = useT();
  const session = useQuery(sessionQuery());
  const config = useQuery(configQuery());
  const notifications = config.data === undefined ? false : readNotificationSettings(config.data.config).enabled;
  // Neither read is worth an error on a welcome: a step whose answer is not known is not ticked.
  const installation = useQuery({ ...installCheckQuery(), retry: false });
  const destinations = useQuery({ ...backupDestinationsQuery(), retry: false });
  const steps: Step[] = [
    {
      key: "check",
      icon: <Stethoscope />,
      title: "overview.firstSteps.check.title",
      description: "overview.firstSteps.check.description",
      action: "overview.firstSteps.check.action",
      to: "/settings/about",
      done: installation.data?.ready === true,
    },
    {
      key: "deploy",
      icon: <Boxes />,
      title: "overview.firstSteps.deploy.title",
      description: "overview.firstSteps.deploy.description",
      action: "overview.firstSteps.deploy.action",
      to: "/apps/new",
      done: false,
    },
    {
      key: "backups",
      icon: <DatabaseBackup />,
      title: "overview.firstSteps.backups.title",
      description: "overview.firstSteps.backups.description",
      action: "overview.firstSteps.backups.action",
      to: "/backups",
      done: overview.backups.scheduled > 0,
    },
    {
      key: "destination",
      icon: <HardDriveUpload />,
      title: "overview.firstSteps.destination.title",
      description: "overview.firstSteps.destination.description",
      action: "overview.firstSteps.destination.action",
      to: "/backups/destinations",
      done: (destinations.data?.destinations.length ?? 0) > 0,
    },
    {
      key: "notifications",
      icon: <Bell />,
      title: "overview.firstSteps.notifications.title",
      description: "overview.firstSteps.notifications.description",
      action: "overview.firstSteps.notifications.action",
      to: "/settings/notifications",
      done: notifications,
    },
    {
      key: "twoFactor",
      icon: <KeyRound />,
      title: "overview.firstSteps.twoFactor.title",
      description: "overview.firstSteps.twoFactor.description",
      action: "overview.firstSteps.twoFactor.action",
      to: "/settings/security",
      done: session.data?.totp_enabled === true,
    },
  ];
  const Done = ICONS.success;
  return (
    <Card as="section" title={t("overview.firstSteps.title")} description={t("overview.firstSteps.description")} level={2} padding="none">
      <ol aria-label={t("overview.firstSteps.listLabel")} className="divide-y divide-border">
        {steps.map((step, index) => {
          const outcome = step.done ? (
            <span className="flex items-center gap-1.5 text-13 text-fg-muted">
              <Done aria-hidden="true" className="size-icon-sm" />
              {t("overview.firstSteps.done")}
            </span>
          ) : (
            <Link to={step.to} className={buttonClassName("secondary", "sm")}>
              {t(step.action)}
            </Link>
          );
          return (
            <li key={step.key} className="flex min-w-0 items-start gap-3 px-5 py-4 sm:items-center sm:gap-4">
              <span
                aria-hidden="true"
                className="flex size-8 shrink-0 items-center justify-center rounded-control border border-border bg-bg-sunken text-fg-muted [&_svg]:size-icon-md"
              >
                {step.icon}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <p className={cx("text-14 font-medium", step.done ? "text-fg-muted" : "text-fg")}>
                  <span className="mr-1.5 text-fg-faint tabular-nums">{`${String(index + 1)}.`}</span>
                  {t(step.title)}
                </p>
                <p className="max-w-measure text-13 text-pretty text-fg-muted">{t(step.description)}</p>
                <div className="mt-2 flex sm:hidden">{outcome}</div>
              </div>
              <div className="hidden shrink-0 sm:flex">{outcome}</div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
