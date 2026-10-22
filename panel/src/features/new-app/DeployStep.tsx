import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

import type { FollowedJob } from "../../api/queries/jobs";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { KeyValueList } from "../../components/page/KeyValueList";
import { JobProgress } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { Card } from "../../components/ui/Card";
import { Disclosure } from "../../components/ui/Disclosure";
import { textLinkClassName } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { containerSummary } from "../container/summary";
import { normalizeDomain } from "../domains/names";
import { JobOutcome } from "./JobOutcome";
import { useDeploymentLanding } from "./useDeploymentLanding";
import type { LandingTarget } from "./useDeploymentLanding";
import { canIncludeWww, hasPort, joinList, typeName } from "./wizard";
import type { AddressMode, AppTypeOption, Inspection, ReviewForm, SourceForm } from "./wizard";

/** The branch and commit a source was inspected at, as a hint under it. */
function revisionOf(t: T, branch: string, commit: string): string | null {
  if (branch && commit) return t("newApp.deploy.revisionBoth", { branch, commit });
  if (branch) return t("newApp.deploy.revisionBranch", { branch });
  if (commit) return t("newApp.deploy.revisionCommit", { commit });
  return null;
}

/** A "Change" that opens the step a fact came from, named for a screen reader by what it changes. */
function ChangeButton({ t, what, onClick }: { t: T; what: string; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className={textLinkClassName("inline", "cursor-pointer")}>
      {t("newApp.deploy.change")} <span className="sr-only">{what.toLowerCase()}</span>
    </button>
  );
}

/**
 * Where the application will answer, as a row of the summary. A temporary address says so, and
 * where the address step was passed over (`onChange` given) offers to open it: the summary is
 * where a person finds out they were not asked.
 */
export function addressItem(
  t: T,
  form: { domain: string; ssl: boolean; includeWww: boolean; addressMode?: AddressMode },
  onChange?: () => void,
): KeyValueItem {
  const domain = normalizeDomain(form.domain);
  const url = `${form.ssl ? "https" : "http"}://${domain}`;
  const temporary = form.addressMode === "temporary";
  return {
    label: t("newApp.deploy.address"),
    value: url,
    copy: url,
    ...(temporary
      ? {
          hint: (
            <>
              {t("newApp.address.temporaryHelp")}
              {onChange !== undefined ? (
                <>
                  {" "}
                  <ChangeButton t={t} what={t("newApp.deploy.address")} onClick={onChange} />
                </>
              ) : null}
            </>
          ),
        }
      : form.includeWww && canIncludeWww(domain)
        ? { hint: t("newApp.deploy.wwwRedirects", { domain }) }
        : {}),
  };
}

/** What is about to happen, in two parts: what a person wants to know, and how Noust does it behind "Details". */
export interface Summary {
  items: KeyValueItem[];
  details: KeyValueItem[];
}

/** The variables of a review, as one fact: how many, and how many are secret. */
function variablesItem(t: T, variables: readonly { secret: boolean }[], onChange?: () => void): KeyValueItem {
  const secrets = variables.filter((row) => row.secret).length;
  return {
    label: t("newApp.deploy.environment"),
    value:
      variables.length === 0
        ? t("newApp.deploy.noVariables")
        : secrets > 0
          ? t("newApp.deploy.variablesSecret", { count: variables.length, secrets: String(secrets) })
          : t("newApp.deploy.variables", { count: variables.length }),
    mono: false,
    copy: false,
    ...(onChange !== undefined ? { hint: <ChangeButton t={t} what={t("newApp.deploy.environment")} onClick={onChange} /> } : {}),
  };
}

/** The steps a summary can open, for the ones the wizard passed over because they had nothing to ask. */
export interface OpenSteps {
  address?: () => void;
  configure?: () => void;
  variables?: () => void;
}

/**
 * What is about to be deployed, as the operator reviewed it: what Noust detected, where it will
 * answer and which variables it has, first; the source, the port, the web server and how deploys
 * work, behind "Details", because they are how Noust does it and not what a person asked for.
 */
export function deploySummary(
  t: T,
  source: SourceForm,
  inspection: Inspection,
  types: readonly AppTypeOption[],
  form: ReviewForm,
  open: OpenSteps = {},
): Summary {
  const variables = form.env.filter((row) => row.name.trim() !== "");
  const revision = revisionOf(t, source.branch.trim() || inspection.branch, inspection.commit);
  const paths = form.layout === "releases" ? form.persistentPaths.map((row) => row.value.trim()).filter((value) => value !== "") : [];
  // A container's releases are images, whatever the layout says: nothing to choose, nothing to say.
  const deploys: KeyValueItem[] =
    form.appType === "container"
      ? []
      : [
          {
            label: t("newApp.deploy.deploys"),
            value: t(form.layout === "releases" ? "newApp.deploy.releases" : "newApp.deploy.inplace"),
            mono: false,
            copy: false,
            ...(paths.length > 0 ? { hint: t("newApp.deploy.persistent", { paths: joinList(paths, t.locale) }) } : {}),
          },
        ];
  const container: KeyValueItem[] = form.appType === "container" ? containerSummary(t, form.container) : [];
  const type = typeName(types, form.appType);
  return {
    items: [
      {
        label: t("newApp.deploy.detected"),
        value: inspection.package_manager && form.appType === inspection.app_type ? t("newApp.deploy.typeUsing", { type, manager: inspection.package_manager }) : type,
        mono: false,
        copy: false,
        ...(form.appType === inspection.app_type
          ? open.configure !== undefined
            ? { hint: <ChangeButton t={t} what={t("newApp.deploy.detected")} onClick={open.configure} /> }
            : {}
          : { hint: t("newApp.deploy.chosenOver", { type: typeName(types, inspection.app_type) }) }),
      },
      addressItem(t, form, open.address),
      variablesItem(t, variables, open.variables),
    ],
    details: [
      { label: t("newApp.deploy.source"), value: source.source.trim(), ...(revision !== null ? { hint: revision } : {}) },
      ...(hasPort(form.appType) ? [{ label: t("newApp.deploy.port"), value: form.port.trim() }] : []),
      ...container,
      { label: t("newApp.deploy.webServer"), value: form.webserver === "apache" ? "Apache" : "nginx", mono: false, copy: false },
      ...deploys,
    ],
  };
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
  /** How Noust does it (the source, the port, the web server, how deploys work): behind "Details". */
  details?: readonly KeyValueItem[];
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
export function DeployStep({ kind, domain: typed, summary, details = [], failure, target, followedJob, onGone }: DeployStepProps) {
  const t = useT();
  const domain = normalizeDomain(typed);
  const importing = kind === "import";
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3">
        <Card padding="none">
          <div className="px-4 py-1">
            <KeyValueList items={summary} />
          </div>
        </Card>
        {details.length > 0 ? (
          <Disclosure variant="card" label={t("newApp.deploy.details")}>
            <KeyValueList items={details} />
          </Disclosure>
        ) : null}
      </div>
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
