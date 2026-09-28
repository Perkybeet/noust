import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useBlocker } from "@tanstack/react-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { request } from "../../api/client";
import { importApp } from "../../api/queries/appImport";
import type { ImportAppBody } from "../../api/queries/appImport";
import { appKeys, appsQuery, appTypesQuery } from "../../api/queries/apps";
import { githubStatusQuery } from "../../api/queries/github";
import { webserverQuery } from "../../api/queries/config";
import { jobKeys, useFollowedJob } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { recipeQuery } from "../../api/queries/recipes";
import type { Recipe, RecipeSummary } from "../../api/queries/recipes";
import { systemInfoQuery } from "../../api/queries/system";
import { announce } from "../../app/Announcer";
import { getLocale } from "../../app/locale";
import { PageHeader } from "../../app/PageHeader";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { translate, useT } from "../../i18n";
import { normalizeDomain } from "../domains/names";
import { DeployStep, deploySummary } from "./DeployStep";
import type { DeployKind } from "./DeployStep";
import { exportFileProblem, importBody, importProblems, importRefusalOf, initialImportForm, readExport } from "./exportFile";
import type { ImportForm } from "./exportFile";
import { ImportFile } from "./ImportFile";
import type { LoadedExport } from "./ImportFile";
import { ImportReview, importSummary } from "./ImportReview";
import { RecipeGallery } from "./RecipeGallery";
import { RecipeReview, recipeSummary } from "./RecipeReview";
import { initialRecipeForm, recipeBody, recipeProblems } from "./recipe";
import type { RecipeForm } from "./recipe";
import { ReviewStep } from "./ReviewStep";
import { SourceStep } from "./SourceStep";
import type { SourceMode } from "./SourceStep";
import { StepRail } from "./StepRail";
import type { LandingTarget } from "./useDeploymentLanding";
import { STEPS, createAppBody, initialReview, inspectBody, manualInspection, refusalOf, reviewProblems, sameSource, shortSource, sourceProblems } from "./wizard";
import type { CreateAppBody, Inspection, ReviewErrors, ReviewForm, SourceErrors, SourceForm, Step, WebServer } from "./wizard";

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/** The first field that needs attention takes focus; its message is read with it. */
function focusFirstInvalid(): void {
  requestAnimationFrame(() => {
    document.querySelector<HTMLElement>("main form [aria-invalid='true']")?.focus();
  });
}

