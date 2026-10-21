import { Archive, ChevronDown, Container, FileUp, FolderGit2, FolderOpen, GitBranch, Layers, LibraryBig, Link2, Upload, X } from "lucide-react";
import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import type { GitHubStatus } from "../../api/queries/github";
import type { GitLabStatus } from "../../api/queries/gitlab";
import { isApiError } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { JobProgress } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import type { SegmentedOption } from "../../components/page/SegmentedControl";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Notice } from "../../components/ui/Notice";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { ArchiveUpload } from "../app/upload/ArchiveUpload";
import { PasteDockerfile } from "../app/upload/PasteDockerfile";
import { ConnectGitHub } from "./ConnectGitHub";
import { GitHubSource } from "./GitHubSource";
import { GitLabSource } from "./GitLabSource";
import { SshKeyHelp } from "./SshKeyHelp";
import { sshRepository } from "./sshSource";
import { Suggestion } from "./Suggestion";
import { WorkspacesDialog } from "./WorkspacesDialog";
import { SOURCE_WORDS, sourceKind } from "./wizard";
import type { SourceErrors, SourceForm, SourceKind } from "./wizard";

const KIND_ICON: Record<SourceKind, ReactNode> = {
  github: <FolderGit2 />,
  git: <GitBranch />,
  archive: <Archive />,
  local: <FolderOpen />,
  upload: <Upload />,
  unknown: <FolderGit2 />,
};

/** The source form: Enter in one of its fields does what the wizard's Continue does. */
const SOURCE_FORM = "new-app-source";

/** What the inspection is doing, said once: there is no streamed progress, so no step is made up. */
const READING: Readonly<Record<SourceKind, PlainKey>> = {
  github: "newApp.source.reading.github",
  git: "newApp.source.reading.git",
  archive: "newApp.source.reading.archive",
  local: "newApp.source.reading.local",
  upload: "newApp.source.reading.upload",
  unknown: "newApp.source.reading.unknown",
};

/** Codes of a source that was fetched and read, but is not something Noust deploys as it is. */
const VERDICT_ERRORS: ReadonlySet<string> = new Set(["validationerror", "deploymenterror"]);

/**
 * The inspection while it runs: what it is reading and for how long, the three things it does
 * with what it fetches, and a way to stop it. The inspection is one request, so the list says
 * what happens, never which part of it is done.
 */
function Inspecting({ source, branch, since, onCancel }: { source: string; branch: string; since: number; onCancel: () => void }) {
  const t = useT();
  const kind = sourceKind(source);
  const local = kind === "local" || kind === "upload";
  const now = useNow(() => 1000);
  const seconds = Math.max(0, Math.floor((now - since) / 1000));
  const step = !local && branch !== "" ? t("newApp.source.atBranchStep", { source, branch }) : source;
  return (
    <div className="flex flex-col gap-3">
      <JobProgress
        state="running"
        title={t(READING[kind])}
        step={step}
        description={t("newApp.source.elapsed", { seconds: String(seconds) })}
        action={
          <Button size="sm" variant="ghost" icon={<X aria-hidden="true" />} onClick={onCancel}>
            {t("newApp.source.cancel")}
          </Button>
        }
      />
      <ol aria-label={t("newApp.source.whatHappensLabel")} className="flex list-decimal flex-col gap-1 pl-5 text-13 text-pretty text-fg-muted marker:text-fg-muted">
        <li>{t(local ? "newApp.source.phaseCopy" : "newApp.source.phaseFetch")}</li>
        <li>{t("newApp.source.phaseDetect")}</li>
        <li>{t("newApp.source.phaseVariables")}</li>
      </ol>
    </div>
  );
}

/**
 * The inspection's verdict on a source it could read but not classify: what it found (a lone
 * Dockerfile, a Rust project, projects in subdirectories) as the backend said it, what would
 * make it deployable - often a file to add, shown as one - and anything a tool printed,
 * verbatim. The type can still be chosen by hand.
 */
