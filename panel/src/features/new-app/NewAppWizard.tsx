import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBlocker, useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { request } from "../../api/client";
import { importApp } from "../../api/queries/appImport";
import type { ImportAppBody } from "../../api/queries/appImport";
import { appKeys, appsQuery, appTypesQuery } from "../../api/queries/apps";
import { temporaryNameQuery } from "../../api/queries/domains";
import type { TemporaryNameOf } from "../../api/queries/domains";
import { githubStatusQuery } from "../../api/queries/github";
import { gitlabStatusQuery } from "../../api/queries/gitlab";
import { webserverQuery } from "../../api/queries/config";
import { isJobFinished, jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { recipeQuery } from "../../api/queries/recipes";
import type { Recipe, RecipeSummary } from "../../api/queries/recipes";
import { systemInfoQuery } from "../../api/queries/system";
import { announce } from "../../app/Announcer";
import { getLocale } from "../../app/locale";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { Wizard } from "../../components/page/Wizard";
import type { WizardActionsProps } from "../../components/page/Wizard";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Dialog } from "../../components/ui/Dialog";
import { textLinkClassName } from "../../components/ui/TextLink";
import { translate, useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { normalizeDomain } from "../domains/names";
import { imageProblem } from "../container/model";
import { AddressFields } from "./AddressFields";
import { ConfigureStep } from "./ConfigureStep";
import { DeployStep, deploySummary } from "./DeployStep";
import type { OpenSteps } from "./DeployStep";
import type { DeployKind } from "./DeployStep";
import { DetectedLine } from "./DetectedLine";
import { EnvironmentFields } from "./EnvironmentFields";
import { exportFileProblem, importBody, importProblems, importRefusalOf, initialImportForm, readExport } from "./exportFile";
import type { ImportForm } from "./exportFile";
import { EXPORT_FILE_INPUT, ImportFile } from "./ImportFile";
import type { LoadedExport } from "./ImportFile";
import { ImportAddress, ImportSecrets, importSummary } from "./ImportReview";
import { RecipeGallery } from "./RecipeGallery";
import { RECIPE_VARIABLE_ATTRIBUTE, RecipeFacts, RecipeVariables, recipeSummary } from "./RecipeReview";
import { initialRecipeForm, recipeBody, recipeProblems, recipeVariableField, requiredVariables } from "./recipe";
import type { RecipeForm } from "./recipe";
import { DatabaseStep, NO_DATABASE, NO_DATABASE_STEP_TYPES, databaseSummary, useDatabaseStepAvailable, withDatabase, withDatabaseStep } from "../databases/wizard/DatabaseStep";
import type { DatabaseChoice } from "../databases/wizard/DatabaseStep";
import { useServerStep, withServerStep } from "./ServerStep";
import { SourceStep } from "./SourceStep";
import { useAdoption } from "./AdoptStack";
import { COMPOSE_DOCUMENT_FIELD, useComposeDocument } from "./ComposeDocument";
import { wizardRecipe, wizardStart } from "./search";
import { DestinationFields } from "../teams/DestinationFields";
import { useDestination } from "../teams/useDestination";
import type { Destination } from "../teams/useDestination";
import type { SourceMode } from "./SourceStep";
import { useDomainDnsCheck } from "./useDomainDnsCheck";
import type { LandingTarget } from "./useDeploymentLanding";
import {
  STEP_LABELS,
  WIZARD_STEPS,
  composeInspection,
  createAppBody,
  dnsQuestion,
  errorsOf,
  hasOwnDomain,
  imageInspection,
  initialReview,
  inspectBody,
  manualInspection,
  nextStep,
  previousStep,
  refusalOf,
  reviewProblems,
  sameSource,
  shortSource,
  sourceKind,
  sourceProblems,
  stepAsks,
  stepOfField,
  typeName,
} from "./wizard";
import type { CreateAppBody, Inspection, ReviewErrors, ReviewForm, SourceErrors, SourceForm, StartKind, Step, WebServer } from "./wizard";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The step on screen: its heading, where a new step starts for the keyboard and a screen reader. */
function focusStepHeading(): void {
  const heading = document.querySelector<HTMLElement>('[data-template="wizard"] [data-slot="step"] h2');
  if (heading === null) return;
  // The template's heading is not in the tab order; it takes focus only from here.
  heading.setAttribute("tabindex", "-1");
  heading.classList.add("outline-none");
  heading.focus({ preventScroll: false });
  heading.scrollIntoView({ block: "nearest" });
}

/** Where it will live, for the last step's list of what was decided: team, project and environment by name. */
function placeSummary(t: T, destination: Destination): KeyValueItem {
  const { value } = destination;
  const name = (list: readonly { slug: string; name: string }[], slug: string): string => list.find((item) => item.slug === slug)?.name ?? slug;
  return {
    label: t("teams.destination.title"),
    value:
      value === null
        ? ""
        : [destination.showTeam ? name(destination.teams, value.team) : null, name(destination.projects, value.project), name(destination.environments, value.environment)]
            .filter((part) => part !== null)
            .join(" / "),
    mono: false,
    copy: false,
  };
}

/** The first field of the step that needs attention takes focus; its message is read with it. */
function focusFirstInvalid(): void {
  requestAnimationFrame(() => {
    const step = document.querySelector('[data-template="wizard"] [data-slot="step"]');
    const invalid =
      step?.querySelector<HTMLElement>("[aria-invalid='true']") ??
      step?.querySelector<HTMLElement>("[data-invalid] input, [data-invalid] button, [data-invalid] [role='combobox']");
    invalid?.focus();
  });
}

function kindOf(mode: SourceMode): StartKind {
  return mode === "recipe" ? "recipe" : mode === "import" ? "import" : "code";
}

/** Reads an export file in the browser, as text. */
function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(reader.error ?? new Error("unreadable"));
    reader.readAsText(file);
  });
}

/** A step's errors put in place of what that step said before, the other steps' left alone. */
function replaceStepErrors(previous: ReviewErrors, step: Step, own: ReviewErrors, kind: StartKind = "code"): ReviewErrors {
  return { ...Object.fromEntries(Object.entries(previous).filter(([field]) => stepOfField(field, kind) !== step)), ...own };
}