function kindOf(mode: SourceMode): DeployKind {
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

/**
 * The new-app wizard: where the code is (a repository or a directory, a recipe, or another
 * server's export), what WASM found in it (every part editable), and the deploy. A deploy from
 * code hands over to the deployment page as soon as the build starts; a recipe or an import is
 * followed here to its end. What the operator typed survives going back and forth between the
 * steps and is never asked twice.
 */
export function NewAppWizard() {
  const t = useT();
  const queryClient = useQueryClient();
  const apps = useQuery(appsQuery());
  const webserver = useQuery(webserverQuery());
  const types = useQuery(appTypesQuery());
  const system = useQuery({ ...systemInfoQuery(), staleTime: 10 * 60_000, refetchOnWindowFocus: false });
  const followedJob = useFollowedJob();
  // Only decides whether "From GitHub" is offered: a failure leaves the typed source, as before.
  const github = useQuery({ ...githubStatusQuery(), retry: false });

  const [step, setStep] = useState<Step>("source");
  const [source, setSource] = useState<SourceForm>({ source: "", branch: "" });
  // Null until the operator acts: GitHub when the App is connected, the typed source otherwise.
  const [chosenMode, setChosenMode] = useState<SourceMode | null>(null);
  const [sourceErrors, setSourceErrors] = useState<SourceErrors>({});
  const [inspected, setInspected] = useState<{ inspection: Inspection; for: SourceForm } | null>(null);
  const [review, setReview] = useState<ReviewForm | null>(null);
  const [reviewErrors, setReviewErrors] = useState<ReviewErrors>({});
  const [inspectingSince, setInspectingSince] = useState<number | null>(null);
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

  const heading = useRef<HTMLHeadingElement>(null);
  const moved = useRef(false);
  const leaving = useRef(false);
  const abort = useRef<AbortController | null>(null);

  const go = (next: Step): void => {
    moved.current = true;
    setStep(next);
    const index = STEPS.findIndex((entry) => entry.id === next);
    const entry = STEPS[index];
    announce(t("newApp.steps.announce", { number: String(index + 1), total: String(STEPS.length), label: entry ? t(entry.label) : next }));
  };

  // A new step starts at its heading, so the next Tab and a screen reader begin there.
  useEffect(() => {
    if (!moved.current) return;
    heading.current?.focus({ preventScroll: false });
    heading.current?.scrollIntoView({ block: "nearest" });
  }, [step]);

  const cores = system.data?.cpu.cores ?? null;
  const context = useMemo(() => {
    const list = apps.data?.apps ?? [];
    return {
      domains: new Set(list.map((app) => app.domain)),
      ports: new Map(list.filter((app) => app.port !== null && app.port !== undefined).map((app) => [Number(app.port), app.domain])),
      cores,
    };
  }, [apps.data, cores]);

  const githubStatus = github.data ?? null;
  const sourceMode: SourceMode = chosenMode ?? (githubStatus?.configured === true ? "github" : "manual");
  const kind = kindOf(sourceMode);
  const typeList = types.data?.types ?? [];

  const defaultWebserver: WebServer = webserver.data?.webserver === "apache" ? "apache" : "nginx";

  const inspect = useMutation({
    mutationFn: async (form: SourceForm) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      const inspection = await request("post", "/api/apps/inspect", { body: inspectBody(form), signal: controller.signal });
      return { inspection, form };
    },
    onMutate: () => {
      setInspectingSince(Date.now());
    },
    onSettled: () => {
      setInspectingSince(null);
    },
    onSuccess: ({ inspection, form }) => {
      setInspected({ inspection, for: form });
      setReview((previous) => initialReview(inspection, { webserver: defaultWebserver, taken: context.ports }, previous));
      setReviewErrors({});
      go("review");
    },
    onError: (error) => {
      const refusal = refusalOf(error);
      if (refusal?.step === "source" && Object.keys(refusal.fields).length > 0) setSourceErrors(refusal.fields);
    },
  });

  const openRecipe = useMutation({
    mutationFn: (summary: RecipeSummary) => queryClient.query(recipeQuery(summary.name)),
    onSuccess: (full) => {
      setRecipe(full);
      setRecipeForm((previous) => initialRecipeForm(full, previous));
      setRecipeErrors({});
      go("review");
    },
  });

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
      if (body.recipe) {
        // A recipe brings its own source and type: only its domain is the operator's to fix.
        if (refusal.fields["domain"] !== undefined) {
          setRecipeErrors({ domain: refusal.fields["domain"] });
          go("review");
        }
      } else if (refusal.step === "source") {
        setSourceErrors(refusal.fields);
        go("source");
      } else if (Object.keys(refusal.fields).length > 0) {
        setReviewErrors(refusal.fields);
        go("review");
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
      go("review");
    },
  });

  const chosen = kind === "recipe" ? recipe !== null : kind === "import" ? loaded !== null : source.source.trim() !== "";
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

  const submitSource = (): void => {
    const errors: SourceErrors =
      sourceMode === "github" && githubStatus?.configured === true && source.installationId === undefined
        ? { source: t("newApp.source.chooseRepository") }
        : sourceProblems(source);
    setSourceErrors(errors);
    if (Object.keys(errors).length > 0) return;
    if (inspected !== null && sameSource(inspected.for, source) && review !== null) {
      go("review");
      return;
    }
    inspect.mutate(source);
  };

  const submitReview = (): void => {
    if (review === null) return;
    const errors = reviewProblems(review, context);
    setReviewErrors(errors);
    if (Object.keys(errors).length > 0) {
      focusFirstInvalid();
      return;
    }
    create.reset();
    go("deploy");
  };

  const submitRecipe = (): void => {
    if (recipeForm === null) return;
    const errors = recipeProblems(recipeForm, context.domains);
    setRecipeErrors(errors);
    if (Object.keys(errors).length > 0) {
      focusFirstInvalid();
      return;
    }
    create.reset();
    go("deploy");
  };

  const submitImport = (): void => {
    if (importForm === null) return;
    const errors = importProblems(importForm, context.domains);
    setImportErrors(errors);
    if (Object.keys(errors).length > 0) {
      focusFirstInvalid();
      return;
    }
    importing.reset();
    go("deploy");
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
        const read = readExport(text);
        if ("problem" in read) {
          setLoaded(null);
          setImportProblem(read.problem);
          return;
        }
        setLoaded({ fileName: file.name, document: read.document });
        setImportProblem(null);
        setImportForm(initialImportForm(read.document));
        setImportErrors({});
      },
      () => {
        setLoaded(null);
        setImportProblem(translate(getLocale(), "newApp.importApp.unreadable"));
      },
    );
  };

  const deploy = (): void => {
    if (kind === "recipe") {
      if (recipe !== null && recipeForm !== null) create.mutate(recipeBody(recipe, recipeForm));
    } else if (kind === "import") {
      if (loaded !== null && importForm !== null) importing.mutate(importBody(loaded.document, importForm));
    } else if (review !== null && inspected !== null) {
      create.mutate(createAppBody(inspected.for, review, inspected.inspection.platform_proposal ?? null));
    }
  };

  const reviewedDomain = kind === "recipe" ? recipeForm?.domain : kind === "import" ? importForm?.domain : review?.domain;
  const notes: Partial<Record<Step, string>> = {
    source: kind === "recipe" ? (recipe?.title ?? "") : kind === "import" ? (loaded?.fileName ?? "") : shortSource(inspected?.for.source ?? source.source),
    review: reviewedDomain ? normalizeDomain(reviewedDomain) : "",
  };

  const createFailure =
    create.isError && (kind === "recipe" ? refusalOf(create.error)?.fields["domain"] === undefined : refusalOf(create.error) === null) ? create.error : null;
  const importFailure = importing.isError && importRefusalOf(importing.error) === null ? importing.error : null;

  const deployProps = {
    onDeploy: deploy,
    deploying: create.isPending || importing.isPending,
    target,
    followedJob,
    onBack: () => {
      setTarget(null);
      followedJob.dismiss();
      create.reset();
      importing.reset();
      go("review");
    },
    onGone,
    headingRef: heading,
  };

  let deployStep = null;
  if (step === "deploy") {
    if (kind === "code" && inspected !== null && review !== null) {
      deployStep = (
        <DeployStep
          kind="code"
          domain={review.domain}
          summary={deploySummary(t, inspected.for, inspected.inspection, typeList, review)}
          failure={createFailure}
          {...deployProps}
        />
      );
    } else if (kind === "recipe" && recipe !== null && recipeForm !== null) {
      deployStep = (
        <DeployStep kind="recipe" domain={recipeForm.domain} summary={recipeSummary(t, recipe, recipeForm)} failure={createFailure} {...deployProps} />
      );
    } else if (kind === "import" && loaded !== null && importForm !== null) {
      deployStep = (
        <DeployStep
          kind="import"
          domain={importForm.domain}
          summary={importSummary(t, loaded.document, importForm, typeList)}
          failure={importFailure}
          {...deployProps}
        />
      );
    }
  }

  return (
    <>
      <PageHeader
        title={t("newApp.page.title")}
        description={t("newApp.page.description")}
        breadcrumbs={[{ label: t("newApp.page.breadcrumb"), to: "/apps" }]}
      />
      <div className="grid min-w-0 gap-6 lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-10">
        <div className="min-w-0 lg:sticky lg:top-20 lg:self-start">
          <StepRail
            current={step}
            notes={notes}
            locked={inspect.isPending || openRecipe.isPending || create.isPending || importing.isPending || target !== null}
            onGoTo={(next) => {
              go(next);
            }}
          />
        </div>
        <div className="min-w-0 max-w-[46rem]">
          {step === "source" ? (
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
              }}
              github={githubStatus}
              mode={sourceMode}
              onModeChange={(mode) => {
                setChosenMode(mode);
                // GitHub and a typed source are two spellings of the same field: switching
                // between them starts it over. A recipe or an export keeps its own state.
                if (kindOf(mode) === "code") {
                  setSource({ source: "", branch: "" });
                  setSourceErrors({});
                }
                inspect.reset();
                openRecipe.reset();
              }}
              onSubmit={submitSource}
              inspecting={inspectingSince === null ? null : { since: inspectingSince }}
              onCancel={() => {
                abort.current?.abort();
                inspect.reset();
              }}
              failure={inspect.isError && !isAbort(inspect.error) ? inspect.error : null}
              inspected={inspected !== null && sameSource(inspected.for, source)}
              onInspectAgain={() => inspect.mutate(source)}
              onManual={() => {
                const inspection = manualInspection(source);
                inspect.reset();
                setInspected({ inspection, for: source });
                setReview((previous) => initialReview(inspection, { webserver: defaultWebserver, taken: context.ports }, previous));
                setReviewErrors({});
                go("review");
              }}
              headingRef={heading}
              recipes={
                <RecipeGallery
                  opening={openRecipe.isPending ? openRecipe.variables.name : null}
                  failure={
                    openRecipe.isError
                      ? { title: t("newApp.recipes.openFailed", { title: openRecipe.variables.title }), error: openRecipe.error }
                      : null
                  }
                  onChoose={(summary) => {
                    // The same recipe again: what was set over it stays, nothing is read twice.
                    if (recipe !== null && recipe.name === summary.name && recipeForm !== null) {
                      go("review");
                      return;
                    }
                    openRecipe.mutate(summary);
                  }}
                />
              }
              importer={
                <ImportFile loaded={loaded} problem={importProblem} types={typeList} onFile={readFile} onContinue={() => go("review")} />
              }
            />
          ) : null}
          {step === "review" && kind === "code" && inspected !== null && review !== null ? (
            <ReviewStep
              taken={context.ports}
              cores={cores}
              inspection={inspected.inspection}
              types={typeList}
              source={inspected.for.source.trim()}
              form={review}
              errors={reviewErrors}
              onChange={(next) => {
                setReview(next);
                if (Object.keys(reviewErrors).length > 0) setReviewErrors(reviewProblems(next, context));
              }}
              onBack={() => go("source")}
              onContinue={submitReview}
              headingRef={heading}
            />
          ) : null}
          {step === "review" && kind === "recipe" && recipe !== null && recipeForm !== null ? (
            <RecipeReview
              recipe={recipe}
              types={typeList}
              form={recipeForm}
              errors={recipeErrors}
              onChange={(next) => {
                setRecipeForm(next);
                if (Object.keys(recipeErrors).length > 0) setRecipeErrors(recipeProblems(next, context.domains));
              }}
              onBack={() => go("source")}
              onContinue={submitRecipe}
              headingRef={heading}
            />
          ) : null}
          {step === "review" && kind === "import" && loaded !== null && importForm !== null ? (
            <ImportReview
              document={loaded.document}
              types={typeList}
              form={importForm}
              errors={importErrors}
              onChange={(next) => {
                setImportForm(next);
                if (Object.keys(importErrors).length > 0) setImportErrors(importProblems(next, context.domains));
              }}
              onBack={() => go("source")}
              onContinue={submitImport}
              headingRef={heading}
            />
          ) : null}
          {deployStep}
        </div>
      </div>

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