function VerdictFailure({ failure, source, onManual }: { failure: { detail: string; hint: string | null; output: string | null }; source: string; onManual: () => void }) {
  const t = useT();
  return (
    <Notice
      tone="warning"
      live
      title={t("newApp.source.cannotDeploy", { source })}
      action={<Button size="sm" onClick={onManual}>{t("newApp.source.chooseType")}</Button>}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-fg">{failure.detail}</p>
        {failure.hint !== null ? <Suggestion text={failure.hint} /> : null}
        {failure.output !== null && failure.output.trim() !== "" ? (
          <SystemOutput label={t("newApp.source.cannotDeployOutput", { source })} maxHeight="max-h-48" className="rounded-control border border-border bg-surface px-3 py-2">
            {failure.output}
          </SystemOutput>
        ) : null}
      </div>
    </Notice>
  );
}

/**
 * Why the inspection failed. A source that could not be fetched is marked on its field, and
 * what the fetch printed (git's own words) is shown here verbatim. A source that was fetched
 * but matched no type can still be deployed: the operator picks the type.
 */
function InspectFailure({ failure, source, onManual, onRetry }: { failure: unknown; source: string; onManual: () => void; onRetry: () => void }) {
  const t = useT();
  // For a repository read through the GitHub App, the usual cause is the installation, not a key.
  const fetchHint = t(sourceKind(source) === "github" ? "newApp.source.githubFetchHint" : "newApp.source.fetchHint");
  if (isApiError(failure) && failure.error === "sourceerror") {
    // The short sentence (failure.detail) is already on the field, next to what it complains
    // about; this block is for what the tool itself printed - git's own words, verbatim, when
    // the backend carried them as `output` (a private repository's auth failure, for example).
    // An older or simpler SourceError has no output of its own; `hint` is what it printed then.
    const printed = failure.output ?? failure.hint;
    const fix = failure.output !== null ? (failure.hint ?? fetchHint) : fetchHint;
    // A private repository over SSH is the one fetch failure whose cure is a key, and the key is
    // this server's: it is given here, under git's words, instead of in the settings of an
    // application that does not exist yet.
    const ssh = sshRepository(source) !== null;
    if (printed === null && !ssh) return null;
    return (
      <div className="flex min-w-0 flex-col gap-3">
        {printed !== null ? <ErrorBlock live error={{ detail: printed }} title={t("newApp.source.fetchReported", { source })} hint={fix} /> : null}
        {ssh ? <SshKeyHelp source={source} onCheckAgain={onRetry} /> : null}
      </div>
    );
  }
  if (isApiError(failure) && VERDICT_ERRORS.has(failure.error) && failure.status === 400) {
    return <VerdictFailure failure={failure} source={source} onManual={onManual} />;
  }
  if (isApiError(failure) && failure.error === "deploymenterror") {
    // An older backend answered an unclassified source 500 deploymenterror.
    return (
      <div className="flex flex-col gap-3">
        <ErrorBlock live error={failure} title={t("newApp.source.couldNotTell", { source })} />
        <div>
          <Button onClick={onManual}>{t("newApp.source.chooseType")}</Button>
        </div>
      </div>
    );
  }
  return <ErrorBlock live error={failure} title={t("newApp.source.couldNotInspect", { source })} hint={fetchHint} />;
}

/** Where the application comes from: code (GitHub, or a typed source), a recipe, or an export. */
export type SourceMode = "github" | "gitlab" | "manual" | "image" | "compose" | "recipe" | "import" | "adopt";

/** The ways in a person starting from code or an image reaches for first: always on screen. */
const MODES: readonly { value: SourceMode; label: PlainKey }[] = [
  { value: "github", label: "newApp.modes.github" },
  { value: "gitlab", label: "gitlab.picker.mode" },
  { value: "manual", label: "newApp.modes.manual" },
  { value: "image", label: "newApp.modes.image" },
];

/** The rest, behind one button: each is a different idea, and four of them on the first screen read as eight choices. */
const MORE_MODES: readonly { value: SourceMode; label: PlainKey; icon: ReactNode }[] = [
  { value: "compose", label: "newApp.modes.compose", icon: <Layers /> },
  { value: "recipe", label: "newApp.modes.recipe", icon: <LibraryBig /> },
  { value: "import", label: "newApp.modes.import", icon: <FileUp /> },
  { value: "adopt", label: "newApp.modes.adopt", icon: <Link2 /> },
];