/**
 * After a Continue pointed at a step's fields, their messages follow what is typed: the step's
 * errors are found again on every change, and only the step's.
 */
function revalidated(previous: ReviewErrors, step: Step, found: ReviewErrors, kind: StartKind = "code"): ReviewErrors {
  if (errorsOf(previous, step, kind).length === 0) return previous;
  return replaceStepErrors(previous, step, Object.fromEntries(errorsOf(found, step, kind)), kind);
}

/** Takes the operator to a recipe variable's control, from the sentence that names it. */
function focusRecipeVariable(name: string): void {
  document.querySelector<HTMLElement>(`[${RECIPE_VARIABLE_ATTRIBUTE}="${CSS.escape(name)}"]`)?.focus();
}

/** The first message of a set of errors, as the sentence Continue says when pressed. */
/** The name the operator knows a field of the review's errors by, when it has a short one. */
function fieldName(t: T, field: string): string | null {
  if (field === "domain") return t("newApp.review.domain");
  if (field === "port") return t("newApp.review.port");
  if (field === "appType") return t("newApp.review.deployAs");
  if (field === "source") return t("newApp.source.field");
  // A variable's field ends with its name; one added by hand may have none yet.
  if ((field.startsWith("env:") || field.startsWith("secret:")) && !field.includes(":added:")) return field.split(":").at(-1) ?? null;
  return null;
}

/**
 * What the wizard's bar says is missing: the one problem in its own words, or how many there
 * are and which comes first. Each is also said beside its field.
 */
function missingOf(t: T, errors: [string, string][]): string | undefined {
  const [first] = errors;
  if (first === undefined) return undefined;
  if (errors.length === 1) return first[1];
  const count = String(errors.length);
  const name = fieldName(t, first[0]);
  return name === null ? t("newApp.page.missingMany", { count }) : t("newApp.page.missingManyNamed", { count, name });
}

/** What is asked of the inspection: the source, and whether to move on once it is read (Continue) or only to read it (a paste). */
interface InspectRequest {
  form: SourceForm;
  advance: boolean;
}

/** What a step shows and what its Continue does. */
interface StepView {
  title: string;
  description: ReactNode;
  content: ReactNode;
  actions: WizardActionsProps;
}

/**
 * The new-app wizard, as a T5 page: where the code is (a repository or a directory, a recipe,
 * or another server's export), where it answers, how it runs, its variables, and the deploy -
 * a vertical stepper beside the step, what has been decided so far beside that on a wide
 * screen, and Back and Continue in a bar that stays at the foot. Continue is never disabled:
 * when something is missing it says what, and takes the operator to it. What was typed survives
 * going back and forth, and is never asked twice. A deploy from code hands over to its
 * deployment page as soon as the build starts; a recipe or an import is followed here to its end.
 */
