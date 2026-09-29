import { Archive, FolderGit2, FolderOpen, GitBranch, Search, TriangleAlert, X } from "lucide-react";
import { Link } from "@tanstack/react-router";
import type { ReactNode, Ref, SyntheticEvent } from "react";

import type { GitHubStatus } from "../../api/queries/github";

import { CommandHint } from "../../components/page/CommandHint";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import type { SegmentedOption } from "../../components/page/SegmentedControl";
import { isApiError } from "../../api/client";
import { ErrorBlock } from "../../components/page/QueryState";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Skeleton } from "../../components/ui/Skeleton";
import { Spinner } from "../../components/ui/Spinner";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { GitHubSource } from "./GitHubSource";
import { Suggestion } from "./Suggestion";
import { SOURCE_WORDS, sourceKind } from "./wizard";
import type { SourceErrors, SourceForm, SourceKind } from "./wizard";

const KIND_ICON: Record<SourceKind, ReactNode> = {
  github: <FolderGit2 />,
  git: <GitBranch />,
  archive: <Archive />,
  local: <FolderOpen />,
  unknown: <FolderGit2 />,
};

/** How long the inspection has been running, in whole seconds, so a slow clone reads as alive. */
function Elapsed({ since }: { since: number }) {
  const now = useNow(() => 1000);
  const t = useT();
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  return <span className="mono text-12 text-fg-faint">{t("newApp.source.elapsed", { seconds: String(seconds) })}</span>;
}

