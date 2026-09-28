import { useNavigate } from "@tanstack/react-router";
import { PackageOpen, Rocket } from "lucide-react";
import { useEffect } from "react";
import type { Ref } from "react";

import type { FollowedJob } from "../../api/queries/jobs";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Spinner } from "../../components/ui/Spinner";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { normalizeDomain } from "../domains/names";
import { JobOutcome } from "./JobOutcome";
import { useDeploymentLanding } from "./useDeploymentLanding";
import type { LandingTarget } from "./useDeploymentLanding";
import { canIncludeWww, hasPort, joinList, typeName } from "./wizard";
import type { AppTypeOption, Inspection, ReviewForm, SourceForm } from "./wizard";

/** The branch and commit a source was inspected at, as a hint under it. */
function revisionOf(t: T, branch: string, commit: string): string | null {
  if (branch && commit) return t("newApp.deploy.revisionBoth", { branch, commit });
  if (branch) return t("newApp.deploy.revisionBranch", { branch });
  if (commit) return t("newApp.deploy.revisionCommit", { commit });
  return null;
}

/** Where the application will answer, as a row of the summary. */
export function addressItem(t: T, form: { domain: string; ssl: boolean; includeWww: boolean }): KeyValueItem {
  const domain = normalizeDomain(form.domain);
  const url = `${form.ssl ? "https" : "http"}://${domain}`;
  return {
    label: t("newApp.deploy.address"),
    value: url,
    copy: url,
    ...(form.includeWww && canIncludeWww(domain) ? { hint: t("newApp.deploy.wwwRedirects", { domain }) } : {}),
  };
}

/** What is about to be deployed, one fact per row, as the operator reviewed it. */
export function deploySummary(t: T, source: SourceForm, inspection: Inspection, types: readonly AppTypeOption[], form: ReviewForm): KeyValueItem[] {
  const variables = form.env.filter((row) => row.name.trim() !== "");
  const secrets = variables.filter((row) => row.secret).length;
  const revision = revisionOf(t, source.branch.trim() || inspection.branch, inspection.commit);
  const paths = form.layout === "releases" ? form.persistentPaths.map((row) => row.value.trim()).filter((value) => value !== "") : [];
  return [
    { label: t("newApp.deploy.source"), value: source.source.trim(), ...(revision !== null ? { hint: revision } : {}) },
    {
      label: t("newApp.deploy.type"),
      value: typeName(types, form.appType),
      mono: false,
      copy: false,
      hint: form.appType === inspection.app_type ? t("newApp.deploy.asDetected") : t("newApp.deploy.chosenOver", { type: typeName(types, inspection.app_type) }),
    },
    addressItem(t, form),
    ...(hasPort(form.appType) ? [{ label: t("newApp.deploy.port"), value: form.port.trim() }] : []),
    { label: t("newApp.deploy.webServer"), value: form.webserver === "apache" ? "Apache" : "nginx", mono: false, copy: false },
    {
      label: t("newApp.deploy.deploys"),
      value: t(form.layout === "releases" ? "newApp.deploy.releases" : "newApp.deploy.inplace"),
      mono: false,
      copy: false,
      ...(paths.length > 0 ? { hint: t("newApp.deploy.persistent", { paths: joinList(paths, t.locale) }) } : {}),
    },
    {
      label: t("newApp.deploy.environment"),
      value:
        variables.length === 0
          ? t("newApp.deploy.noVariables")
          : secrets > 0
            ? t("newApp.deploy.variablesSecret", { count: variables.length, secrets: String(secrets) })
            : t("newApp.deploy.variables", { count: variables.length }),
      mono: false,
      copy: false,
    },
  ];
}

