import { FileUp, FolderGit2, Link2, Search, Upload, X } from "lucide-react";
import { useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import type { GitHubStatus } from "../../api/queries/github";
import type { GitLabStatus } from "../../api/queries/gitlab";
import { isApiError } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { JobProgress } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { ServerText } from "../../components/ui/ServerText";
import type { CodedText } from "../../components/ui/ServerText";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { PlainKey } from "../../i18n";
import { codedOf } from "../../lib/serverSentence";
import { ArchiveUpload } from "../app/upload/ArchiveUpload";
import { PasteDockerfile } from "../app/upload/PasteDockerfile";
import { ConnectGitHub } from "./ConnectGitHub";
import { GitHubSource } from "./GitHubSource";
import { GitLabSource } from "./GitLabSource";
import { SshKeyHelp } from "./SshKeyHelp";
import { sshRepository } from "./sshSource";
import { Suggestion } from "./Suggestion";
import { WorkspacesDialog } from "./WorkspacesDialog";
import { SOURCE_WORDS, classifyEntry, sourceKind, withScheme } from "./wizard";
import type { SourceErrors, SourceForm, SourceKind } from "./wizard";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

const KIND_ICON: Record<SourceKind, ReactNode> = {
  github: <FolderGit2 />,
  git: <OBJECT_ICONS.source />,
  archive: <OBJECT_ICONS.packages />,
  local: <OBJECT_ICONS.file />,
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
            {t("common.words.cancel")}
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
function VerdictFailure({ failure, source, onManual }: { failure: { detail: string; hint: string | null; output: string | null; coded: CodedText | null }; source: string; onManual: () => void }) {
  const t = useT();
  return (
    <Notice
      tone="warning"
      live
      title={t("newApp.source.cannotDeploy", { source })}
      action={<Button size="sm" onClick={onManual}>{t("newApp.source.chooseType")}</Button>}
    >
      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-fg">
          <ServerText coded={failure.coded} text={failure.detail} />
        </p>
        {failure.hint !== null ? <Suggestion text={failure.hint} coded={failure.coded} part="hint" /> : null}
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
    return <VerdictFailure failure={{ detail: failure.detail, hint: failure.hint, output: failure.output, coded: codedOf(failure) }} source={source} onManual={onManual} />;
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

/** The ways of starting that are not a thing to paste or search for, behind one button: each is a different idea. */
const OTHER_WAYS: readonly { value: SourceMode; label: PlainKey; icon: ReactNode }[] = [
  { value: "compose", label: "newApp.modes.compose", icon: <OBJECT_ICONS.compose /> },
  { value: "import", label: "newApp.modes.import", icon: <FileUp /> },
  { value: "adopt", label: "newApp.modes.adopt", icon: <Link2 /> },
];

export interface SourceStepProps {
  form: SourceForm;
  errors: SourceErrors;
  onChange: (form: SourceForm) => void;
  /** Enter in a field: the same as the wizard's Continue. */
  onSubmit: () => void;
  /**
   * The inspection in flight, when it started, and whether Continue asked for it. Only that one holds the step: a read
   * the step started by itself (a repository chosen, an address pasted or left) leaves every field editable, because
   * a convenience that takes the keys of whoever goes on typing is a defect, not a loading state.
   */
  inspecting: { since: number; advancing: boolean } | null;
  onCancel: () => void;
  /** Why the last inspection failed, verbatim. */
  failure: unknown;
  /** Go on without detection: the operator chooses the type. */
  onManual: () => void;
  /** Read the source again, as it is: after the fix its failure asked for. */
  onRetry: () => void;
  /** This server's GitHub integration; null while unknown or when it could not be read. */
  github: GitHubStatus | null;
  /** GitHub's answer has not come yet: what stands under the repositories waits for it. */
  githubReading?: boolean;
  /** This server's GitLab connections; null while unknown or when they could not be read. */
  gitlab: GitLabStatus | null;
  /** Where the source comes from: a repository the GitHub App reaches, or typed. */
  mode: SourceMode;
  /** Goes to another way of starting. `keep` is the source the change brings with it (a link typed in the box), which the change must not clear. */
  onModeChange: (mode: SourceMode, keep?: SourceForm) => void;
  /**
   * The typed source is complete: it was pasted, or the field was left. The wizard reads it then,
   * so what it found is on screen before Continue is pressed.
   */
  onSettle?: () => void;
  /** What was found in the source, said in a line under its fields, with the way to change it. */
  detected?: ReactNode;
  /** The GitHub App is the central's and serves every server of the fleet: its repositories are listed from there. */
  centralGithub?: boolean;
  /** The image of a container application that is pulled from a registry, and what is wrong with it. */
  image: { value: string; error: string | undefined; onChange: (value: string) => void };
  /**
   * What stands under the box for the words typed in it (the catalog), or for nothing typed (the popular recipes,
   * or the whole catalog when the step was opened on recipes: `browse`).
   */
  recipes: (query: string, browse: boolean) => ReactNode;
  /** What stands in the step's place when the application is imported from an export. */
  importer: ReactNode;
  /** What stands in the step's place when a Compose document is typed in. */
  composer: ReactNode;
  /** What stands in the step's place when a stack that already runs is adopted. */
  adopter: ReactNode;
}

/**
 * Step one: what to deploy, in one box (owner item 99). A repository, a folder or an archive is pasted, an
 * image of a registry is pasted, and the name of what to install is searched in the catalog: Noust tells which
 * from what was typed (`classifyEntry`), so nobody has to know which of four doors their thing is behind. Under
 * the box, when nothing is typed, the repositories of the connected GitHub and the popular recipes; the rest of
 * the ways (a Compose file, an export, a stack that already runs, files to upload) are folded. How a repository
 * is reached (the GitHub App, a deploy key, a token) is Noust's to decide and is said only when it fails or
 * when a step of the operator's is needed. The wizard's own bar has Continue; Enter does the same.
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
  githubReading = false,
  centralGithub = false,
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
  // What the box shows: what was typed, or the source a repository list or an upload gave. A recipe's name is not kept.
  const [text, setText] = useState(() => (mode === "image" ? image.value : mode === "manual" ? form.source : ""));
  const kind = sourceKind(form.source);
  const local = kind === "local" || kind === "upload";
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    onSubmit();
  };
  // What disables the step's controls is the read Continue is waiting for, never one that only shows what was found
  // before Continue is pressed: that read is under way from 80ms after a choice, in the middle of the next keystrokes,
  // and a disabled field drops the key pressed on it and loses the focus it had.
  const busy = inspecting?.advancing === true;
  const shown = form.source.trim();
  const startMode: SourceMode = github?.configured === true ? "github" : "manual";
  // The answer to what was typed or chosen: it reads under that, before the other ways of giving the code.
  const reading =
    inspecting !== null ? (
      <Inspecting source={shown} branch={form.branch.trim()} since={inspecting.since} onCancel={onCancel} />
    ) : failure !== null && failure !== undefined ? (
      <InspectFailure failure={failure} source={shown} onManual={onManual} onRetry={onRetry} />
    ) : (
      (detected ?? null)
    );

  // A way that is not the box: its own screen, with the way back to the box.
  const apart = mode === "compose" || mode === "import" || mode === "adopt" || mode === "gitlab" || (mode === "github" && github !== null && !github.configured);
  const back = (
    <div>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          setText("");
          onModeChange(startMode);
        }}
      >
        {t("common.words.back")}
      </Button>
    </div>
  );
  if (apart) {
    return (
      <div className="flex flex-col gap-6">
        {back}
        {mode === "compose" ? (
          composer
        ) : mode === "import" ? (
          importer
        ) : mode === "adopt" ? (
          adopter
        ) : mode === "gitlab" && gitlab !== null ? (
          <form id={SOURCE_FORM} onSubmit={submit} noValidate className="flex flex-col gap-5">
            <GitLabSource status={gitlab} form={form} errors={errors} onChange={onChange} disabled={busy} />
            {reading}
          </form>
        ) : (
          <form id={SOURCE_FORM} onSubmit={submit} noValidate className="flex flex-col gap-5">
            <ConnectGitHub form={form} error={errors.source} />
          </form>
        )}
      </div>
    );
  }

  const entry = classifyEntry(text);
  // The beginning of an address keeps the page as it was before the first letter: typed letter by letter, an address
  // would otherwise show the catalog for `h` and take it away at `https://g`, and the page would jump twice.
  const view = entry === "start" ? "empty" : entry;
  // With the repositories listed under an empty box, a missing choice is said on the list it is chosen from.
  const pickerOwnsError = view === "empty" && mode === "github" && github?.configured === true;
  /** What was typed or pasted decides where it goes: a source, an image, or words for the catalog. */
  const typed = (next: string): void => {
    setText(next);
    const found = classifyEntry(next);
    if (found === "image") {
      if (mode !== "image") onModeChange("image");
      image.onChange(next.trim());
    } else if (found === "source") {
      // A branch chosen with a repository from a list is not the branch of what is typed over it: only the
      // branch of an address already typed here is kept while it is edited.
      const typedSource = { source: withScheme(next), branch: mode === "manual" ? form.branch : "" };
      if (mode !== "manual") onModeChange("manual", typedSource);
      else onChange(typedSource);
    } else if (found === "search") {
      if (mode !== "recipe") onModeChange("recipe");
    } else if (mode === "image" || mode === "manual" || mode === "recipe") {
      // Emptied: back to where the step starts, with nothing left over from what was typed.
      onModeChange(startMode);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <form id={SOURCE_FORM} onSubmit={submit} noValidate className="flex flex-col gap-5">
        <Field label={t("newApp.box.label")} error={mode === "image" ? image.error : pickerOwnsError ? undefined : errors.source} description={t("newApp.box.description")}>
          <Input
            icon={entry === "image" ? <OBJECT_ICONS.container /> : entry === "source" ? KIND_ICON[kind] : <Search />}
            value={text}
            onValueChange={typed}
            // A pasted address is complete: it is read at once, and what was found is on screen before
            // Continue is pressed. A typed one is read when the field is left. A directory on this server is
            // read by Continue alone: reading it asks the server for more.
            onPaste={() => onSettle?.()}
            onBlur={() => onSettle?.()}
            placeholder={t("newApp.box.placeholder")}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={busy}
          />
        </Field>

        {entry === "source" ? (
          <>
            <p className="max-w-measure text-13 text-pretty text-fg-muted">{t(SOURCE_WORDS[kind])}</p>
            {reading}
            {local ? null : (
              <Field label={t("newApp.source.branch")} optional error={errors.branch} description={t("newApp.source.branchDescription")} className="sm:max-w-80">
                <Input
                  mono
                  icon={<OBJECT_ICONS.source />}
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
        ) : null}

        {entry === "image" ? <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("containers.source.privateRegistry")}</p> : null}
      </form>

      {view === "empty" && mode === "github" && github?.configured === true ? (
        <form id={`${SOURCE_FORM}-repositories`} onSubmit={submit} noValidate className="flex flex-col gap-5">
          <GitHubSource status={github} form={form} errors={errors} onChange={onChange} disabled={busy} central={centralGithub} />
          {reading}
        </form>
      ) : null}

      {/* Under the repositories, which are drawn once GitHub has said whether it is connected: what is below them
          is not drawn before, so it is not moved down when they arrive. */}
      <div hidden={githubReading} className="flex flex-col gap-6">
      {view === "empty" || view === "search" ? recipes(view === "search" ? text : "", mode === "recipe" && view === "empty") : null}

      <Disclosure label={t("newApp.box.otherWays")}>
        <div className="flex flex-col gap-5">
          <ul aria-label={t("newApp.box.otherWays")} className="flex flex-wrap gap-2">
            {OTHER_WAYS.map((way) => (
              <li key={way.value}>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={way.icon}
                  onClick={() => {
                    setText("");
                    onModeChange(way.value);
                  }}
                >
                  {t(way.label)}
                </Button>
              </li>
            ))}
            {(gitlab?.connections.length ?? 0) > 0 ? (
              <li>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<OBJECT_ICONS.source />}
                  onClick={() => {
                    setText("");
                    onModeChange("gitlab");
                  }}
                >
                  {t("gitlab.picker.mode")}
                </Button>
              </li>
            ) : null}
            {github !== null && !github.configured ? (
              <li>
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<FolderGit2 aria-hidden="true" />}
                  onClick={() => {
                    setText("");
                    onModeChange("github");
                  }}
                >
                  {t("newApp.box.connectGithub")}
                </Button>
              </li>
            ) : null}
          </ul>
          <ArchiveUpload
            disabled={busy}
            onUploaded={(done) => {
              setText(done.source);
              onModeChange("manual");
              onChange({ source: done.source, branch: "" });
            }}
            onCleared={() => {
              setText("");
              onChange({ source: "", branch: "" });
            }}
          />
          <PasteDockerfile
            disabled={busy}
            onStored={(done) => {
              setText(done.source);
              onModeChange("manual");
              onChange({ source: done.source, branch: "" });
            }}
            onCleared={() => {
              setText("");
              onChange({ source: "", branch: "" });
            }}
          />
          <div className="flex flex-col gap-1">
            <div>
              <Button
                size="sm"
                variant="secondary"
                icon={<OBJECT_ICONS.app aria-hidden="true" />}
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
        </div>
      </Disclosure>
      </div>

      <CommandHint
        command={
          entry === "image"
            ? `noust create --domain example.com --image ${text.trim()}`
            : `noust create --domain example.com --source ${shown === "" ? "https://github.com/you/app.git" : shown}`
        }
        label={t("common.words.fromTerminal")}
      />
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