export interface SourceStepProps {
  form: SourceForm;
  errors: SourceErrors;
  onChange: (form: SourceForm) => void;
  /** Enter in a field: the same as the wizard's Continue. */
  onSubmit: () => void;
  /** The inspection in flight, and when it started. */
  inspecting: { since: number } | null;
  onCancel: () => void;
  /** Why the last inspection failed, verbatim. */
  failure: unknown;
  /** Go on without detection: the operator chooses the type. */
  onManual: () => void;
  /** Read the source again, as it is: after the fix its failure asked for. */
  onRetry: () => void;
  /** This server's GitHub integration; null while unknown or when it could not be read. */
  github: GitHubStatus | null;
  /** This server's GitLab connections; null while unknown or when they could not be read. */
  gitlab: GitLabStatus | null;
  /** Where the source comes from: a repository the GitHub App reaches, or typed. */
  mode: SourceMode;
  onModeChange: (mode: SourceMode) => void;
  /**
   * The typed source is complete: it was pasted, or the field was left. The wizard reads it then,
   * so what it found is on screen before Continue is pressed.
   */
  onSettle?: () => void;
  /** What was found in the source, said in a line under its fields, with the way to change it. */
  detected?: ReactNode;
  /** The image of a container application that is pulled from a registry, and what is wrong with it. */
  image: { value: string; error: string | undefined; onChange: (value: string) => void };
  /** What stands in the step's place when the application starts from a recipe. */
  recipes: ReactNode;
  /** What stands in the step's place when the application is imported from an export. */
  importer: ReactNode;
  /** What stands in the step's place when a Compose document is typed in. */
  composer: ReactNode;
  /** What stands in the step's place when a stack that already runs is adopted. */
  adopter: ReactNode;
}

/**
 * Step one: where the code is. Noust fetches it into a throwaway checkout and reads it, so the
 * next steps propose real commands, a real port and the variables the project declares. Or a
 * recipe, or another server's export. The wizard's own bar has Continue; Enter does the same.
 */
