/**
 * Server > Deploy on this machine (items 187 and 188): the page of a central that does not deploy on the machine it runs on, or that
 * does and wants to be checked again. It says what it means to give Noust the machine's Docker (it is being root there), the lines
 * that turn it on (in the central's compose file: the console cannot, and says so), what the machine's Docker was asked and what it
 * answered, check by check with the change that fixes each one that fails, and, when everything is right, what Noust can do here and
 * what it cannot. What it cannot do is not offered anywhere else in the console; this is where that is said.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useEffect, useRef } from "react";

import { runDeployHereCheck } from "../../../api/queries/containerHost";
import type { DeployHereCheck, DeployHereReport } from "../../../api/queries/containerHost";
import { authKeys, sessionQuery } from "../../../api/queries/auth";
import { DetailPage } from "../../../components/page/DetailPage";
import { ErrorBlock } from "../../../components/page/QueryState";
import { GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { Sections } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { buttonClassName } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { DiffLines } from "../../../components/ui/DiffLines";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { ServerText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph, StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import { useScopeOffers } from "../../../nodes/useNode";
import { isContainerHost } from "../../central/features";
import { CAN, CANNOT, GROUP_COMMAND, composeLines, composeText, stateOf } from "./deployHere";
import type { DeployHereState } from "./deployHere";

/** What each check's state is drawn as: a shape, a colour and, in the sentence the server says, a word. */
const GLYPH: Record<string, { state: Status; className: string }> = {
  ok: { state: "running", className: "text-ok" },
  fail: { state: "failed", className: "text-fail" },
  warn: { state: "warning", className: "text-warn" },
  skipped: { state: "stopped", className: "text-idle" },
};

/** One check: what is so, or what is not and the change that fixes it, in the server's own words and translated by their codes. */
function CheckRow({ check }: { check: DeployHereCheck }) {
  const glyph = GLYPH[check.status] ?? GLYPH["skipped"];
  const coded = { code: check.code, params: check.params ?? {}, text: check.text, hint: check.hint ?? null };
  return (
    <li data-check={check.key} data-status={check.status} className="flex items-start gap-3 px-5 py-3">
      <span className="flex h-5 shrink-0 items-center">{glyph !== undefined ? <StatusGlyph state={glyph.state} size={14} className={glyph.className} /> : null}</span>
      <div className="min-w-0">
        <p className="text-14 font-medium text-pretty text-fg">
          <ServerText coded={coded} text={check.text} />
        </p>
        {check.hint !== null && check.hint !== undefined && check.hint !== "" ? (
          <p className="mt-0.5 max-w-measure text-13 text-pretty text-fg-muted">
            <ServerText coded={coded} text={check.hint} part="hint" />
          </p>
        ) : null}
      </div>
    </li>
  );
}

/** The lines that turn it on, with the keys they go under in the file: the kit's one drawing of lines that are added. */
function ComposeLines() {
  const t = useT();
  return (
    <DiffLines
      label={t("containerHost.deployHere.how.linesLabel")}
      labels={{ added: t("docker.compose.added"), removed: t("docker.compose.removed") }}
      lines={composeLines().map((line) => ({ kind: line.added ? "add" : "same", text: line.text }))}
    />
  );
}

/** What Noust does and does not do on a machine that has Docker and nothing else: one sentence each, in two columns. */
function Able({ on }: { on: boolean }) {
  const t = useT();
  return (
    <SurfaceSection title={t(on ? "containerHost.deployHere.able.titleOn" : "containerHost.deployHere.able.titleOff")} icon={OBJECT_ICONS.diagnose}>
      <div className="grid min-w-0 gap-x-8 gap-y-5 md:grid-cols-2">
        <Subsection title={t("containerHost.deployHere.able.can")}>
          <ul className="m-0 flex list-none flex-col gap-2 p-0 text-13 text-fg">
            {CAN.map((item) => (
              <li key={item.text} className="flex items-start gap-2">
                <StatusGlyph state="running" size={10} className="mt-1.5 text-ok" />
                <span className="text-pretty">{t(item.text)}</span>
              </li>
            ))}
          </ul>
        </Subsection>
        <Subsection title={t("containerHost.deployHere.able.cannot")}>
          <ul className="m-0 flex list-none flex-col gap-2 p-0 text-13 text-fg">
            {CANNOT.map((item) => (
              <li key={item.text} className="flex items-start gap-2">
                <StatusGlyph state="stopped" size={10} className="mt-1.5 text-idle" />
                <span className="text-pretty">{t(item.text)}</span>
              </li>
            ))}
          </ul>
        </Subsection>
      </div>
    </SurfaceSection>
  );
}

const PILL: Record<DeployHereState, { state: Status; label: "off" | "on" | "problem" | "checking" }> = {
  off: { state: "stopped", label: "off" },
  checking: { state: "deploying", label: "checking" },
  problem: { state: "failed", label: "problem" },
  on: { state: "running", label: "on" },
};