export function NewAppWizard() {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const apps = useQuery(appsQuery());
  const webserver = useQuery(webserverQuery());
  const types = useQuery(appTypesQuery());
  const system = useQuery({ ...systemInfoQuery(), staleTime: 10 * 60_000, refetchOnWindowFocus: false });
  const followedJob = useFollowedJob();
  // Only decides whether "From GitHub" is offered: a failure leaves the typed source, as before.
  const github = useQuery({ ...githubStatusQuery(), retry: false });
  // Likewise for "From GitLab": offered where a connection exists.
  const gitlab = useQuery({ ...gitlabStatusQuery(), retry: false });

  // On a fleet the wizard asks on which server first (ServerStep.tsx).
  const serverStep = useServerStep({
    onContinue: () => {
      go("source");
    },
    onArrive: () => {
      if (!moved) setStep("server");
    },
  });
  const [step, setStep] = useState<Step>(serverStep.startHere ? "server" : "source");
  const [source, setSource] = useState<SourceForm>({ source: "", branch: "" });
  // The way in the link asked for (`/apps/new?start=recipe`, from the Applications page and the
  // palette): the wizard opens on it instead of on the default.
  const wizardSearch = useSearch({ from: "/_console/apps/new" });
  const start = wizardStart(wizardSearch.start);
  // The recipe the link already chose (`&recipe=wordpress`, from the catalog): opened for the operator.
  const linkedRecipe = start === "recipe" ? wizardRecipe(wizardSearch.recipe) : undefined;
  // Which team, project and environment it goes to: asked only where there is more than one
  // place (a project's page starts it on that project), so the common case costs no click.
  const destination = useDestination({
    ...(wizardSearch.team !== undefined ? { team: wizardSearch.team } : {}),
    ...(wizardSearch.project !== undefined ? { project: wizardSearch.project } : {}),
    ...(wizardSearch.environment !== undefined ? { environment: wizardSearch.environment } : {}),
  });
  // Null until the operator acts: what the link asked for, GitHub when the App is connected,
  // the typed source otherwise.
  const [chosenMode, setChosenMode] = useState<SourceMode | null>(start ?? null);
  // The same page, asked again from where it already is (the palette, over the wizard): a new
  // start replaces the way in, settled while rendering rather than in an effect.
  const [seenStart, setSeenStart] = useState(start);
  if (start !== seenStart) {
    setSeenStart(start);
    if (start !== undefined) setChosenMode(start);
  }
  const [sourceErrors, setSourceErrors] = useState<SourceErrors>({});
  // An image from a registry, as a container application starts from it: no repository to read.
  const [imageRef, setImageRef] = useState("");
  const [imageError, setImageError] = useState<string | undefined>(undefined);
  const [inspected, setInspected] = useState<{ inspection: Inspection; for: SourceForm; document?: string } | null>(null);
  const [review, setReview] = useState<ReviewForm | null>(null);
  const [reviewErrors, setReviewErrors] = useState<ReviewErrors>({});
  const [target, setTarget] = useState<LandingTarget | null>(null);
  // A recipe: the one chosen, read in full, and what the operator set over it.
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [recipeForm, setRecipeForm] = useState<RecipeForm | null>(null);
  const [recipeErrors, setRecipeErrors] = useState<ReviewErrors>({});
  // An import: the export read from the file, why a file was refused, and what changes.
  const [loaded, setLoaded] = useState<LoadedExport | null>(null);
  const [importProblem, setImportProblem] = useState<string | null>(null);
  const [importForm, setImportForm] = useState<ImportForm | null>(null);
  const [importErrors, setImportErrors] = useState<ReviewErrors>({});
  // A code start's database, created with the app before its first build (databases/wizard).
  const [database, setDatabase] = useState<DatabaseChoice>(NO_DATABASE);
  const databaseAvailable = useDatabaseStepAvailable();

  // Set by a move between steps: the first step on arrival keeps focus on the page's title.
  const [moved, setMoved] = useState(false);
  const leaving = useRef(false);
  const abort = useRef<AbortController | null>(null);

  const githubStatus = github.data ?? null;
  const sourceMode: SourceMode = chosenMode ?? (githubStatus?.configured === true ? "github" : "manual");
  // A stack that already runs: its own form and its two presses, preview then adopt.
  const adoption = useAdoption();
  // A Docker Compose document typed in: looked at first, then deployed as the application's source.
  const composeDoc = useComposeDocument();
  const kind = kindOf(sourceMode);
  const typeList = types.data?.types ?? [];

  const cores = system.data?.cpu.cores ?? null;
  const context = useMemo(() => {
    const list = apps.data?.apps ?? [];
    const domains = new Set(list.map((app) => app.domain));
    return {
      domains,
      ports: new Map(list.filter((app) => app.port !== null && app.port !== undefined).map((app) => [Number(app.port), app.domain])),
      cores,
      // A server with a domain of the operator's own in use is asked for one; one without is not.
      ownDomains: hasOwnDomain(domains),
    };
  }, [apps.data, cores]);

  // Every step this start has, in order, which is what the stepper lists. Database: not for a
  // static site (nothing runs to read it), nor a monorepo or a compose project, which provision
  // their own.
  const allSteps = withDatabaseStep(WIZARD_STEPS[kind], databaseAvailable && kind === "code" && !NO_DATABASE_STEP_TYPES.has(review?.appType ?? ""));
  // The steps Continue and Back walk: the ones with something to ask (`stepAsks`), and the one on
  // screen, which the operator opened and may leave from. The server step is reached on its own.
  const askContext = { ...context, temporaryAvailable: (review?.temporaryDomain ?? "") !== "" };
  const order = allSteps.filter((id) => id === step || stepAsks(id, kind, { inspection: inspected?.inspection ?? null, review, context: askContext }));
  const passedOver = allSteps.filter((id) => !order.includes(id));
  // The stepper lists the steps that ask, and the one on screen: a source that needed nothing from
  // the operator is not announced as a process of six steps. What was passed over is in the
  // summary of the last, with the way to open it.
  const steps = withServerStep(order, serverStep.enabled);

  const go = (next: Step): void => {
    setMoved(true);
    setStep(next);
    const index = steps.indexOf(next);
    announce(t("newApp.steps.announce", { number: String(index + 1), total: String(steps.length), label: t(STEP_LABELS[next]) }));
  };

  // A new step starts at its heading, so the next Tab and a screen reader begin there.
  useEffect(() => {
    if (moved) focusStepHeading();
  }, [step, moved]);

  const defaultWebserver: WebServer = webserver.data?.webserver === "apache" ? "apache" : "nginx";

  /** The temporary address this application would get, or empty where the server cannot make one. */
  const temporaryFor = async (of: TemporaryNameOf): Promise<string> => {
    try {
      return (await queryClient.query(temporaryNameQuery(of))).domain;
    } catch {
      // Behind NAT with no declared address: there is no temporary address, and that is no error to show.
      return "";
    }
  };

  /** The review a source proposes, with where it answers: the temporary address where there is no domain of the operator's yet. */
  const reviewFor = (inspection: Inspection, temporary: string, previous: ReviewForm | null, image?: string): ReviewForm =>
    initialReview(inspection, { webserver: defaultWebserver, taken: context.ports, temporary, ownDomains: context.ownDomains, ...(image !== undefined ? { image } : {}) }, previous);

  /**
   * The first step after the source that has something to ask, in the order of the start: where it
   * answers, then what is not as detected, then the deploy. Continue goes straight there, so a
   * source that needs nothing from the operator is two presses from the build.
   */
  const stepAfterSource = (inspection: Inspection, proposed: ReviewForm): Step => {
    const ask = { inspection, review: proposed, context: { ...context, temporaryAvailable: proposed.temporaryDomain !== "" } };
    const steps = withDatabaseStep(WIZARD_STEPS.code, databaseAvailable && !NO_DATABASE_STEP_TYPES.has(proposed.appType));
    return steps.slice(1).find((id) => stepAsks(id, "code", ask)) ?? "deploy";
  };

  const pressedWhileReading = useRef(false);
  const inspect = useMutation({
    mutationFn: async ({ form }: InspectRequest) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      const inspection = await request("post", "/api/apps/inspect", { body: inspectBody(form), signal: controller.signal });
      const temporary = await temporaryFor({ source: form.source.trim() });
      return { inspection, form, temporary };
    },
    onSuccess: ({ inspection, form, temporary }, { advance }) => {
      const proposed = reviewFor(inspection, temporary, review);
      setInspected({ inspection, for: form });
      setReview(proposed);
      setReviewErrors({});
      // Continue pressed while a paste was still being read goes on as soon as it is read.
      const pressed = pressedWhileReading.current;
      pressedWhileReading.current = false;
      if (advance || pressed) go(stepAfterSource(inspection, proposed));
    },
    onError: (error) => {
      pressedWhileReading.current = false;
      const refusal = refusalOf(error);
      if (refusal?.step === "source" && Object.keys(refusal.fields).length > 0) setSourceErrors(refusal.fields);
    },
  });

  // A source that is complete is read without being asked: pasted, chosen from a list or left, so
  // what was found is on screen before Continue is pressed. A directory on this server is read by
  // Continue alone.
  const [settleAsk, setSettleAsk] = useState(0);
  const [settled, setSettled] = useState(0);
  const settleSource = (): void => {
    setSettleAsk((count) => count + 1);
  };
  useEffect(() => {
    if (settleAsk === 0) return;
    // After the event that completed it: a paste is announced before its text is in the field.
    const timer = window.setTimeout(() => {
      setSettled((count) => count + 1);
    }, 80);
    return () => {
      window.clearTimeout(timer);
    };
  }, [settleAsk]);
  useEffect(() => {
    if (settled === 0) return;
    if (sourceMode !== "manual" && sourceMode !== "github" && sourceMode !== "gitlab") return;
    const problems = sourceProblems(source);
    if (problems.source !== undefined || problems.branch !== undefined) return;
    if (sourceKind(source.source) === "local") return;
    if (inspect.isPending) return;
    if (inspected !== null && sameSource(inspected.for, source) && review !== null) return;
    inspect.mutate({ form: source, advance: false });
    // Only a source that was settled reads it; every other value is read as it is when that happens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  const openRecipe = useMutation({
    mutationFn: async (summary: Pick<RecipeSummary, "name" | "title">) => {
      const full = await queryClient.query(recipeQuery(summary.name));
      // A recipe is named for itself: uptime-kuma.203-0-113-10.sslip.io.
      const temporary = await temporaryFor({ prefix: summary.name });
      return { full, temporary };
    },
    onSuccess: ({ full, temporary }) => {
      setRecipe(full);
      setRecipeForm((previous) => initialRecipeForm(full, previous, { temporary, ownDomains: context.ownDomains }));
      setRecipeErrors({});
      go("setup");
    },
  });

  // A recipe the link names is opened once, as if its card had been pressed; the catalog asking
  // again for another one over the wizard opens that one.
  const openedFromLink = useRef<string | null>(null);
  const openLinkedRecipe = openRecipe.mutate;
  useEffect(() => {
    if (linkedRecipe === undefined || openedFromLink.current === linkedRecipe) return;
    openedFromLink.current = linkedRecipe;
    openLinkedRecipe({ name: linkedRecipe, title: linkedRecipe });
  }, [linkedRecipe, openLinkedRecipe]);

  const queued = (accepted: { job?: unknown; job_id: string }, domain: string, message: string): void => {
    followedJob.follow(accepted.job === undefined ? accepted.job_id : (accepted.job as Job));
    void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    void queryClient.invalidateQueries({ queryKey: appKeys.list, exact: true });
    announce(message);
    setTarget({ domain, jobId: accepted.job_id });
  };

  const create = useMutation({
    mutationFn: (body: CreateAppBody) => request("post", "/api/apps", { body }),
    onSuccess: (accepted, body) => {
      queued(accepted, body.domain, t("newApp.page.queued", { domain: body.domain }));
    },
    onError: (error, body) => {
      const refusal = refusalOf(error);
      if (refusal === null) return;
      if (body.container?.method === "image" && refusal.fields["image"] !== undefined) {
        // The image is the source step's: what is wrong with it is said beside its field.
        setImageError(refusal.fields["image"]);
        go("source");
        focusFirstInvalid();
        return;
      }
      if (body.recipe) {
        // A recipe brings its own source and type: only its domain is the operator's to fix.
        if (refusal.fields["domain"] !== undefined) {
          setRecipeErrors({ domain: refusal.fields["domain"] });
          go("setup");
          focusFirstInvalid();
        }
      } else if (refusal.step === "source") {
        setSourceErrors(refusal.fields);
        go("source");
      } else if (Object.keys(refusal.fields).length > 0) {
        setReviewErrors(refusal.fields);
        go(refusal.step);
        focusFirstInvalid();
      }
    },
  });

  const importing = useMutation({
    mutationFn: (body: ImportAppBody) => importApp(body),
    onSuccess: (accepted, body) => {
      const domain = body.domain ?? normalizeDomain(body.document.app.domain);
      queued(accepted, domain, t("newApp.page.importQueued", { domain }));
    },
    onError: (error) => {
      const fields = importRefusalOf(error);
      if (fields === null) return;
      setImportErrors(fields);
      go("address");
    },
  });

  const chosen = kind === "recipe" ? recipe !== null : kind === "import" ? loaded !== null : sourceMode === "image" ? imageRef.trim() !== "" : sourceMode === "compose" ? composeDoc.document.trim() !== "" : source.source.trim() !== "";
  const dirty = chosen && target === null;
  const blocker = useBlocker({
    // Signing in again after the session expired is not leaving: nothing typed could be kept.
    shouldBlockFn: ({ next }) => dirty && !leaving.current && next.pathname !== "/login",
    enableBeforeUnload: () => dirty && !leaving.current,
    withResolver: true,
  });
  const onGone = useCallback(() => {
    leaving.current = true;
  }, []);
  const cancelInspection = (): void => {
    abort.current?.abort();
    inspect.reset();
  };

  // A temporary address is no DNS question: the service that answers it is the one that made it.
  const addressDomain =
    kind === "recipe" ? (recipeForm === null ? "" : dnsQuestion(recipeForm)) : kind === "import" ? (importForm?.domain ?? "") : review === null ? "" : dnsQuestion(review);
  const dns = useDomainDnsCheck(addressDomain);

  // An export, from a file or from another platform's server: checked the same way, then reviewed.
  const acceptExport = (name: string, text: string): void => {
    const read = readExport(text);
    if ("problem" in read) {
      setLoaded(null);
      setImportProblem(read.problem);
      return;
    }
    setLoaded({ fileName: name, document: read.document });
    setImportProblem(null);
    setImportForm(initialImportForm(read.document));
    setImportErrors({});
  };

  const readFile = (file: File): void => {
    const tooLarge = exportFileProblem(file);
    if (tooLarge !== null) {
      setLoaded(null);
      setImportProblem(tooLarge);
      return;
    }
    readText(file).then(
      (text) => {
        acceptExport(file.name, text);
      },
      () => {
        setLoaded(null);
        setImportProblem(translate(getLocale(), "newApp.importApp.unreadable"));
      },
    );
  };

  const deploy = (): void => {
    if (kind === "recipe") {
      if (recipe !== null && recipeForm !== null) create.mutate({ ...recipeBody(recipe, recipeForm), ...destination.body });
    } else if (kind === "import") {
      if (loaded !== null && importForm !== null) importing.mutate(importBody(loaded.document, importForm));
    } else if (review !== null && inspected !== null) {
      const body = createAppBody(inspected.for, review, inspected.inspection.platform_proposal ?? null, inspected.document !== undefined ? { composeDocument: inspected.document } : {});
      // The database is the operator's choice whether or not the step was passed over: the
      // choice stays "none" until it is opened.
      create.mutate({ ...(allSteps.includes("database") ? withDatabase(body, database) : body), ...destination.body });
    }
  };

  const createFailure =
    create.isError && (kind === "recipe" ? refusalOf(create.error)?.fields["domain"] === undefined : refusalOf(create.error) === null) ? create.error : null;
  const importFailure = importing.isError && importRefusalOf(importing.error) === null ? importing.error : null;

  // What is wrong with each kind's form, found fresh on every render so Continue can say it.
  const codeErrors = review !== null ? reviewProblems(review, context) : {};
  const requiredRecipeVariables = recipe !== null ? requiredVariables(recipe) : [];
  const recipeFound = recipeForm !== null ? recipeProblems(recipeForm, context.domains, requiredRecipeVariables) : {};
  const importFound = importForm !== null ? importProblems(importForm, context.domains) : {};

  /** The step's Back, when it has one, going to the step before for this start. */
  const back = (from: Step): WizardActionsProps["back"] => {
    const before = previousStep(kind, from, order);
    return before === null ? undefined : { onClick: () => go(before) };
  };

  /**
   * Continue for a step that only needs its own fields: the first problem is what it says when
   * pressed; the step's errors are shown on their fields and the first of them takes focus.
   */
  const checked = (
    from: Step,
    found: ReviewErrors,
    show: (update: (previous: ReviewErrors) => ReviewErrors) => void,
    extra?: () => void,
  ): WizardActionsProps => {
    const own = Object.fromEntries(errorsOf(found, from));
    const missing = missingOf(t, errorsOf(found, from));
    const after = nextStep(kind, from, order);
    const before = back(from);
    return {
      ...(before !== undefined ? { back: before } : {}),
      next: {
        onClick: () => {
          show((previous) => replaceStepErrors(previous, from, own));
          extra?.();
          if (after !== null) go(after);
        },
      },
      ...(missing !== undefined
        ? {
            missing,
            onMissing: () => {
              show((previous) => replaceStepErrors(previous, from, own));
              extra?.();
              focusFirstInvalid();
            },
          }
        : {}),
    };
  };

  // ---------------------------------------------------------------------------------------
  // The steps

  const sourceActions = (): WizardActionsProps => {
    if (sourceMode === "adopt") return adoption.actions;
    if (sourceMode === "compose") {
      // Two presses: look at the document, then go on with what was found, when nothing refuses it.
      const found = composeDoc.analysis;
      const empty = composeDoc.document.trim() === "";
      const focusField = (): void => document.getElementById(COMPOSE_DOCUMENT_FIELD)?.focus();
      if (!found?.deployable) {
        return {
          next: { label: t("newApp.compose.look"), loading: composeDoc.looking, onClick: composeDoc.look },
          ...(empty ? { missing: t("newApp.compose.empty"), onMissing: focusField } : {}),
        };
      }
      return {
        next: {
          label: t("newApp.source.continue"),
          onClick: () => {
            const inspection = composeInspection(found, composeDoc.made);
            inspect.reset();
            setInspected({ inspection, for: { source: "", branch: "" }, document: composeDoc.document });
            void (async () => {
              // A stack is named for nothing in particular: the temporary address is an application's.
              const proposed = reviewFor(inspection, await temporaryFor({ prefix: "stack" }), review);
              setReview(proposed);
              setReviewErrors({});
              go(stepAfterSource(inspection, proposed));
            })();
          },
        },
      };
    }
    if (sourceMode === "image") {
      // Nothing to inspect: the image is pulled when it is deployed, so Continue goes on to the
      // address with the container section started as an image's.
      const problem = imageProblem(imageRef);
      return {
        next: {
          label: t("newApp.source.continue"),
          onClick: () => {
            setImageError(undefined);
            const inspection = imageInspection();
            const reference = imageRef.trim();
            inspect.reset();
            setInspected({ inspection, for: { source: reference, branch: "" } });
            void (async () => {
              const proposed = reviewFor(inspection, await temporaryFor({ image: reference }), review, reference);
              setReview(proposed);
              setReviewErrors({});
              go(stepAfterSource(inspection, proposed));
            })();
          },
        },
        ...(problem !== null
          ? {
              missing: problem,
              onMissing: () => {
                setImageError(problem);
                focusFirstInvalid();
              },
            }
          : {}),
      };
    }
    if (kind === "recipe") {
      return {
        next: { onClick: () => go("setup") },
        ...(recipe === null
          ? {
              missing: t("newApp.source.chooseRecipe"),
              onMissing: () => document.querySelector<HTMLElement>('[data-slot="step"] li button')?.focus(),
            }
          : {}),
      };
    }
    if (kind === "import") {
      return {
        next: { onClick: () => go("address") },
        ...(loaded === null
          ? {
              missing: importProblem ?? t("newApp.source.chooseExport"),
              onMissing: () => document.getElementById(EXPORT_FILE_INPUT)?.focus(),
            }
          : {}),
      };
    }
    const found: SourceErrors =
      sourceMode === "github" && githubStatus?.configured === true && source.installationId === undefined
        ? { source: t("newApp.source.chooseRepository") }
        : sourceMode === "gitlab" && source.source.trim() === ""
          ? { source: t("gitlab.picker.chooseProject") }
          : sourceProblems(source);
    const missing = found.source ?? found.branch;
    const ready = inspected !== null && sameSource(inspected.for, source) && review !== null;
    return {
      next: {
        label: t("newApp.source.continue"),
        loading: inspect.isPending,
        onClick: () => {
          setSourceErrors({});
          // A source read already (on paste) goes straight on; one being read goes on when it is
          // read; one that was not is read now, and then goes on.
          if (ready) go(stepAfterSource(inspected.inspection, review));
          else if (inspect.isPending) pressedWhileReading.current = true;
          else inspect.mutate({ form: source, advance: true });
        },
      },
      ...(missing !== undefined
        ? {
            missing,
            onMissing: () => {
              setSourceErrors(found);
              focusFirstInvalid();
            },
          }
        : {}),
    };
  };

  // What was found in the source that is on screen, once it is read: the line under its fields.
  const detectedLine: ReactNode =
    kind === "code" &&
    (sourceMode === "manual" || sourceMode === "github" || sourceMode === "gitlab") &&
    inspected !== null &&
    inspected.document === undefined &&
    sameSource(inspected.for, source) &&
    review !== null ? (
      <DetectedLine inspection={inspected.inspection} types={typeList} onChange={() => go("configure")} />
    ) : null;

  const stepView = (shown: Step): StepView | null => {
    switch (shown) {
      case "server":
        return serverStep.view;
      case "source":
        return {
          title: t("newApp.source.heading"),
          description: t(
            sourceMode === "adopt"
              ? "newApp.source.introAdopt"
              : sourceMode === "compose"
                ? "newApp.source.introCompose"
                : sourceMode === "image"
                ? "containers.source.intro"
                : kind === "recipe"
                ? "newApp.source.introRecipe"
                : kind === "import"
                  ? "newApp.source.introImport"
                  : "newApp.source.intro",
          ),
          content: (
            <SourceStep
              form={source}
              errors={sourceErrors}
              onChange={(next) => {
                // The first edit settles where the source comes from, so the GitHub status
                // arriving late never swaps the field out from under the operator.
                setChosenMode(sourceMode);
                setSource(next);
                setSourceErrors({});
                if (inspect.isError) inspect.reset();
                // A repository chosen from a list is complete the moment it is chosen.
                if ((sourceMode === "github" || sourceMode === "gitlab") && next.source.trim() !== "" && next.source !== source.source) settleSource();
              }}
              onSettle={settleSource}
              detected={detectedLine}
              onSubmit={() => {
                const actions = sourceActions();
                if (actions.missing !== undefined) actions.onMissing?.();
                else actions.next.onClick();
              }}
              github={githubStatus}
              gitlab={gitlab.data ?? null}
              mode={sourceMode}
              onModeChange={(mode) => {
                setChosenMode(mode);
                // GitHub and a typed source are two spellings of the same field: switching
                // between them starts it over. A recipe or an export keeps its own state.
                // An image has a field of its own, so going to it and back loses nothing.
                if (kindOf(mode) === "code" && mode !== "image" && sourceMode !== "image" && mode !== "compose" && sourceMode !== "compose") {
                  setSource({ source: "", branch: "" });
                  setSourceErrors({});
                }
                setImageError(undefined);
                inspect.reset();
                openRecipe.reset();
              }}
              image={{
                value: imageRef,
                error: imageError,
                onChange: (value) => {
                  setImageRef(value);
                  setImageError(undefined);
                },
              }}
              inspecting={inspect.isPending ? { since: inspect.submittedAt } : null}
              onCancel={cancelInspection}
              failure={inspect.isError && !isAbort(inspect.error) ? inspect.error : null}
              onManual={() => {
                const inspection = manualInspection(source);
                inspect.reset();
                setInspected({ inspection, for: source });
                void (async () => {
                  const proposed = reviewFor(inspection, await temporaryFor({ source: source.source.trim() }), review);
                  setReview(proposed);
                  setReviewErrors({});
                  go(stepAfterSource(inspection, proposed));
                })();
              }}
              recipes={
                <RecipeGallery
                  opening={openRecipe.isPending ? openRecipe.variables.name : null}
                  failure={openRecipe.isError ? { title: t("newApp.recipes.openFailed", { title: openRecipe.variables.title }), error: openRecipe.error } : null}
                  onChoose={(summary) => {
                    // The same recipe again: what was set over it stays, nothing is read twice.
                    if (recipe !== null && recipe.name === summary.name && recipeForm !== null) {
                      go("setup");
                      return;
                    }
                    openRecipe.mutate(summary);
                  }}
                />
              }
              importer={<ImportFile loaded={loaded} problem={importProblem} types={typeList} onFile={readFile} onRemote={acceptExport} />}
              composer={composeDoc.content}
              adopter={adoption.content}
            />
          ),
          actions: sourceActions(),
        };

      case "address": {
        const title = t("newApp.steps.address");
        const description = t("newApp.review.addressDescription");
        if (kind === "import" && loaded !== null && importForm !== null) {
          return {
            title,
            description: t("newApp.importApp.reviewIntro"),
            content: (
              <ImportAddress
                document={loaded.document}
                types={typeList}
                form={importForm}
                errors={importErrors}
                dns={dns}
                onChange={(next) => {
                  setImportForm(next);
                  setImportErrors((previous) => revalidated(previous, "address", importProblems(next, context.domains)));
                }}
              />
            ),
            actions: checked("address", importFound, setImportErrors, dns.checkNow),
          };
        }
        if (kind === "code" && review !== null) {
          return {
            title,
            description,
            content: (
              <div className="flex flex-col gap-6">
                <AddressFields
                  value={review}
                  onChange={(patch) => {
                    const next = { ...review, ...patch };
                    setReview(next);
                    setReviewErrors((previous) => revalidated(previous, "address", reviewProblems(next, context)));
                  }}
                  error={reviewErrors["domain"]}
                  dns={dns}
                />
                <DestinationFields destination={destination} />
              </div>
            ),
            actions: checked("address", codeErrors, setReviewErrors, dns.checkNow),
          };
        }
        return null;
      }

      case "configure":
        if (kind !== "code" || inspected === null || review === null) return null;
        return {
          title: t("newApp.steps.configure"),
          description: t("newApp.review.intro"),
          content: (
            <ConfigureStep
              taken={context.ports}
              cores={cores}
              inspection={inspected.inspection}
              types={typeList}
              source={inspected.for.source.trim()}
              form={review}
              errors={reviewErrors}
              onChange={(next) => {
                setReview(next);
                setReviewErrors((previous) => revalidated(previous, "configure", reviewProblems(next, context)));
              }}
            />
          ),
          actions: checked("configure", codeErrors, setReviewErrors),
        };

      case "setup": {
        // A recipe's one step: where it answers, the variables it cannot run without, and a
        // Deploy of its own. Pressed with something missing it says what, as every step's Continue
        // does, and takes the operator to the field.
        if (kind !== "recipe" || recipe === null || recipeForm === null) return null;
        const missingNames = requiredRecipeVariables.filter((name) => recipeFound[recipeVariableField(name)] !== undefined);
        const [firstMissing] = missingNames;
        const domainProblem = recipeFound["domain"];
        let missing: ReactNode;
        if (domainProblem !== undefined) missing = domainProblem;
        else if (firstMissing !== undefined) {
          const link = (
            <button
              type="button"
              aria-label={t("newApp.recipes.goTo", { name: firstMissing })}
              onClick={() => focusRecipeVariable(firstMissing)}
              className={textLinkClassName("inline", "cursor-pointer")}
            >
              <Mono>{firstMissing}</Mono>
            </button>
          );
          missing =
            missingNames.length === 1
              ? t.rich("newApp.recipes.missingOne", { name: link })
              : t.rich("newApp.recipes.missingMany", { count: missingNames.length, name: link });
        }
        const before = back("setup");
        return {
          title: t("newApp.steps.setup"),
          description: t("newApp.recipes.setupIntro", { title: recipe.title }),
          content: (
            <div className="flex flex-col gap-6">
              <RecipeFacts recipe={recipe} types={typeList} />
              <AddressFields
                value={recipeForm}
                onChange={(patch) => {
                  const next = { ...recipeForm, ...patch };
                  setRecipeForm(next);
                  setRecipeErrors((previous) => revalidated(previous, "setup", recipeProblems(next, context.domains, requiredRecipeVariables), "recipe"));
                }}
                error={recipeErrors["domain"]}
                dns={dns}
              />
              <DestinationFields destination={destination} />
              <RecipeVariables
                recipe={recipe}
                form={recipeForm}
                errors={recipeErrors}
                onChange={(next) => {
                  setRecipeForm(next);
                  setRecipeErrors((previous) => revalidated(previous, "setup", recipeProblems(next, context.domains, requiredRecipeVariables), "recipe"));
                }}
              />
            </div>
          ),
          actions: {
            ...(before !== undefined ? { back: before } : {}),
            // Deploy goes ahead on the next step, where the job is followed to its end.
            next: {
              label: t("newApp.recipes.deploy"),
              onClick: () => {
                setRecipeErrors({});
                dns.checkNow();
                go("deploy");
                deploy();
              },
            },
            ...(missing !== undefined
              ? {
                  missing,
                  onMissing: () => {
                    setRecipeErrors((previous) => replaceStepErrors(previous, "setup", recipeFound, "recipe"));
                    dns.checkNow();
                    focusFirstInvalid();
                  },
                }
              : {}),
          },
        };
      }

      case "variables":
        if (kind === "import" && importForm !== null) {
          return {
            title: t("newApp.steps.variables"),
            description: t("newApp.importApp.secretsDescription"),
            content: (
              <ImportSecrets
                form={importForm}
                errors={importErrors}
                onChange={(next) => {
                  setImportForm(next);
                  setImportErrors((previous) => revalidated(previous, "variables", importProblems(next, context.domains)));
                }}
              />
            ),
            actions: checked("variables", importFound, setImportErrors),
          };
        }
        if (kind === "code" && review !== null) {
          return {
            title: t("newApp.steps.variables"),
            description: t("newApp.review.environmentDescription"),
            content: (
              <EnvironmentFields
                rows={review.env}
                errors={reviewErrors}
                onChange={(env) => {
                  const next = { ...review, env };
                  setReview(next);
                  setReviewErrors((previous) => revalidated(previous, "variables", reviewProblems(next, context)));
                }}
              />
            ),
            actions: checked("variables", codeErrors, setReviewErrors, () => {
              create.reset();
            }),
          };
        }
        return null;

      case "database": {
        if (kind !== "code" || review === null || !allSteps.includes("database")) return null;
        const before = back("database");
        return {
          title: t("databases.wizard.title"),
          description: t("databases.wizard.description"),
          content: <DatabaseStep domain={normalizeDomain(review.domain)} value={database} onChange={setDatabase} />,
          actions: { ...(before !== undefined ? { back: before } : {}), next: { onClick: () => go("deploy") } },
        };
      }

      case "deploy": {
        let summary: readonly KeyValueItem[] | null = null;
        let details: readonly KeyValueItem[] = [];
        let domain = "";
        if (kind === "code" && inspected !== null && review !== null) {
          // A step that had nothing to ask was passed over: the summary says so and offers to open it.
          const open: OpenSteps = {
            ...(passedOver.includes("address") ? { address: () => go("address") } : {}),
            ...(passedOver.includes("configure") ? { configure: () => go("configure") } : {}),
            ...(passedOver.includes("variables") ? { variables: () => go("variables") } : {}),
          };
          const made = deploySummary(t, inspected.for, inspected.inspection, typeList, review, open);
          summary = made.items;
          details = made.details;
          if (allSteps.includes("database")) summary = [...summary, databaseSummary(t, database, undefined)];
          if (destination.needed) summary = [...summary, placeSummary(t, destination)];
          domain = review.domain;
        } else if (kind === "recipe" && recipe !== null && recipeForm !== null) {
          const made = recipeSummary(t, recipe, recipeForm);
          summary = destination.needed ? [...made.items, placeSummary(t, destination)] : made.items;
          details = made.details;
          domain = recipeForm.domain;
        } else if (kind === "import" && loaded !== null && importForm !== null) {
          summary = importSummary(t, loaded.document, importForm, typeList);
          domain = importForm.domain;
        }
        if (summary === null) return null;
        const normal = normalizeDomain(domain);
        const job = followedJob.job;
        const finished = job !== null && isJobFinished(job);
        const failed = target !== null && finished && job.status !== "completed";
        const done = target !== null && finished && job.status === "completed" && kind !== "code";
        const deploying = create.isPending || importing.isPending || (target !== null && !finished);
        const importKind = kind === "import";
        const deployKind: DeployKind = kind;
        let next: WizardActionsProps["next"];
        if (done) {
          next = { label: t("newApp.deploy.openApp", { domain: normal }), onClick: () => void navigate({ to: "/apps/$domain", params: { domain: normal } }) };
        } else if (failed) {
          next = {
            label: t("newApp.deploy.again"),
            onClick: () => {
              setTarget(null);
              followedJob.dismiss();
              deploy();
            },
          };
        } else {
          next = { label: t(importKind ? "newApp.deploy.importAction" : "newApp.deploy.action", { domain: normal }), loading: deploying, onClick: deploy };
        }
        const backTo = previousStep(kind, "deploy", order);
        return {
          title: t("newApp.deploy.heading"),
          description: t(
            kind === "recipe"
              ? "newApp.deploy.introRecipe"
              : importKind
                ? "newApp.deploy.introImport"
                : // Steps that had nothing to ask were passed over: say so, and where they are.
                  passedOver.length > 0
                  ? "newApp.deploy.introDetected"
                  : "newApp.deploy.intro",
          ),
          content: (
            <DeployStep
              kind={deployKind}
              domain={domain}
              summary={summary}
              details={details}
              failure={kind === "import" ? importFailure : createFailure}
              target={target}
              followedJob={followedJob}
              onGone={onGone}
            />
          ),
          actions: {
            ...(backTo !== null && !deploying && !done
              ? {
                  back: {
                    onClick: () => {
                      setTarget(null);
                      followedJob.dismiss();
                      create.reset();
                      importing.reset();
                      go(backTo);
                    },
                  },
                }
              : {}),
            next,
          },
        };
      }
    }
  };

  // A step whose data is gone (a start switched under it) shows the first one instead.
  const shownView = stepView(step);
  const current: Step = shownView === null ? "source" : step;
  const view = shownView ?? stepView("source");

  const locked = inspect.isPending || openRecipe.isPending || create.isPending || importing.isPending || target !== null;

  return (
    <>
      <Wizard
        header={{
          title: t("newApp.page.title"),
          description: t("newApp.page.description"),
          breadcrumbs: [{ label: t("newApp.page.breadcrumb"), to: "/apps" }],
          server: node,
        }}
        steps={steps.map((id) => ({ id, label: t(STEP_LABELS[id]) }))}
        current={current}
        {...(locked ? {} : { onSelectStep: (id: string) => go(id as Step) })}
        title={view?.title ?? ""}
        description={view?.description}
        {...(current !== "server" && current !== "source" && current !== "setup" && current !== "deploy" ? { summary: <SoFar t={t} kind={kind} source={inspected?.for ?? source} review={review} recipe={recipe} recipeForm={recipeForm} loaded={loaded} importForm={importForm} typeName={(type) => typeName(typeList, type)} /> } : {})}
        actions={view?.actions ?? { next: { onClick: () => undefined } }}
      >
        {view?.content}
      </Wizard>

      <Dialog
        open={blocker.status === "blocked"}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.();
        }}
        size="sm"
        title={t("newApp.leave.title")}
        description={t("newApp.leave.description")}
        footer={
          <>
            <Button onClick={() => blocker.reset?.()}>{t("newApp.leave.stay")}</Button>
            <Button variant="danger" onClick={() => blocker.proceed?.()}>
              {t("newApp.leave.leave")}
            </Button>
          </>
        }
      />
    </>
  );
}

