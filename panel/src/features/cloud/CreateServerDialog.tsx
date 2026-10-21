import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

import { ElevationCancelledError, request } from "../../api/client";
import { isJobFinished, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { KeyValueList } from "../../components/page/KeyValueList";
import { Stepper } from "../../components/page/Stepper";
import { WizardActions } from "../../components/page/Wizard";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Combobox } from "../../components/ui/Combobox";
import { Dialog } from "../../components/ui/Dialog";
import { Disclosure } from "../../components/ui/Disclosure";
import { Field } from "../../components/ui/Field";
import { ICONS } from "../../components/ui/icons";
import { Input } from "../../components/ui/Input";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { jobState } from "../databases/jobs";
import { fleetKeys } from "../fleet/data";
import { nodeKeys, nodesQuery } from "../fleet/nodes";
import { JobLogPanel } from "../jobs/JobLogPanel";
import { Added } from "../settings/servers/AddServerDialog";
import {
  EMPTY_CREATE,
  cloudAccountsQuery,
  cloudCatalogQuery,
  cloudKeys,
  cloudScriptsQuery,
  createBody,
  createProblem,
  imagesFor,
  plansIn,
  preferredImage,
  priceOf,
} from "./data";
import type { Access, CloudCatalog, CloudChoice, CreateForm, CreateProblem, ProviderKind } from "./data";

type Step = "server" | "machine" | "options" | "review" | "create";

export interface CreateServerDialogProps {
  open: boolean;
  onClose: () => void;
  /** The account to start from; the only one there is, or none. */
  account?: string;
  /** Closes this and opens the form that adds a provider account. */
  onAddAccount: () => void;
}

/** What the wizard knows of a provider's create call. */
function capabilitiesOf(catalog: CloudCatalog | undefined): Record<string, boolean> {
  return catalog?.capabilities ?? {};
}

const KINDS: readonly string[] = ["hetzner", "digitalocean", "vultr"];

function providerKey(kind: string | undefined): ProviderKind | null {
  return kind !== undefined && KINDS.includes(kind) ? (kind as ProviderKind) : null;
}

/** Ticks and unticks one id in a list of ids. */
function toggle(list: readonly string[], id: string, on: boolean): string[] {
  return on ? [...list.filter((item) => item !== id), id] : list.filter((item) => item !== id);
}

/**
 * Creating a server at a cloud provider, in the console. (1) Name it and pick the account it is
 * created with; (2) pick where it goes, what size and what it starts from, from what the provider
 * itself lists for that account now; (3) the options the provider's create call has, and the boot
 * scripts of the library; (4) read what will be created, and what it costs, before pressing the
 * button; (5) the job: the machine is created, waited for, its SSH host key checked against the
 * one chosen for it, Noust installed and the server joined, with the log streaming. Confirming
 * asks "Confirm it's you". Nothing secret is on this page: the token never left the central.
 */