export function DeployHerePage() {
  const t = useT();
  const queryClient = useQueryClient();
  const { data: session } = useQuery(sessionQuery());
  const told = isContainerHost(session);
  const check = useMutation<DeployHereReport>({
    mutationFn: runDeployHereCheck,
    // What the machine can do follows what the check found, so the session is read again: the bar and the column take it from there.
    onSuccess: () => queryClient.invalidateQueries({ queryKey: authKeys.session }),
  });
  // A central that was told is checked when a person who may ask opens the page, once: the check starts a container, so it is never
  // on every load, and a role that may not start one is not met with a refusal on opening a page it was offered.
  const may = useCan("docker.manage");
  const asked = useRef(false);
  useEffect(() => {
    if (told && may && !asked.current) {
      asked.current = true;
      check.mutate();
    }
  }, [told, may, check]);

  const report = check.data ?? null;
  const state = stateOf(told, report, check.isPending);
  const pill = PILL[state];
  const ready = state === "on";
  // With everything right the way on is the zone Docker has in the console, offered by the same filter as every link to a place.
  const offers = useScopeOffers();
  const toDocker = ready && offers("docker_zone") && (session?.permissions ?? []).includes("docker.read");
  const run = (
    <GuardedButton permission="docker.manage" variant={toDocker ? "secondary" : "primary"} loading={check.isPending} onClick={() => check.mutate()}>
      {t("containerHost.deployHere.run")}
    </GuardedButton>
  );

  return (
    <DetailPage
      measure="settings"
      header={{
        title: t("containerHost.deployHere.title"),
        description: t("containerHost.deployHere.description"),
        status: <StatusPill state={pill.state} label={t(`containerHost.deployHere.state.${pill.label}`)} />,
        ...(toDocker
          ? {
              secondaryActions: run,
              primaryAction: (
                <Link to="/server/docker" className={buttonClassName("primary", "md")}>
                  {t("containerHost.deployHere.goToDocker")}
                </Link>
              ),
            }
          : { primaryAction: run }),
      }}
    >
      <Sections>
        <ReadOnlyLine permission="docker.manage" />
        {state === "problem" ? (
          <Notice variant="banner" tone="error" title={t("containerHost.deployHere.problem.title")}>
            {t("containerHost.deployHere.problem.body")}
          </Notice>
        ) : null}

        {told ? null : (
          <>
            <Notice tone="warning" title={t("containerHost.deployHere.warning.title")}>
              {t("containerHost.deployHere.warning.body")}
            </Notice>
            <SurfaceSection title={t("containerHost.deployHere.changes.title")} icon={OBJECT_ICONS.passkey}>
              <ul className="m-0 flex list-disc flex-col gap-2 pl-5 text-14 text-pretty text-fg">
                <li>{t("containerHost.deployHere.changes.unprivileged")}</li>
                <li>{t.rich("containerHost.deployHere.changes.notFromHere", { file: <Mono key="file">compose.yaml</Mono> })}</li>
                <li>{t("containerHost.deployHere.changes.turnOff")}</li>
              </ul>
            </SurfaceSection>
            <SurfaceSection
              title={t("containerHost.deployHere.how.title")}
              icon={OBJECT_ICONS.container}
              description={t("containerHost.deployHere.how.description")}
              actions={<CopyButton value={composeText()} label={t("containerHost.deployHere.how.copy")} />}
            >
              <div className="flex flex-col gap-4">
                <ol className="m-0 flex list-decimal flex-col gap-1.5 pl-5 text-14 text-pretty text-fg">
                  <li>{t.rich("containerHost.deployHere.how.addLines", { file: <Mono key="file">compose.yaml</Mono> })}</li>
                  <li>{t("containerHost.deployHere.how.restart")}</li>
                  <li>{t("containerHost.deployHere.how.comeBack")}</li>
                </ol>
                <ComposeLines />
                <p className="max-w-measure text-12 text-pretty text-fg-muted">{t.rich("containerHost.deployHere.how.note", { command: <Mono key="command">{GROUP_COMMAND}</Mono>, volumes: <Mono key="volumes">volumes:</Mono>, environment: <Mono key="environment">environment:</Mono> })}</p>
              </div>
            </SurfaceSection>
          </>
        )}

        {check.isError ? (
          <ErrorBlock error={check.error} title={t("containerHost.deployHere.checked.loadFailed")} onRetry={() => check.mutate()} retrying={check.isPending} />
        ) : null}

        {report !== null || check.isPending ? (
          <SurfaceSection title={t("containerHost.deployHere.checked.title")} icon={OBJECT_ICONS.diagnose} description={t(check.isPending ? "containerHost.deployHere.checked.checking" : "containerHost.deployHere.checked.description")} flush loading={check.isPending && report === null}>
            {report !== null ? (
              <ul className="m-0 flex list-none flex-col divide-y divide-border p-0">
                {report.checks.map((item) => (
                  <CheckRow key={item.key} check={item} />
                ))}
              </ul>
            ) : (
              <div className="px-5 py-4">
                <Skeleton className="h-5 w-2/3" />
              </div>
            )}
          </SurfaceSection>
        ) : null}

        {ready || !told ? <Able on={ready} /> : null}
      </Sections>
    </DetailPage>
  );
}