export function SourceStep({
  form,
  errors,
  onChange,
  onSubmit,
  inspecting,
  onCancel,
  failure,
  onManual,
  onRetry,
  github,
  gitlab,
  mode,
  onModeChange,
  onSettle,
  detected,
  image,
  recipes,
  importer,
  composer,
  adopter,
}: SourceStepProps) {
  const t = useT();
  const [workspaces, setWorkspaces] = useState(false);
  const kind = sourceKind(form.source);
  const local = kind === "local" || kind === "upload";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    onSubmit();
  };
  const busy = inspecting !== null;
  const shown = form.source.trim();
  // The answer to what was typed or chosen: it reads under that, before the other ways of giving the code.
  const reading =
    inspecting !== null ? (
      <Inspecting source={shown} branch={form.branch.trim()} since={inspecting.since} onCancel={onCancel} />
    ) : failure !== null && failure !== undefined ? (
      <InspectFailure failure={failure} source={shown} onManual={onManual} onRetry={onRetry} />
    ) : (
      (detected ?? null)
    );
  // "From GitHub" is offered wherever the server can say whether it has an App: with one it lists
  // the repositories, without one it is where GitHub is connected. "From GitLab" only where at
  // least one connection exists.
  const modes: SegmentedOption<SourceMode>[] = MODES.filter(
    (option) => (option.value !== "github" || github !== null) && (option.value !== "gitlab" || (gitlab?.connections.length ?? 0) > 0),
  ).map((option) => ({
    value: option.value,
    label: t(option.label),
  }));
  // The way chosen is one of the rest: the button that holds them says which, instead of a row
  // with nothing checked.
  const chosenMore = MORE_MODES.find((option) => option.value === mode);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        {/* When the way chosen is one of the rest, nothing here is checked and the button beside it says which. */}
        <SegmentedControl label={t("newApp.modes.label")} options={modes} value={mode} onValueChange={onModeChange} className="max-w-full flex-wrap" />
        <Menu
          trigger={
            <Button size="sm" variant={chosenMore === undefined ? "ghost" : "secondary"} trailingIcon={<ChevronDown aria-hidden="true" />} {...(chosenMore !== undefined ? { "aria-current": "true" as const } : {})}>
              {chosenMore === undefined ? t("newApp.modes.more") : t(chosenMore.label)}
            </Button>
          }
        >
          {MORE_MODES.map((option) => (
            <MenuItem key={option.value} icon={option.icon} onClick={() => onModeChange(option.value)}>
              {t(option.label)}
            </MenuItem>
          ))}
        </Menu>
      </div>

      {mode === "recipe" ? (
        recipes
      ) : mode === "import" ? (
        importer
      ) : mode === "adopt" ? (
        adopter
      ) : mode === "compose" ? (
        composer
      ) : mode === "image" ? (
        <form id={SOURCE_FORM} onSubmit={submit} noValidate className="flex flex-col gap-5">
          <Field label={t("containers.source.image")} error={image.error} description={t("containers.source.imageDescription")}>
            <Input
              mono
              icon={<Container />}
              value={image.value}
              onValueChange={image.onChange}
              placeholder="ghcr.io/owner/app:1.4"
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
            />
          </Field>
          <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("containers.source.privateRegistry")}</p>
          <CommandHint command={`noust create --domain example.com --image ${image.value.trim() === "" ? "ghcr.io/owner/app:1.4" : image.value.trim()}`} label={t("newApp.source.terminal")} />
        </form>
      ) : (
        <form id={SOURCE_FORM} onSubmit={submit} noValidate className="flex flex-col gap-5">
          {mode === "github" && github?.configured === true ? (
            <>
              <GitHubSource status={github} form={form} errors={errors} onChange={onChange} disabled={busy} />
              {reading}
            </>
          ) : mode === "github" && github !== null ? (
            <ConnectGitHub form={form} error={errors.source} />
          ) : mode === "gitlab" && gitlab !== null ? (
            <>
              <GitLabSource status={gitlab} form={form} errors={errors} onChange={onChange} disabled={busy} />
              {reading}
            </>
          ) : (
            <>
              <Field label={t("newApp.source.field")} error={errors.source} description={t(SOURCE_WORDS[kind])}>
                <Input
                  mono
                  icon={KIND_ICON[kind]}
                  value={form.source}
                  onValueChange={(value: string) => onChange({ source: value, branch: form.branch })}
                  // A pasted address is complete: it is read at once, and what was found is on screen
                  // before Continue is pressed. A typed one is read when the field is left. A directory
                  // on this server is read by Continue alone: reading it asks the server for more.
                  onPaste={() => onSettle?.()}
                  onBlur={() => onSettle?.()}
                  placeholder="https://github.com/you/app.git"
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  disabled={busy}
                />
              </Field>

              {reading}

              {mode === "manual" ? (
                <ArchiveUpload
                  disabled={busy}
                  onUploaded={(done) => {
                    onChange({ source: done.source, branch: "" });
                  }}
                  onCleared={() => {
                    onChange({ source: "", branch: "" });
                  }}
                />
              ) : null}
              {mode === "manual" ? (
                <PasteDockerfile
                  disabled={busy}
                  onStored={(done) => {
                    onChange({ source: done.source, branch: "" });
                  }}
                  onCleared={() => {
                    onChange({ source: "", branch: "" });
                  }}
                />
              ) : null}

              {local ? null : (
                <Field label={t("newApp.source.branch")} optional error={errors.branch} description={t("newApp.source.branchDescription")} className="sm:max-w-80">
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

          {mode === "manual" || mode === "gitlab" ? (
            <div className="flex flex-col gap-1">
              <div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setWorkspaces(true);
                  }}
                  disabled={busy}
                >
                  {t("workspaces.open")}
                </Button>
              </div>
              <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("workspaces.openHint")}</p>
            </div>
          ) : null}

          <CommandHint command={`noust create --domain example.com --source ${shown === "" ? "https://github.com/you/app.git" : shown}`} label={t("newApp.source.terminal")} />
        </form>
      )}
      {workspaces ? (
        <WorkspacesDialog
          open
          source={form.source.trim()}
          branch={form.branch.trim()}
          onClose={() => {
            setWorkspaces(false);
          }}
        />
      ) : null}
    </div>
  );
}
