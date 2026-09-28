import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleAlert, FolderGit2, GitBranch, Globe, Lock, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";

import { githubBranchesQuery, githubRepositoriesQuery } from "../../api/queries/github";
import type { GitHubRepository, GitHubStatus } from "../../api/queries/github";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { cx } from "../../lib/cx";
import { ExternalAnchor } from "../settings/github/ExternalAnchor";
import { splitFullName } from "../settings/github/github";
import type { SourceErrors, SourceForm } from "./wizard";

/** Repositories whose name holds what was typed, those whose name starts with it first. */
export function filterRepositories(repositories: readonly GitHubRepository[], query: string): GitHubRepository[] {
  const q = query.trim().toLowerCase();
  if (q === "") return [...repositories];
  const scored = repositories
    .map((repository, order) => {
      const full = repository.full_name.toLowerCase();
      const name = full.split("/")[1] ?? full;
      const score = name.startsWith(q) ? 3 : full.startsWith(q) ? 2 : full.includes(q) ? 1 : 0;
      return { repository, order, score };
    })
    .filter((entry) => entry.score > 0);
  scored.sort((a, b) => b.score - a.score || a.order - b.order);
  return scored.map((entry) => entry.repository);
}

/** Private or public, told by shape and word; neither is a state, so neither is coloured. */
function Visibility({ repository }: { repository: Pick<GitHubRepository, "private"> }) {
  return repository.private ? (
    <span className="flex shrink-0 items-center gap-1 text-12 text-fg-muted">
      <Lock aria-hidden="true" className="size-3" />
      Private
    </span>
  ) : (
    <span className="flex shrink-0 items-center gap-1 text-12 text-fg-muted">
      <Globe aria-hidden="true" className="size-3" />
      Public
    </span>
  );
}