/**
 * What has been decided so far, beside the step on a wide screen and under it on a narrow one:
 * where the code comes from, where it answers, and what it runs as.
 */
function SoFar({
  t,
  kind,
  source,
  review,
  recipe,
  recipeForm,
  loaded,
  importForm,
  typeName: nameOf,
}: {
  t: T;
  kind: StartKind;
  source: SourceForm;
  review: ReviewForm | null;
  recipe: Recipe | null;
  recipeForm: RecipeForm | null;
  loaded: LoadedExport | null;
  importForm: ImportForm | null;
  typeName: (type: string) => string;
}) {
  const items: KeyValueItem[] = [];
  const domain = kind === "recipe" ? recipeForm?.domain : kind === "import" ? importForm?.domain : review?.domain;
  if (kind === "recipe" && recipe !== null) items.push({ label: t("newApp.recipes.recipe"), value: recipe.title, mono: false, copy: false });
  if (kind === "import" && loaded !== null) items.push({ label: t("newApp.importApp.file"), value: loaded.fileName, copy: false });
  // An image is named whole: its owner, name and tag read as one thing, not as a path to shorten.
  const image = review !== null && review.appType === "container" && review.container.method === "image" ? review.container.image : null;
  if (kind === "code") items.push({ label: t("newApp.deploy.source"), value: image ?? shortSource(source.source), copy: false });
  items.push({ label: t("newApp.deploy.address"), value: domain !== undefined && domain.trim() !== "" ? normalizeDomain(domain) : null, copy: false });
  if (kind === "code" && review !== null) {
    items.push({ label: t("newApp.deploy.type"), value: review.appType === "" ? null : nameOf(review.appType), mono: false, copy: false });
  }
  // Stacked, not side by side: the summary's column is narrow beside the step.
  return (
    <Card padding="sm" title={t("newApp.page.soFar")}>
      <dl className="flex min-w-0 flex-col gap-3">
        {items.map((item) => (
          <div key={item.label} className="flex min-w-0 flex-col gap-0.5">
            <dt className="text-12 text-fg-muted">{item.label}</dt>
            <dd className="min-w-0 text-13 text-fg">
              {item.value === null || item.value === undefined ? (
                <span className="text-fg-muted">{t("newApp.page.notYet")}</span>
              ) : item.mono === false ? (
                <span className="block truncate">{item.value}</span>
              ) : (
                <Mono truncate>{item.value}</Mono>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}
