import { useMutation, useQuery } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { isJobFinished, jobLogQuery, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { JobProgress } from "../../components/page/JobProgress";
import type { JobState } from "../../components/page/JobProgress";
import { Button } from "../../components/ui/Button";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { LogViewer } from "../../components/ui/LogViewer";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { commandLine, parseCommand, shellSyntaxIn } from "../../lib/terminal/command";
import { reportActionError } from "../apps/useAppActions";
import { jobStep } from "../app/useAppJob";
import { outputLines } from "./output";
import { terminalStatusQuery, useHasPermission } from "./queries";

/** How often the output of a command that is still running is read again. */
const OUTPUT_POLL_MS = 1500;

function stateOf(job: Job): JobState {
  switch (job.status) {
    case "pending":
      return "queued";
    case "running":
      return "running";
    case "completed":
      return "succeeded";
    case "cancelled":
      return "cancelled";
    default:
      return "failed";
  }
}

function exitCodeOf(job: Job): number | null {
  const code = job.result?.["exit_code"];
  return typeof code === "number" ? code : null;
}

/**
 * Whole seconds from what was typed: `null` when it is empty (the server's default is used) and
 * `NaN` when it is not a number in range, so the field can say so.
 */
function parseSeconds(text: string, max: number): number | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  const value = /^\d+$/.test(trimmed) ? Number(trimmed) : Number.NaN;
  return value >= 1 && value <= max ? value : Number.NaN;
}

/**
 * Run one command in an application, once.
 *
 * The line is split into the program and its arguments here, shown as it will be given (no shell
 * ever sees it, which a `|` or a `$VAR` would be told about), and sent as that list. The server
 * runs it as the application's own account in its sandbox, as a job whose log is the output,
 * which is read here while it runs and once more when it ends.
 */