function optionId(base: string, repository: GitHubRepository): string {
  return `${base}-${String(repository.installation_id)}-${repository.full_name.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

/**
 * The repositories the App reaches, searchable: a combobox over a list that is always shown.
 * Arrow keys move through the matches, Enter chooses; the pointer works on any row.
 */
function RepositoryPicker({
  repositories,
  onChoose,
  onCancel,
  error,
  disabled,
}: {
  repositories: readonly GitHubRepository[];
  onChoose: (repository: GitHubRepository) => void;
  /** Go back to the repository already chosen, when there is one. */
  onCancel: (() => void) | null;
  error: string | undefined;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const base = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const listId = `${base}-repositories`;
  const matches = useMemo(() => filterRepositories(repositories, query), [repositories, query]);
  const activeIndex = matches.length === 0 ? -1 : Math.min(active, matches.length - 1);
  const activeRepository = activeIndex >= 0 ? matches[activeIndex] : undefined;
  const activeId = activeRepository ? optionId(base, activeRepository) : undefined;
  const input = useRef<HTMLInputElement>(null);
  const reopened = useRef(onCancel !== null);

  useEffect(() => {
    // Opened by "Change repository": the operator is here to search.
    if (reopened.current) input.current?.focus();
  }, []);

  useEffect(() => {
    if (activeId === undefined) return;
    document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
  }, [activeId]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>): void => {
    if (event.key === "Escape" && onCancel !== null) {
      event.preventDefault();
      onCancel();
      return;
    }
    if (matches.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((activeIndex + 1) % matches.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((activeIndex - 1 + matches.length) % matches.length);
    } else if (event.key === "Home") {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActive(matches.length - 1);
    } else if (event.key === "Enter" && activeRepository) {
      // Enter chooses; it never submits the step's form with half a choice.
      event.preventDefault();
      onChoose(activeRepository);
    }
  };

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <Field
        label="Repository"
        error={error}
        description={`${String(repositories.length)} ${repositories.length === 1 ? "repository" : "repositories"} the App can read. Type to filter, arrow keys to move, Enter to choose.`}
      >
        <Input
          icon={<Search />}
          role="combobox"
          aria-expanded={matches.length > 0}
          aria-controls={listId}
          aria-activedescendant={activeId}
          aria-autocomplete="list"
          value={query}
          onValueChange={(value: string) => {
            setQuery(value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search repositories"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          disabled={disabled}
          ref={input}
        />
      </Field>
      <div
        id={listId}
        role="listbox"
        aria-label="Repositories"
        className={cx(
          "max-h-72 min-h-0 overflow-y-auto rounded-card border border-border bg-surface p-1 shadow-raised scroll-thin",
          matches.length === 0 && "hidden",
        )}
      >
        {matches.map((repository, position) => {
          const selected = repository === activeRepository;
          const [owner, name] = repository.full_name.split("/");
          return (
            // Keyboard selection lives on the input (aria-activedescendant); the row only
            // answers the pointer.
            // eslint-disable-next-line jsx-a11y/click-events-have-key-events
            <div
              key={`${String(repository.installation_id)}:${repository.full_name}`}
              id={optionId(base, repository)}
              role="option"
              aria-selected={selected}
              aria-disabled={disabled || undefined}
              tabIndex={-1}
              onMouseMove={() => {
                if (!selected) setActive(position);
              }}
              onMouseDown={(event) => {
                event.preventDefault();
              }}
              onClick={() => {
                if (!disabled) onChoose(repository);
              }}
              className={cx(
                "flex min-h-9 cursor-pointer items-center gap-2.5 rounded-control px-2 py-1 text-13 text-fg select-none",
                selected && "bg-surface-active",
              )}
            >
              <FolderGit2 aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
              <span translate="no" className="mono min-w-0 flex-1 truncate text-12">
                <span className="text-fg-muted">{`${owner ?? ""}/`}</span>
                {name}
              </span>
              <Visibility repository={repository} />
              {repository.default_branch ? (
                <span translate="no" className="mono hidden shrink-0 items-center gap-1 text-12 text-fg-muted sm:flex">
                  <GitBranch aria-hidden="true" className="size-3" />
                  <span className="sr-only">default branch </span>
                  {repository.default_branch}
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      {matches.length === 0 ? (
        <p role="status" className="rounded-card border border-dashed border-border px-4 py-6 text-center text-13 text-fg-muted">
          {query.trim() === "" ? "The App cannot read any repository yet." : `No repository matches "${query.trim()}".`}
        </p>
      ) : null}
      {onCancel !== null ? (
        <div>
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={disabled}>
            Keep the chosen repository
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** The chosen repository's branch: GitHub's list, the default one first chosen. */
function BranchField({
  repository,
  form,
  error,
  onChange,
  disabled,
}: {
  repository: { owner: string; repo: string; defaultBranch: string | null };
  form: SourceForm;
  error: string | undefined;
  onChange: (form: SourceForm) => void;
  disabled: boolean;
}) {
  const branches = useQuery(githubBranchesQuery(repository.owner, repository.repo));
  const description = "Pushes to this branch can redeploy it later.";

  if (branches.isError) {
    // The list is a convenience: the branch can still be typed, and the inspection checks it.
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <ErrorBlock compact error={branches.error} title="Could not list the branches" onRetry={() => void branches.refetch()} retrying={branches.isFetching} />
        <Field label="Branch" error={error} description={`${description} Type it while the list is unavailable.`} className="sm:max-w-80">
          <Input
            mono
            icon={<GitBranch />}
            value={form.branch}
            onValueChange={(value: string) => onChange({ ...form, branch: value })}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={disabled}
          />
        </Field>
      </div>
    );
  }

  const listed = branches.data?.items ?? [];
  const options: SelectOption[] =
    listed.length > 0
      ? listed.map((branch) => {
          const notes = [branch.name === repository.defaultBranch ? "Default branch" : null, branch.protected ? "Protected" : null].filter(
            (note): note is string => note !== null,
          );
          return { value: branch.name, label: branch.name, ...(notes.length > 0 ? { hint: notes.join(", ") } : {}) };
        })
      : form.branch !== ""
        ? [{ value: form.branch, label: form.branch }]
        : [];

  return (
    <Field
      label="Branch"
      nativeLabel={false}
      error={error}
      description={branches.isPending ? `${description} Loading the branches from GitHub…` : description}
      className="sm:max-w-80"
    >
      <Select
        mono
        options={options}
        value={form.branch === "" ? null : form.branch}
        placeholder={branches.isPending ? "Loading branches" : "Choose a branch"}
        onValueChange={(branch) => onChange({ ...form, branch })}
        disabled={disabled || (branches.isPending && options.length === 0)}
        className="w-full"
      />
    </Field>
  );
}

export interface GitHubSourceProps {
  status: GitHubStatus;
  form: SourceForm;
  errors: SourceErrors;
  onChange: (form: SourceForm) => void;
  disabled: boolean;
}

/**
 * The source as a repository this server's GitHub App reaches: search the repositories every
 * installation covers, choose one, then its branch. The choice becomes `github:owner/repo`
 * and carries the installation that reads it into the inspection and the deploy.
 */
export function GitHubSource({ status, form, errors, onChange, disabled }: GitHubSourceProps) {
  const installed = status.installations.length > 0;
  const repositories = useQuery({ ...githubRepositoriesQuery(), enabled: installed });
  const [changing, setChanging] = useState(false);
  const change = useRef<HTMLButtonElement>(null);
  const chose = useRef(false);

  const chosen = form.installationId !== undefined && form.source !== "" && !changing;
  const fullName = form.source.replace(/^github:/, "");
  const listed = repositories.data?.items.find((item) => item.source === form.source && item.installation_id === form.installationId);
  const names = splitFullName(fullName);

  useEffect(() => {
    // Choosing unmounts the search box; focus lands on what now stands in its place.
    if (chosen && chose.current) {
      chose.current = false;
      change.current?.focus();
    }
  }, [chosen]);

  if (!installed) {
    return (
      <div className="flex min-w-0 flex-col gap-3 rounded-card border border-border bg-surface p-4 shadow-raised">
        <p className="text-13 text-pretty text-fg">
          The GitHub App is not installed on any account yet, so it cannot read a repository.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <ExternalAnchor href={status.install_url} button="primary">
            Install on GitHub
          </ExternalAnchor>
          <Link to="/settings/integrations" className="rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
            Settings, Integrations
          </Link>
        </div>
      </div>
    );
  }

  if (repositories.data === undefined) {
    if (repositories.isError) {
      return (
        <ErrorBlock
          error={repositories.error}
          title="Could not list the repositories on GitHub"
          hint="Check that this server can reach api.github.com, and that the App is still installed."
          onRetry={() => void repositories.refetch()}
          retrying={repositories.isFetching}
        />
      );
    }
    return (
      <div aria-busy="true" className="flex flex-col gap-2">
        <span className="sr-only">Loading the repositories</span>
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-8" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  const choose = (repository: GitHubRepository): void => {
    chose.current = true;
    setChanging(false);
    const branch = repository.default_branch ?? "";
    onChange({ source: repository.source, branch, installationId: repository.installation_id });
    announce(`Chose ${repository.full_name}${branch !== "" ? `, branch ${branch}` : ""}`);
  };

  if (!chosen) {
    return (
      <div className="flex min-w-0 flex-col gap-3">
        <RepositoryPicker
          repositories={repositories.data.items}
          onChoose={choose}
          onCancel={changing && form.installationId !== undefined ? () => setChanging(false) : null}
          error={errors.source}
          disabled={disabled}
        />
        <p className="text-12 text-pretty text-fg-muted">
          {"Missing one? The App reads only the repositories its installations allow. "}
          <Link to="/settings/integrations" className="rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus">
            Manage installations
          </Link>
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1.5">
        <span className="text-13 font-medium text-fg">Repository</span>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-border bg-surface px-3 py-2 shadow-raised">
          <FolderGit2 aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
          <code translate="no" className="min-w-0 flex-1 truncate text-12 text-fg" title={fullName}>
            {fullName}
          </code>
          {listed !== undefined ? <Visibility repository={listed} /> : null}
          <Button ref={change} size="sm" variant="ghost" onClick={() => setChanging(true)} disabled={disabled}>
            Change repository
          </Button>
        </div>
        {errors.source !== undefined ? (
          <p role="alert" className="flex items-start gap-1.5 text-13 text-fail">
            <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
            <span>{errors.source}</span>
          </p>
        ) : null}
      </div>
      {names !== null ? (
        <BranchField
          repository={{ owner: names.owner, repo: names.repo, defaultBranch: listed?.default_branch ?? null }}
          form={form}
          error={errors.branch}
          onChange={onChange}
          disabled={disabled}
        />
      ) : null}
    </div>
  );
}