export function CreateServerDialog({ open, onClose, account: initialAccount, onAddAccount }: CreateServerDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>("server");
  const [form, setForm] = useState<CreateForm>({ ...EMPTY_CREATE, account: initialAccount ?? "" });
  const [problem, setProblem] = useState<CreateProblem | null>(null);
  const followed = useFollowedJob();
  const nameRef = useRef<HTMLInputElement>(null);
  const set = (patch: Partial<CreateForm>): void => {
    setForm((previous) => ({ ...previous, ...patch }));
    setProblem(null);
  };

  const accounts = useQuery({ ...cloudAccountsQuery(), enabled: open });
  const only = accounts.data?.length === 1 ? accounts.data[0] : undefined;
  const chosenAccount = form.account === "" ? (only?.name ?? "") : form.account;
  const catalog = useQuery({ ...cloudCatalogQuery(chosenAccount), enabled: open && chosenAccount !== "" && step !== "server" });
  const scripts = useQuery({ ...cloudScriptsQuery(), enabled: open && (step === "options" || step === "review") });
  const data = catalog.data;
  const capabilities = capabilitiesOf(data);
  const provider = providerKey(data?.provider ?? accounts.data?.find((candidate) => candidate.name === chosenAccount)?.provider);

  const plans = data === undefined ? [] : plansIn(data, form.location);
  const plan: CloudChoice | undefined = plans.find((candidate) => candidate.id === form.plan);
  const images = data === undefined ? [] : imagesFor(data, plan);

  const create = useMutation({
    mutationFn: () => request("post", "/api/fleet/cloud/servers", { body: createBody({ ...form, account: chosenAccount }, capabilities) }),
    onSuccess: (accepted) => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      followed.follow(accepted.job_id);
      setStep("create");
    },
    onError: (error) => {
      // Declining "Confirm it's you" changes nothing: the review stays where it was.
      if (error instanceof ElevationCancelledError) create.reset();
    },
  });

  const job = followed.job;
  const finished = job !== null && isJobFinished(job);
  const running = followed.id !== null && !finished;
  const succeeded = job !== null && jobState(job.status) === "succeeded";
  const settled = useRef<string | null>(null);
  useEffect(() => {
    if (job === null || !isJobFinished(job) || settled.current === job.id) return;
    settled.current = job.id;
    void queryClient.invalidateQueries({ queryKey: nodeKeys.all });
    void queryClient.invalidateQueries({ queryKey: fleetKeys.all });
    void queryClient.invalidateQueries({ queryKey: cloudKeys.machines });
  }, [job, queryClient]);
  const nodes = useQuery({ ...nodesQuery(), enabled: succeeded });
  const node = nodes.data?.items.find((item) => item.name === form.name.trim()) ?? null;

  const preview = useQuery({
    queryKey: [...cloudKeys.scripts, "preview", form.scripts],
    queryFn: () => request("post", "/api/fleet/cloud/scripts/preview", { body: { scripts: form.scripts } }),
    enabled: open && step === "review",
    retry: false,
  });

  const reset = (): void => {
    setStep("server");
    setForm({ ...EMPTY_CREATE, account: initialAccount ?? "" });
    setProblem(null);
    followed.dismiss();
    create.reset();
  };
  const close = (): void => {
    if (create.isPending) return;
    if (running) {
      toast.info(t("cloud.create.job.running", { name: form.name }), {
        description: t("cloud.create.job.backgrounded"),
        action: { label: t("cloud.create.job.seeActivity"), onClick: () => void navigate({ to: "/activity" }) },
      });
    }
    onClose();
    reset();
  };

  const words = (found: CreateProblem): string => {
    switch (found) {
      case "name":
        return t("cloud.create.problem.name");
      case "account":
        return t("cloud.create.problem.account");
      case "location":
        return t("cloud.create.problem.location");
      case "plan":
        return t("cloud.create.problem.plan");
      case "image":
        return t("cloud.create.problem.image");
      case "addresses":
        return t("cloud.create.problem.addresses");
    }
  };

  const next = (to: Step, blockers: readonly CreateProblem[]): void => {
    const found = createProblem({ ...form, account: chosenAccount });
    // Only what this step asks is checked: what a later step asks is empty on purpose.
    const mine = found !== null && blockers.includes(found) ? found : null;
    setProblem(mine);
    if (mine === null) setStep(to);
  };

  const onLocation = (location: string): void => {
    if (data === undefined) return;
    const stillSold = plansIn(data, location).some((candidate) => candidate.id === form.plan);
    set({ location, plan: stillSold ? form.plan : "", image: stillSold ? form.image : "" });
  };
  const onPlan = (planId: string): void => {
    if (data === undefined) return;
    const chosenPlan = plans.find((candidate) => candidate.id === planId);
    const fitting = imagesFor(data, chosenPlan);
    const keep = fitting.some((candidate) => candidate.id === form.image) ? form.image : preferredImage(fitting);
    set({ plan: planId, image: keep });
  };

  const steps = [
    { id: "server", label: t("cloud.create.steps.server") },
    { id: "machine", label: t("cloud.create.steps.machine") },
    { id: "options", label: t("cloud.create.steps.options") },
    { id: "review", label: t("cloud.create.steps.review") },
    { id: "create", label: t("cloud.create.steps.create") },
  ];
  const name = form.name.trim();
  const accountOptions = (accounts.data ?? []).map((candidate) => ({ value: candidate.name, label: candidate.name, hint: t(`cloud.provider.${providerKey(candidate.provider) ?? "hetzner"}.name`) }));

  let body;
  let footer;
  if (step === "server") {
    body = (
      <form
        noValidate
        className="flex min-w-0 flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          next("machine", ["name", "account"]);
        }}
      >
        <Field label={t("cloud.create.name")} description={t("cloud.create.nameHelp")} error={problem === "name" ? words("name") : undefined}>
          <Input ref={nameRef} mono maxLength={32} value={form.name} onValueChange={(value: string) => set({ name: value })} placeholder="web-2" autoComplete="off" autoCapitalize="off" spellCheck={false} />
        </Field>
        {accounts.isError ? (
          <ErrorBlock error={accounts.error} title={t("cloud.accounts.loadFailed")} onRetry={() => void accounts.refetch()} />
        ) : accounts.data?.length === 0 ? (
          <Notice
            tone="info"
            title={t("cloud.create.noAccountTitle")}
            action={
              <Button size="sm" onClick={onAddAccount}>
                {t("cloud.accounts.add")}
              </Button>
            }
          >
            {t("cloud.create.noAccountBody")}
          </Notice>
        ) : (
          <Field label={t("cloud.create.account")} nativeLabel={false} description={t("cloud.create.accountHelp")} error={problem === "account" ? words("account") : undefined}>
            <Select
              value={chosenAccount === "" ? null : chosenAccount}
              onValueChange={(account) => set({ account, location: "", plan: "", image: "" })}
              placeholder={t("cloud.create.accountPlaceholder")}
              options={accountOptions}
            />
          </Field>
        )}
        <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("cloud.create.howItWorks")}</p>
      </form>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{ label: t("cloud.common.cancel"), onClick: close }}
        next={{
          label: t("cloud.create.next"),
          onClick: () => {
            next("machine", ["name", "account"]);
          },
        }}
      />
    );
  } else if (step === "machine") {
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        {catalog.isError ? (
          <ErrorBlock error={catalog.error} title={t("cloud.create.catalogFailed")} onRetry={() => void catalog.refetch()} retrying={catalog.isRefetching} />
        ) : data === undefined ? (
          <LoadingRegion label={t("cloud.create.catalogLoading")} className="flex flex-col gap-4">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </LoadingRegion>
        ) : (
          <>
            <Field label={t("cloud.create.location")} nativeLabel={false} error={problem === "location" ? words("location") : undefined}>
              <Combobox
                items={data.locations.map((choice) => ({ value: choice.id, label: choice.name, detail: choice.detail }))}
                value={form.location === "" ? null : form.location}
                onValueChange={onLocation}
                placeholder={t("cloud.create.locationPlaceholder")}
              />
            </Field>
            <Field
              label={t("cloud.create.plan")}
              nativeLabel={false}
              description={form.location === "" ? t("cloud.create.planNeedsLocation") : undefined}
              error={problem === "plan" ? words("plan") : undefined}
            >
              <Combobox
                items={plans.map((choice) => ({ value: choice.id, label: `${choice.name} · ${choice.detail}`, price: priceOf(choice) ?? "" }))}
                value={form.plan === "" ? null : form.plan}
                onValueChange={onPlan}
                disabled={form.location === ""}
                placeholder={t("cloud.create.planPlaceholder")}
                emptyMessage={t("cloud.create.planNone")}
              />
            </Field>
            <Field
              label={t("cloud.create.image")}
              nativeLabel={false}
              description={form.plan === "" ? t("cloud.create.imageNeedsPlan") : t("cloud.create.imageHelp")}
              error={problem === "image" ? words("image") : undefined}
            >
              <Combobox
                items={images.map((choice) => ({ value: choice.id, label: choice.name, detail: choice.detail }))}
                value={form.image === "" ? null : form.image}
                onValueChange={(image) => set({ image })}
                disabled={form.plan === ""}
                placeholder={t("cloud.create.imagePlaceholder")}
                emptyMessage={t("cloud.create.imageNone")}
              />
            </Field>
            {plan !== undefined && priceOf(plan) !== null ? <p className="text-13 text-fg-muted">{t("cloud.create.price", { price: priceOf(plan) ?? "" })}</p> : null}
          </>
        )}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{
          label: t("cloud.create.back"),
          onClick: () => {
            setStep("server");
          },
        }}
        next={{
          label: t("cloud.create.next"),
          onClick: () => {
            next("options", ["location", "plan", "image"]);
          },
        }}
        {...(data === undefined ? { missing: t("cloud.create.catalogLoading") } : {})}
      />
    );
  } else if (step === "options") {
    const firewalls = data?.firewalls ?? [];
    const networks = data?.networks ?? [];
    const keys = data?.ssh_keys ?? [];
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        <Field label={t("cloud.create.access")} nativeLabel={false} description={t("servers.add.access.description")}>
          <Select<Access>
            value={form.access}
            onValueChange={(access) => set({ access })}
            options={[
              { value: "admin", label: t("fleet.access.level.admin"), hint: t("servers.add.access.adminHint") },
              { value: "deploy", label: t("fleet.access.level.deploy"), hint: t("servers.add.access.deployHint") },
              { value: "read", label: t("fleet.access.level.read"), hint: t("servers.add.access.readHint") },
            ]}
          />
        </Field>
        {(scripts.data ?? []).length > 0 ? (
          <fieldset className="flex min-w-0 flex-col gap-2.5">
            <legend className="mb-1.5 text-13 font-medium text-fg">{t("cloud.create.scripts")}</legend>
            <p className="text-13 text-fg-muted">{t("cloud.create.scriptsHelp")}</p>
            {(scripts.data ?? []).map((script) => (
              <Checkbox
                key={script.name}
                checked={form.scripts.includes(script.name)}
                onCheckedChange={(on) => set({ scripts: toggle(form.scripts, script.name, on) })}
                label={script.name}
                description={script.description !== "" ? script.description : undefined}
              />
            ))}
          </fieldset>
        ) : null}
        <Disclosure label={t("cloud.create.more")} defaultOpen={form.sshKeys.length + form.firewalls.length + form.networks.length > 0 || form.backups || !form.ipv4 || !form.ipv6 || form.hostAccess || form.deleteOnFailure}>
          <div className="flex min-w-0 flex-col gap-5">
            <div className="flex min-w-0 flex-col gap-3">
              <Checkbox checked={form.ipv4} onCheckedChange={(ipv4) => set({ ipv4 })} disabled={capabilities["ipv4_optional"] !== true} label={t("cloud.create.ipv4")} description={capabilities["ipv4_optional"] === true ? undefined : t("cloud.create.ipv4Fixed")} />
              {capabilities["ipv6"] === true ? <Checkbox checked={form.ipv6} onCheckedChange={(ipv6) => set({ ipv6 })} label={t("cloud.create.ipv6")} /> : null}
              {problem === "addresses" ? <p className="text-13 text-fail">{words("addresses")}</p> : null}
              {capabilities["backups"] === true ? <Checkbox checked={form.backups} onCheckedChange={(backups) => set({ backups })} label={t("cloud.create.backups")} description={t("cloud.create.backupsHelp")} /> : null}
            </div>
            {keys.length > 0 ? (
              <fieldset className="flex min-w-0 flex-col gap-2.5">
                <legend className="mb-1.5 text-13 font-medium text-fg">{t("cloud.create.sshKeys")}</legend>
                <p className="text-13 text-fg-muted">{t("cloud.create.sshKeysHelp")}</p>
                {keys.map((key) => (
                  <Checkbox key={key.id} checked={form.sshKeys.includes(key.id)} onCheckedChange={(on) => set({ sshKeys: toggle(form.sshKeys, key.id, on) })} label={key.name} description={key.detail !== "" ? key.detail : undefined} />
                ))}
              </fieldset>
            ) : null}
            {capabilities["firewalls"] === true && firewalls.length > 0 ? (
              <fieldset className="flex min-w-0 flex-col gap-2.5">
                <legend className="mb-1.5 text-13 font-medium text-fg">{t("cloud.create.firewalls")}</legend>
                {capabilities["multiple_firewalls"] === true ? null : <p className="text-13 text-fg-muted">{t("cloud.create.oneOnly")}</p>}
                {firewalls.map((firewall) => (
                  <Checkbox
                    key={firewall.id}
                    checked={form.firewalls.includes(firewall.id)}
                    onCheckedChange={(on) => set({ firewalls: capabilities["multiple_firewalls"] === true ? toggle(form.firewalls, firewall.id, on) : on ? [firewall.id] : [] })}
                    label={firewall.name}
                  />
                ))}
              </fieldset>
            ) : null}
            {capabilities["networks"] === true && networks.length > 0 ? (
              <fieldset className="flex min-w-0 flex-col gap-2.5">
                <legend className="mb-1.5 text-13 font-medium text-fg">{t("cloud.create.networks")}</legend>
                {capabilities["multiple_networks"] === true ? null : <p className="text-13 text-fg-muted">{t("cloud.create.oneOnly")}</p>}
                {networks.map((network) => (
                  <Checkbox
                    key={network.id}
                    checked={form.networks.includes(network.id)}
                    onCheckedChange={(on) => set({ networks: capabilities["multiple_networks"] === true ? toggle(form.networks, network.id, on) : on ? [network.id] : [] })}
                    label={network.name}
                    description={network.detail !== "" ? network.detail : undefined}
                  />
                ))}
              </fieldset>
            ) : null}
            <Checkbox checked={form.hostAccess} onCheckedChange={(hostAccess) => set({ hostAccess })} label={t("enroll.server.hostAccess")} description={t("enroll.server.hostAccessHelp")} />
            <Checkbox checked={form.deleteOnFailure} onCheckedChange={(deleteOnFailure) => set({ deleteOnFailure })} label={t("cloud.create.deleteOnFailure")} description={t("cloud.create.deleteOnFailureHelp")} />
          </div>
        </Disclosure>
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{
          label: t("cloud.create.back"),
          onClick: () => {
            setStep("machine");
          },
        }}
        next={{
          label: t("cloud.create.toReview"),
          onClick: () => {
            next("review", ["addresses"]);
          },
        }}
      />
    );
  } else if (step === "review") {
    const price = priceOf(plan);
    body = (
      <div className="flex min-w-0 flex-col gap-5">
        <KeyValueList
          items={[
            { label: t("cloud.create.review.name"), value: name, mono: true },
            { label: t("cloud.create.review.provider"), value: `${provider === null ? "" : t(`cloud.provider.${provider}.name`)} · ${chosenAccount}`, copy: false, mono: false },
            { label: t("cloud.create.review.where"), value: data?.locations.find((choice) => choice.id === form.location)?.name ?? form.location, copy: false, mono: false },
            { label: t("cloud.create.review.plan"), value: plan === undefined ? form.plan : `${plan.name} · ${plan.detail}`, copy: false, mono: false },
            { label: t("cloud.create.review.image"), value: data?.images.find((choice) => choice.id === form.image)?.name ?? form.image, copy: false, mono: false },
            { label: t("cloud.create.review.addresses"), value: [form.ipv4 ? "IPv4" : null, form.ipv6 && capabilities["ipv6"] === true ? "IPv6" : null].filter(Boolean).join(" + "), copy: false, mono: false },
            { label: t("cloud.create.review.access"), value: t(`fleet.access.level.${form.access}`), copy: false, mono: false },
            { label: t("cloud.create.review.scripts"), value: form.scripts.length === 0 ? t("cloud.create.review.noScripts") : form.scripts.join(", "), copy: false, mono: form.scripts.length > 0 },
          ]}
        />
        <Notice tone="warning" title={t("cloud.create.costTitle")}>
          {price !== null ? t("cloud.create.costWithPrice", { price, provider: provider === null ? "" : t(`cloud.provider.${provider}.name`) }) : t("cloud.create.cost", { provider: provider === null ? "" : t(`cloud.provider.${provider}.name`) })}
        </Notice>
        <Disclosure label={t("cloud.create.bootDocument")}>
          <div className="flex min-w-0 flex-col gap-2">
            <p className="text-13 text-fg-muted">{t("cloud.create.bootDocumentHelp")}</p>
            {preview.isError ? (
              <ErrorBlock compact error={preview.error} title={t("cloud.create.previewFailed")} />
            ) : preview.data === undefined ? (
              <Skeleton className="h-24 w-full" />
            ) : (
              <SystemOutput label={t("cloud.create.bootDocument")}>{preview.data.document}</SystemOutput>
            )}
          </div>
        </Disclosure>
        {create.isError && !(create.error instanceof ElevationCancelledError) ? <ErrorBlock live compact error={create.error} title={t("cloud.create.startFailed")} /> : null}
      </div>
    );
    footer = (
      <WizardActions
        className="w-full"
        back={{
          label: t("cloud.create.back"),
          onClick: () => {
            if (!create.isPending) setStep("options");
          },
        }}
        next={{ label: t("cloud.create.submit"), loading: create.isPending, onClick: () => create.mutate() }}
      />
    );
  } else {
    const state = job === null ? "queued" : jobState(job.status);
    body = (
      <div className="flex min-w-0 flex-col gap-4">
        <JobLogPanel
          job={job}
          words={{ queued: t("cloud.create.job.queued", { name }), running: t("cloud.create.job.running", { name }), succeeded: t("cloud.create.job.succeeded", { name }), failed: t("cloud.create.job.failed", { name }) }}
          label={t("cloud.create.job.logLabel", { name })}
          height={280}
          result={node !== null ? <Added t={t} node={node} onNavigate={close} /> : null}
        />
        {state === "failed" ? <p className="text-13 text-pretty text-fg-muted">{t("cloud.create.job.failedHint")}</p> : null}
      </div>
    );
    footer = (
      <>
        {state === "failed" ? (
          <Button
            icon={<ICONS.dismiss aria-hidden="true" />}
            onClick={() => {
              followed.dismiss();
              create.reset();
              setStep("review");
            }}
          >
            {t("cloud.create.job.tryAgain")}
          </Button>
        ) : null}
        <Button variant="primary" onClick={close}>
          {finished ? t("cloud.create.done") : t("cloud.create.close")}
        </Button>
      </>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(opened) => {
        if (!opened) close();
      }}
      size="lg"
      initialFocus={nameRef}
      title={t("cloud.create.title")}
      description={t("cloud.create.description")}
      footer={footer}
    >
      <div className="flex min-w-0 flex-col gap-5">
        <Stepper orientation="horizontal" steps={steps} current={step} />
        {body}
      </div>
    </Dialog>
  );
}
