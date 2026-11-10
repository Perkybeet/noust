/**
 * The one way to open a port (owner item 119: the atlas counted six places and four dialogs that ended in a firewall
 * rule). It asks what is behind the port, reads who listens on it now, and says exactly what it will do before doing it:
 * a rule of the server's firewall, and, for a program that listens only inside the machine, nginx in front of it.
 *
 * Every place that opens a port opens this dialog, with what it knows filled in (`useOpenPort().open({ port })`): the
 * published ports, the firewall's rules, a row of "Listening ports". A database is not opened here: its account and its
 * TLS are asked in its own Connect section, and the dialog leads there instead of asking them a second time.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { networkKeys, streamsQuery } from "../../api/queries/network";
import { databasesQuery } from "../../api/queries/databases";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Subsection } from "../../components/page/Subsection";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { ServerText, useServerSentence } from "../../components/ui/ServerText";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useDebounced } from "../../lib/useDebounced";
import { useServerConfirm } from "../server/errors";
import { portRows } from "../server/network/data";
import type { Holder } from "../server/network/data";
import { firewallQuery, portsQuery, serverKeys } from "../server/queries";
import type { FirewallRule } from "../server/queries";
import { useServerJob } from "../server/serverJob";
import { MissingSoftware } from "../server/software/MissingSoftware";
import { MAX_RELAY_SOURCES, OPEN_PORT_DEFAULTS, factsOf, openToEveryone, parsePort, planOf, ruleBody, streamBody } from "./openPort";
import type { OpenForm, OpenPlan, OpenProto, OpenProblems, PortFact } from "./openPort";
import { PortsLack } from "./PortsLack";
import { isProtected as isHeldOpen } from "./firewallRules";

type Purpose = "service" | "database";

/** What a place that opens the dialog knows already. */
export interface OpenPortPreset {
  port?: number;
  proto?: OpenProto;
  purpose?: Purpose;
  /** The database it is for, as `engine/name`, when the place is a database's. */
  database?: string;
}

/** What holds a port, said the way the ports table says it. */
function holderWords(t: T, holder: Holder): string {
  switch (holder.kind) {
    case "web":
      return t("serverPorts.holder.web");
    case "ssh":
      return t("serverPorts.holder.ssh");
    case "console":
      return t("serverPorts.holder.console");
    case "dns":
      return t("serverPorts.holder.dns");
    case "app":
      return `${t("serverPorts.holder.app")} ${holder.owner}`;
    case "stack":
      return `${t("serverPorts.holder.stack")} ${holder.owner}`;
    case "stream":
      return `${t("serverPorts.holder.stream")} ${holder.owner}`;
    case "database":
      return `${t("serverPorts.holder.database")} ${holder.owner}`;
    case "instance":
      return `${t("serverPorts.holder.instance")} ${holder.owner}`;
    case "process":
      return holder.owner;
  }
}

function protocolWords(t: T, proto: OpenProto): string {
  return proto === "any" ? t("network.openPort.protocolBoth") : proto.toUpperCase();
}

function problemText(t: T, problems: OpenProblems, field: "port" | "source" | "outside" | "proto"): string | null {
  switch (field) {
    case "port":
      return problems.port === undefined ? null : t("network.openPort.problem.port");
    case "source":
      switch (problems.source) {
        case undefined:
          return null;
        case "needed":
          return t("network.openPort.problem.sourceNeeded");
        case "invalid":
          return t("network.openPort.problem.sourceInvalid");
        case "one":
          return t("network.openPort.problem.sourceOne");
        case "many":
          return t("network.openPort.problem.sourceMany", { max: MAX_RELAY_SOURCES });
      }
      return null;
    case "outside":
      return problems.outside === undefined ? null : problems.outside === "same" ? t("network.openPort.problem.outsideSame") : t("network.openPort.problem.outsideInvalid");
    case "proto":
      return problems.proto === undefined ? null : t("network.openPort.problem.protocolOne");
  }
}

