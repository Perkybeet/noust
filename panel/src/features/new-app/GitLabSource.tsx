import { useQuery } from "@tanstack/react-query";
import { FolderGit2, GitBranch, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { gitlabBranchesQuery, gitlabGroupsQuery, gitlabProjectsQuery } from "../../api/queries/gitlab";
import type { GitLabProject, GitLabStatus } from "../../api/queries/gitlab";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Select } from "../../components/ui/Select";
import type { SelectOption } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { ICONS } from "../../components/ui/icons";
import type { SourceErrors, SourceForm } from "./wizard";

/** How long typing pauses before GitLab is asked, so a name is not searched letter by letter. */
const SEARCH_DELAY_MS = 300;

/** The group filter's "no filter": a Select has no empty value. */
const ALL_GROUPS = "*";

function ProjectList({ projects, onChoose, disabled }: { projects: readonly GitLabProject[]; onChoose: (project: GitLabProject) => void; disabled: boolean }) {
  const t = useT();
  return (
    <Card padding="none" className="overflow-hidden">
      <ul aria-label={t("gitlab.picker.projects")} className="max-h-72 min-h-0 overflow-y-auto p-1 scroll-thin">
        {projects.map((project) => (
          <li key={project.id}>
            <button
              type="button"
              disabled={disabled || !project.clone_url}
              onClick={() => {
                onChoose(project);
              }}
              className="flex min-h-9 w-full cursor-pointer items-center gap-2.5 rounded-control px-2 py-1 text-left text-13 text-fg select-none hover:bg-surface-active focus-visible:bg-surface-active disabled:cursor-not-allowed disabled:opacity-60"
            >
              <FolderGit2 aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
              <span className="min-w-0 flex-1 truncate">
                <Mono truncate>{project.path ?? String(project.id)}</Mono>
              </span>
              {project.default_branch ? (
                <span className="hidden shrink-0 items-center gap-1 text-12 text-fg-muted sm:flex">
                  <GitBranch aria-hidden="true" className="size-icon-xs" />
                  <Mono tone="muted">{project.default_branch}</Mono>
                </span>
              ) : null}
            </button>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** The chosen project's branch: GitLab's list, the default one first chosen; typed when the list cannot be had. */
function BranchField({
  connection,
  project,
  form,
  error,
  onChange,
  disabled,
}: {
  connection: string;
  project: string | null;
  form: SourceForm;
  error: string | undefined;
  onChange: (form: SourceForm) => void;
  disabled: boolean;
}) {
  const t = useT();
  const branches = useQuery({ ...gitlabBranchesQuery(connection, project ?? ""), enabled: project !== null });

  if (project === null || branches.isError) {
    // The list is a convenience: the branch can still be typed, and the inspection checks it.
    return (
      <div className="flex min-w-0 flex-col gap-3">
        {branches.isError ? (
          <ErrorBlock compact error={branches.error} title={t("gitlab.picker.branchesFailed")} onRetry={() => void branches.refetch()} retrying={branches.isFetching} />
        ) : null}
        <Field label={t("gitlab.picker.branch")} error={error} className="sm:max-w-80">
          <Input
            mono
            icon={<GitBranch />}
            value={form.branch}
            onValueChange={(value: string) => {
              onChange({ ...form, branch: value });
            }}
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
      ? listed.map((branch) => ({ value: branch.name, label: branch.name, ...(branch.default ? { hint: t("gitlab.picker.defaultBranch", { name: branch.name }) } : {}) }))
      : form.branch !== ""
        ? [{ value: form.branch, label: form.branch }]
        : [];
  return (
    <Field label={t("gitlab.picker.branch")} nativeLabel={false} error={error} description={branches.isPending ? t("gitlab.picker.loadingBranches") : undefined} className="sm:max-w-80">
      <Select
        mono
        options={options}
        value={form.branch === "" ? null : form.branch}
        onValueChange={(branch) => {
          onChange({ ...form, branch });
        }}
        disabled={disabled || (branches.isPending && options.length === 0)}
        className="w-full"
      />
    </Field>
  );
}

export interface GitLabSourceProps {
  status: GitLabStatus;
  form: SourceForm;
  errors: SourceErrors;
  onChange: (form: SourceForm) => void;
  disabled: boolean;
}

/**
 * The source as a project a GitLab connection can read: choose the connection, narrow by group
 * or by name, choose the project, then its branch. The choice becomes the project's https
 * clone address; a private project is cloned with the connection's own token.
 */
export function GitLabSource({ status, form, errors, onChange, disabled }: GitLabSourceProps) {
  const t = useT();
  const [connection, setConnection] = useState(status.connections[0]?.name ?? "");
  const [group, setGroup] = useState(ALL_GROUPS);
  const [typed, setTyped] = useState("");
  const [search, setSearch] = useState("");
  const [chosen, setChosen] = useState<GitLabProject | null>(null);
  const [changing, setChanging] = useState(false);
  const change = useRef<HTMLButtonElement>(null);
  const justChose = useRef(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setSearch(typed.trim());
    }, SEARCH_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [typed]);

  const groups = useQuery({ ...gitlabGroupsQuery(connection), enabled: connection !== "" });
  const projects = useQuery({ ...gitlabProjectsQuery(connection, group === ALL_GROUPS ? "" : group, search), enabled: connection !== "" });

  const showingChoice = form.source !== "" && !changing;
  useEffect(() => {
    // Choosing unmounts the list; focus lands on what now stands in its place.
    if (showingChoice && justChose.current) {
      justChose.current = false;
      change.current?.focus();
    }
  }, [showingChoice]);

  if (status.connections.length === 0) {
    return (
      <Card padding="sm">
        <div className="flex min-w-0 flex-col gap-3">
          <p className="text-13 text-pretty text-fg">{t("gitlab.picker.notConnected")}</p>
          <div>
            <TextLink to="/settings/integrations" size="ui">
              {t("gitlab.picker.connect")}
            </TextLink>
          </div>
        </div>
      </Card>
    );
  }

  const choose = (project: GitLabProject): void => {
    justChose.current = true;
    setChanging(false);
    setChosen(project);
    const branch = project.default_branch ?? "";
    onChange({ source: project.clone_url ?? "", branch });
    announce(t("gitlab.picker.chosen", { path: project.path ?? String(project.id), branch: branch === "" ? "-" : branch }));
  };

  const connectionOptions: SelectOption[] = status.connections.map((item) => ({ value: item.name, label: `${item.name} (${item.host})` }));
  const groupOptions: SelectOption[] = [{ value: ALL_GROUPS, label: t("gitlab.picker.allGroups") }, ...(groups.data?.items ?? []).map((item) => ({ value: item.full_path, label: item.full_path }))];

  if (showingChoice) {
    return (
      <div className="flex min-w-0 flex-col gap-6">
        <div className="flex min-w-0 flex-col gap-1.5">
          <span className="text-13 font-medium text-fg">{t("gitlab.picker.project")}</span>
          <Card padding="none">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2">
              <FolderGit2 aria-hidden="true" className="size-icon-md shrink-0 text-fg-muted" />
              <span className="min-w-0 flex-1 truncate">
                <Mono truncate title={form.source}>
                  {chosen?.path ?? form.source}
                </Mono>
              </span>
              <Button
                ref={change}
                size="sm"
                variant="ghost"
                onClick={() => {
                  setChanging(true);
                }}
                disabled={disabled}
              >
                {t("gitlab.picker.change")}
              </Button>
            </div>
          </Card>
          {errors.source !== undefined ? (
            <p role="alert" className="flex items-start gap-1.5 text-13 text-fail">
              <ICONS.error aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0" />
              <span>{errors.source}</span>
            </p>
          ) : null}
        </div>
        <BranchField connection={connection} project={chosen === null ? null : String(chosen.id)} form={form} error={errors.branch} onChange={onChange} disabled={disabled} />
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap gap-4">
        {status.connections.length > 1 ? (
          <Field label={t("gitlab.picker.connection")} nativeLabel={false} className="sm:w-72">
            <Select
              options={connectionOptions}
              value={connection}
              onValueChange={(next) => {
                setConnection(next);
                setGroup(ALL_GROUPS);
              }}
              disabled={disabled}
              className="w-full"
            />
          </Field>
        ) : null}
        {groups.data !== undefined && groups.data.items.length > 0 ? (
          <Field label={t("gitlab.picker.group")} nativeLabel={false} className="sm:w-72">
            <Select options={groupOptions} value={group} onValueChange={setGroup} disabled={disabled} mono className="w-full" />
          </Field>
        ) : null}
        <Field label={t("gitlab.picker.search")} error={errors.source} className="sm:w-72">
          <Input
            icon={<Search />}
            value={typed}
            onValueChange={setTyped}
            placeholder={t("gitlab.picker.searchPlaceholder")}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={disabled}
          />
        </Field>
      </div>
      {projects.isError ? (
        <ErrorBlock error={projects.error} title={t("gitlab.picker.projectsFailed")} onRetry={() => void projects.refetch()} retrying={projects.isFetching} />
      ) : projects.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">{t("gitlab.picker.loadingProjects")}</span>
          <Skeleton className="h-40" />
        </div>
      ) : projects.data.items.length === 0 ? (
        <div role="status">
          <EmptyState variant="inline" title={t("gitlab.picker.noProjects")} />
        </div>
      ) : (
        <ProjectList projects={projects.data.items} onChoose={choose} disabled={disabled} />
      )}
    </div>
  );
}
