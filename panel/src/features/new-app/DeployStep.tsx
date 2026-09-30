import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

import type { FollowedJob } from "../../api/queries/jobs";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { JobProgress } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
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
function Landing({ target, followedJob, onGone }: { target: LandingTarget; followedJob: FollowedJob; onGone: () => void }) {
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
      <ErrorBlock
        live
        error={{ detail: job?.error ?? t("newApp.deploy.failedSilently") }}
        title={t("newApp.deploy.failedBeforeStart", { domain: target.domain })}
        hint={t("newApp.deploy.failedHint")}
      />
    );
  }

  return (
    <JobProgress
      state={followedJob.job?.status === "pending" ? "queued" : "running"}
      title={t("newApp.deploy.deploying", { domain: target.domain })}
      step={followedJob.job?.current_step ?? null}
      description={t("newApp.deploy.buildLogSoon")}
    />
  );
}

/** What the application is started from; decides the words, and whether the page hands over. */
export type DeployKind = "code" | "recipe" | "import";

export interface DeployStepProps {
  kind: DeployKind;
  /** The domain it is created on, as typed. */
  domain: string;
  /** What is about to be done, one fact per row. */
  summary: readonly KeyValueItem[];
  /** The failure of the last attempt to queue it, when it is not about a field. */
  failure: unknown;
  /** Where the deploy was queued to, once it is. */
  target: LandingTarget | null;
  /** The job queued, followed for its status and its own failure. */
  followedJob: FollowedJob;
  /** Called just before the wizard navigates away for good. */
  onGone: () => void;
}

/**
 * The last step: the summary of what is about to happen, and once the wizard's Deploy is
 * pressed, the job in hand. A deploy from code waits for the build to start and hands over to
 * its deployment page, where the log streams; a recipe or an import is followed here to its
 * end, where it says what comes next.
 */
export function DeployStep({ kind, domain: typed, summary, failure, target, followedJob, onGone }: DeployStepProps) {
  const t = useT();
  const domain = normalizeDomain(typed);
  const importing = kind === "import";
  return (
    <div className="flex flex-col gap-6">
      <Card padding="none">
        <div className="px-4 py-1">
          <KeyValueList items={summary} />
        </div>
      </Card>
      {target !== null ? (
        kind === "code" ? (
          <Landing key={target.jobId} target={target} followedJob={followedJob} onGone={onGone} />
        ) : (
          <JobOutcome key={target.jobId} kind={kind} target={target} followedJob={followedJob} />
        )
      ) : failure !== null && failure !== undefined ? (
        <ErrorBlock live error={failure} title={t(importing ? "newApp.deploy.notImported" : "newApp.deploy.notDeployed", { domain })} />
      ) : null}
    </div>
  );
}