/** What listens on the port that was typed, said before there is a plan: a program inside the machine is the news that changes the form. */
function Listener({ fact, port }: { fact: PortFact; port: number }) {
  const t = useT();
  const said =
    fact.reach === "free" ? (
      <p>{t("network.openPort.free", { port })}</p>
    ) : fact.process !== null && fact.holder !== null ? (
      <p>{t.rich("network.openPort.listens", { port, process: <Mono key="process">{fact.process}</Mono>, holder: holderWords(t, fact.holder) })}</p>
    ) : fact.process !== null ? (
      <p>{t.rich("network.openPort.listensBare", { port, process: <Mono key="process">{fact.process}</Mono> })}</p>
    ) : fact.holder !== null ? (
      <p>{t("network.openPort.listensKnown", { port, holder: holderWords(t, fact.holder) })}</p>
    ) : null;
  return (
    <>
      {said}
      {fact.reach === "inside" ? <p>{t("network.openPort.inside")}</p> : null}
    </>
  );
}

/** What the open dialog will do, in steps, from the plan it made. */
function Steps({ plan, who, loader, removing }: { plan: OpenPlan; who: string; loader: boolean; removing: readonly FirewallRule[] }) {
  const t = useT();
  return (
    <>
      <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-fg">
        {plan.mode === "rule" ? (
          <>
            <li>{t("network.openPort.stepRule", { protocol: protocolWords(t, plan.proto), port: plan.port, who })}</li>
            {removing.map((rule) => (
              <li key={rule.id}>{t.rich("network.openPort.stepRemove", { rule: <Mono key="rule">{rule.spec}</Mono> })}</li>
            ))}
          </>
        ) : (
          <>
            <li>
              {t.rich("network.openPort.stepRelay", {
                protocol: protocolWords(t, plan.proto),
                outside: plan.listen,
                target: <Mono key="target">{`${plan.target.host}:${String(plan.target.port)}`}</Mono>,
              })}
            </li>
            <li>{t("network.openPort.stepRelayRule", { protocol: protocolWords(t, plan.proto), outside: plan.listen, who })}</li>
            {loader ? <li>{t("network.openPort.stepLoader")}</li> : null}
          </>
        )}
      </ol>
    </>
  );
}

function DatabasePicker({ value, onChange }: { value: string | null; onChange: (value: string) => void }) {
  const t = useT();
  const databases = useQuery(databasesQuery());
  const list = databases.data?.databases ?? [];
  return (
    <div className="flex flex-col gap-3">
      <Field label={t("network.openPort.databasePick")} nativeLabel={false}>
        <Select
          aria-label={t("network.openPort.databasePick")}
          value={value}
          placeholder={t("network.openPort.databasePlaceholder")}
          onValueChange={onChange}
          options={list.map((database) => ({ value: `${database.engine}/${database.name}`, label: database.name, hint: database.engine }))}
        />
      </Field>
      {databases.data !== undefined && list.length === 0 ? <InlineMessage tone="info">{t("network.openPort.databaseNone")}</InlineMessage> : null}
      <p className="text-13 text-pretty text-fg-muted">{t("network.openPort.databaseHint")}</p>
    </div>
  );
}

/** Opens the dialog from wherever a port is the question: `open({ port })` fills it in, `dialog` is drawn once beside it. */
export function useOpenPort(): { open: (preset?: OpenPortPreset) => void; dialog: ReactNode } {
  const [preset, setPreset] = useState<OpenPortPreset | null>(null);
  // A fresh key for each opening: another preset is another form, with none of the last one's draft.
  const [opened, setOpened] = useState(0);
  return {
    open: (next = {}) => {
      setPreset(next);
      setOpened((count) => count + 1);
    },
    dialog: preset === null ? null : <OpenPortDialog key={opened} preset={preset} onClose={() => setPreset(null)} />,
  };
}