export function ExecPanel({ domain }: { domain: string }) {
  const t = useT();
  const status = useQuery(terminalStatusQuery());
  const permitted = useHasPermission("apps.terminal");
  const followed = useFollowedJob();
  const [line, setLine] = useState("");
  const [service, setService] = useState("");
  const [seconds, setSeconds] = useState("");

  const parsed = useMemo(() => parseCommand(line), [line]);
  const syntax = useMemo(() => shellSyntaxIn(parsed.argv), [parsed.argv]);
  const max = status.data?.exec_max_seconds ?? 1800;
  const timeout = parseSeconds(seconds, max);
  const timeoutInvalid = Number.isNaN(timeout);

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const busy = job !== null && !finished;

  const run = useMutation({
    mutationFn: () =>
      request("post", "/api/apps/{domain}/exec", {
        params: { domain },
        body: {
          argv: parsed.argv,
          ...(service.trim() !== "" ? { service: service.trim() } : {}),
          ...(timeout !== null && !Number.isNaN(timeout) ? { timeout } : {}),
        },
      }),
    onSuccess: (accepted) => {
      followed.follow(accepted.job_id);
    },
    onError: (error) => {
      reportActionError(t("terminal.exec.cannotQueue"), error);
    },
  });

  const log = useQuery({
    ...jobLogQuery(followed.id ?? "", 5000),
    enabled: followed.id !== null,
    refetchInterval: finished ? false : OUTPUT_POLL_MS,
  });
  // The last lines are read once more when the command ends: the poll stops with it.
  const refetchLog = log.refetch;
  useEffect(() => {
    if (finished) void refetchLog();
  }, [finished, refetchLog]);
  const lines = useMemo(() => outputLines(log.data?.content ?? ""), [log.data]);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (parsed.argv.length === 0 || parsed.unclosed !== null || timeoutInvalid || busy || run.isPending) return;
    run.mutate();
  };

  if (permitted === false) {
    return <Notice tone="warning">{t("terminal.start.permission", { permission: "apps.terminal" })}</Notice>;
  }

  const code = job !== null ? exitCodeOf(job) : null;
  const ready = parsed.argv.length > 0 && parsed.unclosed === null && !timeoutInvalid;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <form onSubmit={submit} className="flex max-w-3xl flex-col gap-4">
        <Field
          label={t("terminal.exec.commandLabel")}
          description={t("terminal.exec.commandHelp")}
          error={parsed.unclosed !== null ? t("terminal.exec.unclosed") : undefined}
        >
          <Input
            mono
            autoComplete="off"
            spellCheck={false}
            placeholder={t("terminal.exec.commandPlaceholder")}
            value={line}
            onChange={(event) => {
              setLine(event.currentTarget.value);
            }}
          />
        </Field>
        <div className="flex min-w-0 flex-col gap-2" aria-live="polite">
          {parsed.argv.length > 0 ? (
            <>
              <span className="text-12 text-fg-faint">{t("terminal.exec.argvLabel")}</span>
              <ol aria-label={t("terminal.exec.argvLabel")} className="flex min-w-0 flex-wrap gap-1.5">
                {parsed.argv.map((argument, index) => (
                  <li key={index} className="max-w-full min-w-0 rounded-chip border border-border bg-bg-sunken px-1.5 py-0.5">
                    <Mono truncate>{argument === "" ? '""' : argument}</Mono>
                  </li>
                ))}
              </ol>
              {syntax.length > 0 ? <p className="text-12 text-fg-muted">{t("terminal.exec.shellSyntax", { words: syntax.join(" ") })}</p> : null}
            </>
          ) : (
            <span className="text-12 text-fg-faint">{t("terminal.exec.previewEmpty")}</span>
          )}
        </div>
        <Disclosure label={t("terminal.exec.optionsLabel")}>
          <div className="grid max-w-xl gap-4 sm:grid-cols-2">
            <Field label={t("terminal.exec.serviceLabel")} description={t("terminal.exec.serviceHelp")} optional>
              <Input
                mono
                value={service}
                onChange={(event) => {
                  setService(event.currentTarget.value);
                }}
              />
            </Field>
            <Field
              label={t("terminal.exec.timeoutLabel")}
              description={t("terminal.exec.timeoutHelp")}
              error={timeoutInvalid ? t("terminal.exec.timeoutInvalid", { max }) : undefined}
              optional
            >
              <Input
                mono
                inputMode="numeric"
                placeholder={t("terminal.exec.timeoutPlaceholder", { seconds: max })}
                value={seconds}
                onChange={(event) => {
                  setSeconds(event.currentTarget.value);
                }}
              />
            </Field>
          </div>
        </Disclosure>
        <div>
          <Button
            type="submit"
            variant="primary"
            icon={<Play aria-hidden="true" />}
            loading={run.isPending || busy}
            disabled={permitted !== true || !ready}
          >
            {busy ? t("terminal.exec.running") : finished ? t("terminal.exec.again") : t("terminal.exec.run")}
          </Button>
        </div>
      </form>

      {job !== null ? (
        <div className="flex min-w-0 flex-col gap-3">
          <JobProgress
            state={stateOf(job)}
            title={
              finished
                ? stateOf(job) === "succeeded"
                  ? t("terminal.exec.succeededTitle")
                  : t("terminal.exec.failedTitle", { domain })
                : t("terminal.exec.queuedTitle", { domain })
            }
            step={jobStep(job)}
            {...(job.error ? { error: { detail: job.error } } : {})}
            {...(code !== null ? { description: t("terminal.exec.exitStatus", { code }) } : {})}
            action={
              <TextLink to="/activity" size="ui">
                {t("terminal.exec.viewActivity")}
              </TextLink>
            }
            {...(finished ? { onDismiss: followed.dismiss } : {})}
          />
          <LogViewer
            lines={lines}
            follow
            height={320}
            label={t("terminal.exec.outputLabel", { domain })}
            emptyMessage={finished ? t("terminal.exec.outputEmpty") : t("terminal.exec.outputWaiting")}
            loading={log.data === undefined && !finished}
          />
        </div>
      ) : null}

      {parsed.argv.length > 0 ? <CommandHint label={t("terminal.exec.hint")} command={`noust app exec ${domain} -- ${commandLine(parsed.argv)}`} /> : null}
    </div>
  );
}
