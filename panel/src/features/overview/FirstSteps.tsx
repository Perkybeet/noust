import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Archive, ArrowRight, Bell, GitBranch, HardDriveUpload, LibraryBig, Play, Rocket, Shield, Stethoscope } from "lucide-react";
import type { ReactNode } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { backupDestinationsQuery } from "../../api/queries/backupDestinations";
import { configQuery } from "../../api/queries/config";
import type { Overview } from "../../api/queries/overview";
import { recipesQuery } from "../../api/queries/recipes";
import { installCheckQuery } from "../../api/queries/system";
import { buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { ICONS } from "../../components/ui/icons";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { cx } from "../../lib/cx";
import { popularRecipes } from "../catalog/model";
import { readNotificationSettings } from "../settings/notifications";

/** One row of a card: what it is, in a sentence, and the one thing that does it. */
function Row({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return (
    // One copy of the actions: beside the text from a tablet up, under it (aligned with it) on a phone.
    <li className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-3 px-5 py-4 sm:flex-nowrap">
      <span
        aria-hidden="true"
        className="flex size-8 shrink-0 items-center justify-center rounded-control border border-border bg-bg-sunken text-fg-muted [&_svg]:size-icon-md"
      >
        {icon}
      </span>
      <div className="flex min-w-0 flex-1 basis-48 flex-col gap-0.5">
        <p className="text-14 font-medium text-fg">{title}</p>
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{description}</p>
      </div>
      {/* The icon (2rem) and the gap (1rem) are the 3rem the actions keep clear on a phone, to sit under the text. */}
      <div className="flex basis-full flex-wrap items-center gap-2 pl-12 sm:basis-auto sm:shrink-0 sm:justify-end sm:pl-0">{children}</div>
    </li>
  );
}

interface SafeStep {
  key: string;
  icon: ReactNode;
  title: PlainKey;
  description: PlainKey;
  action: PlainKey;
  to: "/backups" | "/backups/destinations" | "/settings/notifications" | "/account/security";
  done: boolean;
}

/**
 * An empty server's Overview: how to get something live, and then how to leave it alone. It
 * replaces the figures and the charts: four empty charts are not a welcome. The first card is the
 * only thing that asks for a decision (a demo, a repository or a service), each row with its own
 * button; the second is folded, because backups and notifications are for after the first deploy
 * and nobody needs them to see a page come up.
 */
export function FirstSteps({ overview }: { overview: Overview }) {
  const t = useT();
  const session = useQuery(sessionQuery());
  const config = useQuery(configQuery());
  const recipes = useQuery({ ...recipesQuery(), retry: false });
  const notifications = config.data === undefined ? false : readNotificationSettings(config.data.config).enabled;
  // None of these reads is worth an error on a welcome: a step whose answer is not known is not ticked.
  const installation = useQuery({ ...installCheckQuery(), retry: false });
  const destinations = useQuery({ ...backupDestinationsQuery(), retry: false });
  const protectedAccount = session.data?.totp_enabled === true || session.data?.account?.mfa_enabled === true;
  const safe: SafeStep[] = [
    {
      key: "backups",
      icon: <Archive />,
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
    // Offered here only while the account is not protected; "Set up later" at the door lands on it.
    ...(session.data !== undefined && !protectedAccount
      ? [
          {
            key: "twoFactor",
            icon: <Shield />,
            title: "overview.firstSteps.twoFactor.title",
            description: "overview.firstSteps.twoFactor.description",
            action: "overview.firstSteps.twoFactor.action",
            to: "/account/security",
            done: false,
          } satisfies SafeStep,
        ]
      : []),
  ];
  const left = safe.filter((step) => !step.done).length;
  const Done = ICONS.success;
  const popular = popularRecipes(recipes.data?.items);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {/* A server that cannot deploy yet says so before anything else, in one line with its fix. */}
      {installation.data?.ready === false ? (
        <InlineMessage tone="warning">
          {t("overview.firstSteps.check.message")}{" "}
          <TextLink to="/settings/about" size="ui" className="inline-flex items-center gap-1.5">
            <Stethoscope aria-hidden="true" className="size-icon-sm" />
            {t("overview.firstSteps.check.action")}
          </TextLink>
        </InlineMessage>
      ) : null}
      <Card as="section" title={t("overview.firstSteps.title")} description={t("overview.firstSteps.description")} level={2} padding="none">
        <ul className="divide-y divide-border">
          <Row icon={<Rocket />} title={t("overview.firstSteps.demo.title")} description={t("overview.firstSteps.demo.description")}>
            <Link to="/apps/new" search={{ start: "recipe", recipe: "hello", deploy: "now" }} className={buttonClassName("secondary", "sm")}>
              <Play aria-hidden="true" />
              {t("overview.firstSteps.demo.action")}
            </Link>
          </Row>
          <Row icon={<GitBranch />} title={t("overview.firstSteps.repository.title")} description={t("overview.firstSteps.repository.description")}>
            <Link to="/apps/new" className={buttonClassName("secondary", "sm")}>
              <ArrowRight aria-hidden="true" />
              {t("overview.firstSteps.repository.action")}
            </Link>
          </Row>
          <Row icon={<LibraryBig />} title={t("overview.firstSteps.catalog.title")} description={t("overview.firstSteps.catalog.description")}>
            {popular.length > 0 ? (
              <ul aria-label={t("overview.firstSteps.catalog.pills")} className="flex flex-wrap items-center gap-2">
                {popular.map((recipe) => (
                  <li key={recipe.name}>
                    <Link to="/apps/new" search={{ start: "recipe", recipe: recipe.name }} className={buttonClassName("secondary", "sm")}>
                      {recipe.title}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
            <TextLink to="/catalog" size="ui" className="inline-flex items-center gap-1">
              {t("overview.firstSteps.catalog.all")}
              <ArrowRight aria-hidden="true" className="size-icon-sm" />
            </TextLink>
          </Row>
        </ul>
      </Card>
      <Disclosure
        variant="card"
        label={t("overview.firstSteps.safe.title")}
        summary={left === 0 ? t("overview.firstSteps.safe.allDone") : t("overview.firstSteps.safe.left", { count: left })}
      >
        <ul aria-label={t("overview.firstSteps.safe.list")} className="-mx-4 divide-y divide-border">
          {safe.map((step) => (
            <li key={step.key} className="flex min-w-0 items-start gap-3 px-4 py-3 sm:items-center sm:gap-4">
              <span
                aria-hidden="true"
                className="flex size-8 shrink-0 items-center justify-center rounded-control border border-border bg-bg-sunken text-fg-muted [&_svg]:size-icon-md"
              >
                {step.icon}
              </span>
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <p className={cx("text-14 font-medium", step.done ? "text-fg-muted" : "text-fg")}>{t(step.title)}</p>
                <p className="max-w-measure text-13 text-pretty text-fg-muted">{t(step.description)}</p>
              </div>
              <div className="shrink-0">
                {step.done ? (
                  <span className="flex items-center gap-1.5 text-13 text-fg-muted">
                    <Done aria-hidden="true" className="size-icon-sm" />
                    {t("overview.firstSteps.done")}
                  </span>
                ) : (
                  <Link to={step.to} className={buttonClassName("secondary", "sm")}>
                    {t(step.action)}
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}