/** After the deploy was queued: waiting for the deployer to record a deployment, then going to it. */
function Landing({ target, followedJob, onGone, onBack }: { target: LandingTarget; followedJob: FollowedJob; onGone: () => void; onBack: () => void }) {
  const t = useT();
  const navigate = useNavigate();
  const landing = useDeploymentLanding(target, followedJob);

  useEffect(() => {
    if (landing?.kind === "deployment") {
      onGone();
      void navigate({ to: "/apps/$domain/deployments/$id", params: { domain: target.domain, id: String(landing.id) }, replace: true });
    } else if (landing?.kind === "app") {
      onGone();
      void navigate({ to: "/apps/$domain", params: { domain: target.domain }, replace: true });
    }
  }, [landing, navigate, onGone, target.domain]);

  if (landing?.kind === "failed") {
    const job = followedJob.job;
    return (
      <div className="flex flex-col gap-3">
        <ErrorBlock
          live
          error={{ detail: job?.error ?? t("newApp.deploy.failedSilently") }}
          title={t("newApp.deploy.failedBeforeStart", { domain: target.domain })}
          hint={t("newApp.deploy.failedHint")}
        />
        <div>
          <Button onClick={onBack}>{t("newApp.deploy.backToReview")}</Button>
        </div>
      </div>
    );
  }

  const step = followedJob.job?.current_step ?? null;
  return (
    <div role="status" className="flex min-w-0 flex-col gap-1 rounded-card border border-border bg-surface px-4 py-3 shadow-raised">
      <p className="flex items-center gap-2.5 text-14 font-medium text-fg">
        <Spinner size={16} className="text-warn" />
        {t("newApp.deploy.deploying", { domain: target.domain })}
      </p>
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-13 text-fg-muted">
        <span>{t("newApp.deploy.buildLogSoon")}</span>
        {step ? (
          <code translate="no" className="min-w-0 truncate text-12" title={step}>
            {step}
          </code>
        ) : null}
      </p>
    </div>
  );
}

/** What the application is started from; decides the words, and whether the page hands over. */
export type DeployKind = "code" | "recipe" | "import";

const INTRO: Readonly<Record<DeployKind, PlainKey>> = {
  code: "newApp.deploy.intro",
  recipe: "newApp.deploy.introRecipe",
  import: "newApp.deploy.introImport",
};

export interface DeployStepProps {
  kind: DeployKind;
  /** The domain it is created on, as typed. */
  domain: string;
  /** What is about to be done, one fact per row. */
  summary: readonly KeyValueItem[];
  onDeploy: () => void;
  deploying: boolean;
  /** The failure of the last attempt to queue it, when it is not about a field. */
  failure: unknown;
  /** Where the deploy `onDeploy` queued is followed to, once it exists. */
  target: LandingTarget | null;
  /** The job queued by `onDeploy`, followed for its status and its own failure. */
  followedJob: FollowedJob;
  onBack: () => void;
  /** Called just before the wizard navigates away for good. */
  onGone: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}

/**
 * Step three: the summary, and the one button. Once queued, a deploy from code waits for the
 * build to start and hands over to its deployment page, where the log streams; a recipe or an
 * import is followed here to its end, where it says what comes next.
 */
export function DeployStep({ kind, domain: typed, summary, onDeploy, deploying, failure, target, followedJob, onBack, onGone, headingRef }: DeployStepProps) {
  const t = useT();
  const domain = normalizeDomain(typed);
  const importing = kind === "import";
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 ref={headingRef} tabIndex={-1} className="title text-18 text-fg outline-none">
          {t("newApp.deploy.heading")}
        </h2>
        <p className="text-14 text-pretty text-fg-muted">{t(INTRO[kind])}</p>
      </header>

      <div className="rounded-card border border-border bg-surface px-4 py-1 shadow-raised">
        <KeyValueList items={summary} />
      </div>

      {target !== null ? (
        kind === "code" ? (
          <Landing key={target.jobId} target={target} followedJob={followedJob} onGone={onGone} onBack={onBack} />
        ) : (
          <JobOutcome key={target.jobId} kind={kind} target={target} followedJob={followedJob} onBack={onBack} />
        )
      ) : (
        <>
          {failure !== null && failure !== undefined ? (
            <ErrorBlock live error={failure} title={t(importing ? "newApp.deploy.notImported" : "newApp.deploy.notDeployed", { domain })} />
          ) : null}
          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-6">
            <Button disabled={deploying} onClick={onBack}>
              {t("newApp.deploy.back")}
            </Button>
            <Button
              variant="primary"
              size="lg"
              icon={importing ? <PackageOpen aria-hidden="true" /> : <Rocket aria-hidden="true" />}
              loading={deploying}
              onClick={onDeploy}
            >
              {t(importing ? "newApp.deploy.importAction" : "newApp.deploy.action", { domain })}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