/** The shape of the Review step, while the source is fetched and read. */
function ReviewSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-4 rounded-card border border-border bg-surface p-4 shadow-raised">
      <div className="flex items-center gap-3">
        <Skeleton className="h-4 w-4 rounded-[4px]" />
        <Skeleton className="h-3 w-64 max-w-full" />
      </div>
      <div className="flex flex-col gap-2.5 rounded-control bg-bg-sunken p-3">
        {["w-32", "w-44", "w-36"].map((width) => (
          <div key={width} className="flex items-center gap-4">
            <Skeleton className="h-3 w-12" />
            <Skeleton className={`h-3 ${width}`} />
          </div>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
      </div>
    </div>
  );
}

/** What the inspection is doing, said once: there is no streamed progress, so no steps are made up. */
const READING: Readonly<Record<SourceKind, PlainKey>> = {
  github: "newApp.source.reading.github",
  git: "newApp.source.reading.git",
  archive: "newApp.source.reading.archive",
  local: "newApp.source.reading.local",
  unknown: "newApp.source.reading.unknown",
};

/** Codes of a source that was fetched and read, but is not something Noust deploys as it is. */
const VERDICT_ERRORS: ReadonlySet<string> = new Set(["validationerror", "deploymenterror"]);

/**
 * The inspection's verdict on a source it could read but not classify: what it found (a lone
 * Dockerfile, a Rust project, projects in subdirectories) as the backend said it, what would
 * make it deployable - often a file to add, shown as one - and anything a tool printed,
 * verbatim. The type can still be chosen by hand.
 */
function VerdictFailure({ failure, source, onManual }: { failure: { detail: string; hint: string | null; output: string | null }; source: string; onManual: () => void }) {
  const t = useT();
  const title = t("newApp.source.cannotDeploy", { source });
  return (
    <div role="alert" className="flex min-w-0 flex-col gap-3 rounded-card border border-warn/40 bg-warn-soft/50 p-4">
      <div className="flex items-start gap-2">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-13 font-medium text-fg">{title}</p>
          <p className="text-13 text-pretty text-fg">{failure.detail}</p>
        </div>
      </div>
      {failure.hint !== null ? <Suggestion text={failure.hint} className="pl-6" /> : null}
      {failure.output !== null && failure.output.trim() !== "" ? (
        <SystemOutput label={t("newApp.source.cannotDeployOutput", { source })} maxHeight="max-h-48" className="rounded-control border border-border bg-surface px-3 py-2">
          {failure.output}
        </SystemOutput>
      ) : null}
      <div className="pl-6">
        <Button onClick={onManual}>{t("newApp.source.chooseType")}</Button>
      </div>
    </div>
  );
}

/**
 * Why the inspection failed. A source that could not be fetched is marked on its field, and
 * what the fetch printed (git's own words) is shown here verbatim. A source that was fetched
 * but matched no type can still be deployed: the operator picks the type.
 */
function InspectFailure({ failure, source, onManual }: { failure: unknown; source: string; onManual: () => void }) {
  const t = useT();
  // For a repository read through the GitHub App, the usual cause is the installation, not a key.
  const fetchHint = t(sourceKind(source) === "github" ? "newApp.source.githubFetchHint" : "newApp.source.fetchHint");
  let block: ReactNode;
  if (isApiError(failure) && failure.error === "sourceerror") {
    // The short sentence (failure.detail) is already on the field, next to what it complains
    // about; this block is for what the tool itself printed - git's own words, verbatim, when
    // the backend carried them as `output` (a private repository's auth failure, for example).
    // An older or simpler SourceError has no output of its own; `hint` is what it printed then.
    const printed = failure.output ?? failure.hint;
    if (printed === null) return null;
    const fix = failure.output !== null ? (failure.hint ?? fetchHint) : fetchHint;
    block = <ErrorBlock live error={{ detail: printed }} title={t("newApp.source.fetchReported", { source })} hint={fix} />;
  } else if (isApiError(failure) && VERDICT_ERRORS.has(failure.error) && failure.status === 400) {
    block = <VerdictFailure failure={failure} source={source} onManual={onManual} />;
  } else if (isApiError(failure) && failure.error === "deploymenterror") {
    // An older backend answered an unclassified source 500 deploymenterror.
    block = (
      <>
        <ErrorBlock live error={failure} title={t("newApp.source.couldNotTell", { source })} />
        <div>
          <Button onClick={onManual}>{t("newApp.source.chooseType")}</Button>
        </div>
      </>
    );
  } else {
    block = <ErrorBlock live error={failure} title={t("newApp.source.couldNotInspect", { source })} hint={fetchHint} />;
  }
  return (
    <div className="flex flex-col gap-3">
      {block}
    </div>
  );
}

export interface SourceStepProps {
  form: SourceForm;
  errors: SourceErrors;
  onChange: (form: SourceForm) => void;
  onSubmit: () => void;
  /** The inspection in flight, and when it started. */
  inspecting: { since: number } | null;
  onCancel: () => void;
  /** Why the last inspection failed, verbatim. */
  failure: unknown;
  /** An inspection of exactly this source is already in hand: continuing needs no new one. */
  inspected: boolean;
  onInspectAgain: () => void;
  /** Go on without detection: the operator chooses the type. */
  onManual: () => void;
  headingRef: Ref<HTMLHeadingElement>;
  /** This server's GitHub integration; null while unknown or when it could not be read. */
  github: GitHubStatus | null;
  /** Where the source comes from: a repository the GitHub App reaches, or typed. */
  mode: SourceMode;
  onModeChange: (mode: SourceMode) => void;
  /** What stands in the step's place when the application starts from a recipe. */
  recipes: ReactNode;
  /** What stands in the step's place when the application is imported from an export. */
  importer: ReactNode;
}

/** Where the application comes from: code (GitHub, or a typed source), a recipe, or an export. */
export type SourceMode = "github" | "manual" | "recipe" | "import";

const MODES: readonly { value: SourceMode; label: PlainKey }[] = [
  { value: "github", label: "newApp.modes.github" },
  { value: "manual", label: "newApp.modes.manual" },
  { value: "recipe", label: "newApp.modes.recipe" },
  { value: "import", label: "newApp.modes.import" },
];

const INTRO: Readonly<Record<SourceMode, PlainKey>> = {
  github: "newApp.source.intro",
  manual: "newApp.source.intro",
  recipe: "newApp.source.introRecipe",
  import: "newApp.source.introImport",
};

/**
 * Step one: where the code is. Noust fetches it into a throwaway checkout and reads it, so the
 * next step proposes real commands, a real port and the variables the project declares.
 */
export function SourceStep({
  form,
  errors,
  onChange,
  onSubmit,
  inspecting,
  onCancel,
  failure,
  inspected,
  onInspectAgain,
  onManual,
  headingRef,
  github,
  mode,
  onModeChange,
  recipes,
  importer,
}: SourceStepProps) {
  const t = useT();
  const kind = sourceKind(form.source);
  const local = kind === "local";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    onSubmit();
  };
  const busy = inspecting !== null;
  const shown = form.source.trim();
  // "From GitHub" is offered only where this server has a GitHub App to read repositories with.
  const modes: SegmentedOption<SourceMode>[] = MODES.filter((option) => option.value !== "github" || github?.configured === true).map((option) => ({
    value: option.value,
    label: t(option.label),
  }));

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 ref={headingRef} tabIndex={-1} className="title text-18 text-fg outline-none">
          {t("newApp.source.heading")}
        </h2>
        <p className="text-14 text-pretty text-fg-muted">{t(INTRO[mode])}</p>
      </header>

      <SegmentedControl label={t("newApp.modes.label")} options={modes} value={mode} onValueChange={onModeChange} className="max-w-full flex-wrap self-start" />

      {mode === "recipe" ? (
        recipes
      ) : mode === "import" ? (
        importer
      ) : (
        <form onSubmit={submit} noValidate className="flex flex-col gap-6">
          {mode === "github" && github?.configured === true ? (
            <GitHubSource status={github} form={form} errors={errors} onChange={onChange} disabled={busy} />
          ) : (
            <>
              <Field
                label={t("newApp.source.field")}
                error={errors.source}
                description={
                  github !== null && !github.configured ? (
                    <>
                      {`${t(SOURCE_WORDS[kind])} `}
                      {t.rich("newApp.source.githubPrompt", {
                        link: (
                          <Link
                            to="/settings/integrations"
                            className="rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                          >
                            {t("newApp.source.connect")}
                          </Link>
                        ),
                      })}
                    </>
                  ) : (
                    t(SOURCE_WORDS[kind])
                  )
                }
              >
                <Input
                  mono
                  icon={KIND_ICON[kind]}
                  value={form.source}
                  onValueChange={(value: string) => onChange({ source: value, branch: form.branch })}
                  placeholder="https://github.com/you/app.git"
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  disabled={busy}
                />
              </Field>

              {local ? null : (
                <Field
                  label={t("newApp.source.branch")}
                  optional
                  error={errors.branch}
                  description={t("newApp.source.branchDescription")}
                  className="sm:max-w-80"
                >
                  <Input
                    mono
                    icon={<GitBranch />}
                    value={form.branch}
                    onValueChange={(value: string) => onChange({ source: form.source, branch: value })}
                    placeholder="main"
                    autoComplete="off"
                    autoCapitalize="off"
                    spellCheck={false}
                    disabled={busy}
                  />
                </Field>
              )}
            </>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {inspected && !busy ? (
              <>
                <Button type="submit" variant="primary">
                  {t("newApp.source.continue")}
                </Button>
                <Button variant="ghost" onClick={onInspectAgain}>
                  {t("newApp.source.inspectAgain")}
                </Button>
              </>
            ) : (
              <Button type="submit" variant="primary" icon={<Search aria-hidden="true" />} loading={busy}>
                {t("newApp.source.inspect")}
              </Button>
            )}
            {busy ? (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={onCancel}>
                {t("newApp.source.cancel")}
              </Button>
            ) : null}
          </div>

          {busy ? (
            <div className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <p role="status" className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 text-13 text-fg">
                  <Spinner size={14} className="text-warn" />
                  <span>{t(READING[kind])}</span>
                  <code translate="no" className="min-w-0 truncate text-12 text-fg-muted" title={shown}>
                    {shown}
                  </code>
                  {!local && form.branch.trim() !== "" ? <span className="text-fg-muted">{t("newApp.source.atBranch", { branch: form.branch.trim() })}</span> : null}
                  <Elapsed since={inspecting.since} />
                </p>
                <p className="text-12 text-pretty text-fg-muted">
                  {t(local ? "newApp.source.readsLocal" : "newApp.source.readsRemote")}
                </p>
              </div>
              <ReviewSkeleton />
            </div>
          ) : failure !== null && failure !== undefined ? (
            <InspectFailure failure={failure} source={shown} onManual={onManual} />
          ) : null}

          <CommandHint command={`noust create --domain example.com --source ${shown === "" ? "https://github.com/you/app.git" : shown}`} label={t("newApp.source.terminal")} />
        </form>
      )}
    </div>
  );
}