export function OpenPortDialog({ onClose, preset = {} }: { onClose: () => void; preset?: OpenPortPreset }) {
  const t = useT();
  const say = useServerSentence();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const confirmation = useServerConfirm();
  const jobs = useServerJob();
  const [purpose, setPurpose] = useState<Purpose>(preset.purpose ?? "service");
  const [database, setDatabase] = useState<string | null>(preset.database ?? null);
  const [form, setForm] = useState<OpenForm>({ ...OPEN_PORT_DEFAULTS, port: preset.port === undefined ? "" : String(preset.port), proto: preset.proto ?? "tcp" });
  const [touched, setTouched] = useState<ReadonlySet<string>>(new Set());
  const touch = (field: string): void => setTouched((current) => new Set(current).add(field));
  const edit = (patch: Partial<OpenForm>): void => setForm((current) => ({ ...current, ...patch }));

  const asService = purpose === "service";
  const ports = useQuery({ ...portsQuery(), enabled: asService });
  const firewall = useQuery({ ...firewallQuery(), enabled: asService });
  const reading = asService && (ports.data === undefined || firewall.data === undefined) && !ports.isError && !firewall.isError;
  const failed = asService && (ports.isError || firewall.isError);
  const rows = portRows(ports.data?.claims ?? [], firewall.data?.ports ?? []);
  const state = { active: firewall.data?.firewall.active === true, installed: firewall.data?.firewall.installed === true || firewall.data?.firewall.active === true };
  const port = parsePort(form.port);
  const fact = factsOf(rows, port ?? 0, form.proto);
  const inside = port !== null && fact.reach === "inside";
  const { plan, problems } = planOf(form, rows, state);

  const streams = useQuery({ ...streamsQuery(), enabled: asService && inside });
  const support = streams.data?.support;
  const relayBody = plan?.mode === "relay" ? streamBody(form, plan.fact) : null;
  // nginx is asked what it would do once the person has stopped typing the port, not for each digit of it.
  const asked = useDebounced(relayBody, 400);
  const relayPlan = useQuery({
    queryKey: [...networkKeys.streams, "plan", asked] as const,
    queryFn: ({ signal }) => request("post", "/api/streams/plan", { body: asked ?? streamBody(form), signal }),
    enabled: asked !== null && support?.available === true,
    retry: false,
  });
  // What nginx answered is about what was asked, which is what is on the form now.
  const relayReady = plan?.mode === "relay" ? relayPlan.data !== undefined && JSON.stringify(asked) === JSON.stringify(relayBody) : true;

  // A rule for everybody that opening the port "only to these" leaves standing (item 139). Taking it away is asked for and
  // is about the rules on screen: a tick belongs to the rules it was given for, so another port is never removed by one.
  const wide = asService && port !== null && form.who === "some" && !inside ? openToEveryone(firewall.data?.firewall.rules ?? [], port, form.proto, firewall.data?.protected_ports ?? []) : [];
  const removable = wide.filter((entry) => entry.removable).map((entry) => entry.rule);
  const signature = removable.map((rule) => rule.id).join(",");
  const [ticked, setTicked] = useState<string | null>(null);
  const removing = ticked !== null && ticked === signature ? removable : [];

  const who = form.who === "some" ? form.source.trim() || t("network.openPort.whoAnyone") : t("network.openPort.whoAnyone");
  const ready = asService ? plan !== null && relayReady && !reading : database !== null;
  const label = !asService ? t("network.openPort.databaseGo") : port === null ? t("network.openPort.openAny") : t("network.openPort.open", { port });

  const confirm = async (): Promise<void> => {
    if (!asService) {
      const [engine, name] = (database ?? "").split("/");
      if (engine !== undefined && name !== undefined) await navigate({ to: "/databases/$engine/$name/connect", params: { engine, name } });
      return;
    }
    if (plan === null) return;
    if (plan.mode === "rule") {
      const accepted = await request("post", "/api/server/security/firewall/rules", { body: ruleBody(form, removing.map((rule) => rule.id)) });
      jobs.track(accepted.job_id, "security");
      void queryClient.invalidateQueries({ queryKey: serverKeys.firewall });
      return;
    }
    const result = await request("post", "/api/streams", { body: streamBody(form, plan.fact) });
    void queryClient.invalidateQueries({ queryKey: networkKeys.streams });
    void queryClient.invalidateQueries({ queryKey: serverKeys.ports });
    toast.success(t("network.ports.publishedToast", { name: result.exposure.name }), { description: say(result.firewall_coded, result.firewall) });
  };

  const shown = (field: "port" | "source" | "outside" | "proto"): string | null => (touched.has(field) ? problemText(t, problems, field) : null);

  return (
    <ConfirmDialog
      {...confirmation}
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      friction="simple"
      destructive={false}
      size="lg"
      title={t("network.openPort.title")}
      description={t("network.openPort.description")}
      actionLabel={label}
      ready={ready}
      onConfirm={confirm}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <ChoiceCards<Purpose>
          legend={t("network.openPort.behindLegend")}
          value={purpose}
          onValueChange={setPurpose}
          options={[
            { value: "service", label: t("network.openPort.service"), description: t("network.openPort.serviceHelp") },
            { value: "database", label: t("network.openPort.database"), description: t("network.openPort.databaseHelp") },
          ]}
        />
        {!asService ? (
          <DatabasePicker value={database} onChange={setDatabase} />
        ) : (
          <>
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <Field label={t("network.openPort.port")} error={shown("port")}>
                <Input
                  mono
                  inputMode="numeric"
                  value={form.port}
                  onValueChange={(value: string) => {
                    edit({ port: value });
                    touch("port");
                  }}
                  autoComplete="off"
                />
              </Field>
              <div className="flex min-w-0 flex-col gap-1.5">
                <span aria-hidden="true" className="text-13 font-medium text-fg">
                  {t("network.openPort.protocol")}
                </span>
                <SegmentedControl<OpenProto>
                  label={t("network.openPort.protocol")}
                  value={form.proto}
                  onValueChange={(proto) => {
                    edit({ proto });
                    touch("proto");
                  }}
                  options={[
                    { value: "tcp", label: "TCP" },
                    { value: "udp", label: "UDP" },
                    { value: "any", label: t("network.openPort.protocolBoth") },
                  ]}
                  className="self-start"
                />
                {shown("proto") !== null ? <InlineMessage tone="error">{shown("proto")}</InlineMessage> : null}
              </div>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <span aria-hidden="true" className="text-13 font-medium text-fg">
                {t("network.openPort.who")}
              </span>
              <SegmentedControl<"any" | "some">
                label={t("network.openPort.who")}
                value={form.who}
                onValueChange={(next) => edit({ who: next })}
                options={[
                  { value: "any", label: t("network.openPort.anyone") },
                  { value: "some", label: t("network.openPort.onlyThese") },
                ]}
                className="self-start"
              />
              {form.who === "some" ? (
                <Field label={t("network.openPort.sourceLabel")} description={inside ? t("network.openPort.sourceMany") : t("network.openPort.sourceOne")} error={shown("source")}>
                  {inside ? (
                    <Textarea
                      mono
                      rows={2}
                      value={form.source}
                      onChange={(event) => {
                        edit({ source: event.target.value });
                        touch("source");
                      }}
                      autoComplete="off"
                      spellCheck={false}
                    />
                  ) : (
                    <Input
                      mono
                      value={form.source}
                      onValueChange={(value: string) => {
                        edit({ source: value });
                        touch("source");
                      }}
                      autoComplete="off"
                      spellCheck={false}
                      placeholder="10.0.0.0/24"
                    />
                  )}
                </Field>
              ) : null}
            </div>
            {wide.length > 0 ? (
              <Notice tone="warning" title={t("network.openPort.wideTitle", { port: port ?? 0 })}>
                <div className="flex flex-col gap-2">
                  <p>{t("network.openPort.wideBody", { count: wide.length })}</p>
                  <ul className="flex flex-col gap-1">
                    {wide.map((entry) => (
                      <li key={entry.rule.id}>
                        <Mono>{entry.rule.spec}</Mono>
                        {entry.removable ? null : <span className="block text-fg-muted">{isHeldOpen(entry.rule, firewall.data?.protected_ports ?? []) !== null ? t("network.openPort.wideKept") : t("network.openPort.wideMore")}</span>}
                      </li>
                    ))}
                  </ul>
                  {removable.length > 0 ? (
                    <Checkbox
                      label={t("network.openPort.wideRemove", { count: removable.length })}
                      checked={removing.length > 0}
                      onCheckedChange={(next) => {
                        setTicked(next ? signature : null);
                      }}
                    />
                  ) : null}
                </div>
              </Notice>
            ) : null}
            {inside ? (
              <Field label={t("network.openPort.outside")} description={t("network.openPort.outsideHelp")} error={shown("outside")}>
                <Input
                  mono
                  className="max-w-60"
                  inputMode="numeric"
                  value={form.outside}
                  onValueChange={(value: string) => {
                    edit({ outside: value });
                    touch("outside");
                  }}
                  autoComplete="off"
                />
              </Field>
            ) : null}
            {inside && support !== undefined && !support.available ? <PortsLack support={support} /> : null}
            {problems.firewall === "missing" ? (
              <MissingSoftware component="firewall" onInstalled={() => void queryClient.invalidateQueries({ queryKey: serverKeys.firewall })} />
            ) : problems.firewall === "off" ? (
              <Notice tone="warning" title={t("network.openPort.firewallOffTitle")}>
                <p>
                  {t("network.openPort.firewallOff")}{" "}
                  <TextLink to="/network/firewall" size="inline">
                    {t("network.openPort.firewallOffLink")}
                  </TextLink>
                </p>
              </Notice>
            ) : null}
            <div className="min-w-0 rounded-control border border-border bg-bg-sunken p-4 text-13 text-fg-muted">
              <Subsection title={t("network.openPort.plan")}>
                {failed ? (
                  <ErrorBlock compact error={ports.error ?? firewall.error} title={t("network.openPort.readFailed")} />
                ) : reading ? (
                  <LoadingRegion label={t("network.openPort.reading")}>
                    <Skeleton className="h-12 w-full rounded-control" />
                  </LoadingRegion>
                ) : (
                  <>
                    {port !== null ? <Listener fact={fact} port={port} /> : null}
                    {plan !== null ? (
                      <>
                        <Steps plan={plan} who={who} loader={relayPlan.data?.loader === "write"} removing={removing} />
                        {plan.mode === "rule" && plan.fact.openToAll && form.who === "any" ? <InlineMessage tone="info">{t("network.openPort.alreadyOpen", { port: plan.port })}</InlineMessage> : null}
                        {plan.mode === "relay" && relayPlan.isError ? <ErrorBlock compact error={relayPlan.error} title={t("network.openPort.relayFailed")} /> : null}
                        {plan.mode === "relay" && !relayReady && !relayPlan.isError && support?.available === true ? <p>{t("network.openPort.relayChecking")}</p> : null}
                        {(relayPlan.data?.warnings ?? []).map((warning, index) => (
                          <InlineMessage key={warning} tone="warning">
                            <ServerText coded={relayPlan.data?.warnings_coded?.[index]} text={warning} />
                          </InlineMessage>
                        ))}
                      </>
                    ) : null}
                    <p className="text-12">{t("network.openPort.provider")}</p>
                    {plan !== null ? <p className="text-12">{t("server.firewall.reverts")}</p> : null}
                  </>
                )}
              </Subsection>
            </div>
          </>
        )}
      </div>
    </ConfirmDialog>
  );
}
