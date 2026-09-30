import { useId, useMemo, useState } from "react";

import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { Field } from "../../../components/ui/Field";
import { Textarea } from "../../../components/ui/Textarea";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import type { DraftOp, EnvMap } from "./draft";
import { nameProblem, parseDotenv, valueProblem } from "./dotenv";

export type PasteMode = "merge" | "replace";

interface Problem {
  /** Blocks staging: the API would refuse the variables. */
  blocking: boolean;
  text: string;
}

/** What is wrong with the pasted text, line by line, in the order the lines come. */
function problemsOf(text: string, t: T): { problems: Problem[]; count: number; parsed: ReturnType<typeof parseDotenv> } {
  const parsed = parseDotenv(text);
  const problems: Problem[] = [];
  const lines = new Map<string, number[]>();
  for (const assignment of parsed.assignments) {
    lines.set(assignment.name, [...(lines.get(assignment.name) ?? []), assignment.line]);
    const name = nameProblem(assignment.name, t.locale);
    if (name !== null) problems.push({ blocking: true, text: t("environment.pasteDialog.lineProblem", { line: assignment.line, problem: name }) });
    const value = valueProblem(assignment.value, t.locale);
    if (value !== null) problems.push({ blocking: true, text: t("environment.pasteDialog.lineProblem", { line: assignment.line, problem: value }) });
  }
  for (const [name, at] of lines) {
    if (at.length > 1 && nameProblem(name, t.locale) === null) {
      const list = at.map(String);
      problems.push({
        blocking: false,
        text: t("environment.pasteDialog.duplicateLines", {
          name,
          firstLines: list.slice(0, -1).join(", "),
          lastLine: list.at(-1) ?? "",
        }),
      });
    }
  }
  for (const skipped of parsed.skipped) {
    problems.push({ blocking: false, text: t("environment.pasteDialog.skippedLine", { line: skipped.line }) });
  }
  return { problems, count: parsed.variables.size, parsed };
}

export interface PasteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The variables the file holds now (masked), to say what each pasted one does. */
  current: EnvMap;
  onStage: (ops: DraftOp[], summary: string) => void;
}

/**
 * A whole `.env` pasted at once, parsed live the way Noust reads the file on disk, with what
 * is wrong said line by line before anything is staged.
 */
export function PasteDialog({ open, onOpenChange, current, onStage }: PasteDialogProps) {
  const t = useT();
  const formId = useId();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<PasteMode>("merge");
  const { problems, count, parsed } = useMemo(() => problemsOf(text, t), [text, t]);
  const blocking = problems.some((problem) => problem.blocking);

  const removals = mode === "replace" ? [...current.keys()].filter((name) => !parsed.variables.has(name)).length : 0;

  const close = (next: boolean): void => {
    onOpenChange(next);
    if (!next) {
      setText("");
      setMode("merge");
    }
  };

  const stage = (): void => {
    if (count === 0 || blocking) return;
    const ops: DraftOp[] =
      mode === "replace"
        ? [{ kind: "replace", variables: new Map(parsed.variables) }]
        : [...parsed.variables].map(([name, value]) => ({ kind: "set", name, value }));
    const summary = t(mode === "replace" ? "environment.pasteDialog.stagedSummaryReplace" : "environment.pasteDialog.stagedSummary", { count });
    onStage(ops, summary);
    close(false);
  };

  const modes: readonly { value: PasteMode; label: string }[] = [
    { value: "merge", label: t("environment.pasteDialog.modeMerge") },
    { value: "replace", label: t("environment.pasteDialog.modeReplace") },
  ];

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="lg"
      title={t("environment.pasteDialog.title")}
      description={t("environment.pasteDialog.description")}
      footer={
        <>
          <Button onClick={() => close(false)}>{t("environment.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary" disabled={count === 0 || blocking}>
            {count === 0 ? t("environment.pasteDialog.stageEmpty") : t("environment.pasteDialog.stage", { count })}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        onSubmit={(event) => {
          event.preventDefault();
          stage();
        }}
        className="flex flex-col gap-4"
      >
        {/* Side by side from the small breakpoint: the text and what it parses to. */}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("environment.pasteDialog.contentsLabel")}>
            <Textarea
              mono
              rows={11}
              wrap="off"
              autoComplete="off"
              spellCheck={false}
              placeholder={"NODE_ENV=production\nDATABASE_URL=postgres://app:secret@127.0.0.1:5432/app"}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
              className="h-60 [&_textarea]:h-full [&_textarea]:resize-none"
            />
          </Field>

          <section aria-labelledby={`${formId}-preview`} className="flex min-w-0 flex-col gap-1.5">
            <p id={`${formId}-preview`} className="text-13 font-medium text-fg">
              {count === 0 ? t("environment.pasteDialog.parsedHeading") : t("environment.pasteDialog.foundCount", { count })}
            </p>
            <div
              role="region"
              aria-label={t("environment.pasteDialog.previewRegionAria")}
              tabIndex={0}
              className="flex h-60 flex-col gap-2 overflow-y-auto rounded-control border border-border bg-bg-sunken p-2 scroll-thin -outline-offset-2"
            >
              {count === 0 && problems.length === 0 ? (
                <p className="px-1 py-1 text-12 text-fg-faint">{t("environment.pasteDialog.emptyHint")}</p>
              ) : null}
              {problems.length > 0 ? (
                <ul className="flex flex-col gap-1.5 px-1 pt-0.5">
                  {problems.map((problem, index) => (
                    <li key={index} className="flex items-start gap-1.5 text-12">
                      {problem.blocking ? (
                        <ICONS.error aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fail" />
                      ) : (
                        <ICONS.warning aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-warn" />
                      )}
                      <span className={problem.blocking ? "text-fg" : "text-fg-muted"}>
                        <span className="sr-only">{problem.blocking ? t("environment.pasteDialog.errorPrefix") : t("environment.pasteDialog.notePrefix")}</span>
                        {problem.text}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}
              {count > 0 ? (
                <ul aria-label={t("environment.pasteDialog.parsedHeading")} className="divide-y divide-border rounded-chip border border-border bg-surface">
                  {[...parsed.variables].map(([name, value]) => {
                    const invalid = nameProblem(name, t.locale) !== null;
                    return (
                      <li key={name} className="flex min-w-0 flex-col px-2 py-1 text-12">
                        <span className="flex min-w-0 items-baseline justify-between gap-2">
                          <Mono truncate title={name}>
                            {name === "" ? t("environment.noName") : name}
                          </Mono>
                          {invalid ? (
                            <span className="shrink-0 text-fail">{t("environment.tab.notValidName")}</span>
                          ) : (
                            <span className="shrink-0 text-fg-muted">{current.has(name) ? t("environment.pasteDialog.replaces") : t("environment.pasteDialog.new")}</span>
                          )}
                        </span>
                        {value === "" ? (
                          <span className="text-fg-muted">{t("environment.empty")}</span>
                        ) : (
                          <Mono tone="muted" truncate title={value}>
                            {value}
                          </Mono>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : null}
            </div>
          </section>
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
            <span aria-hidden="true" className="text-13 font-medium text-fg">
              {t("environment.pasteDialog.modeLabel")}
            </span>
            <SegmentedControl<PasteMode> label={t("environment.pasteDialog.modeLabel")} options={modes} value={mode} onValueChange={setMode} />
          </div>
          <p className="text-13 text-fg-muted">
            {mode === "merge"
              ? t("environment.pasteDialog.mergeDescription")
              : removals > 0
                ? t("environment.pasteDialog.replaceDescriptionWithRemovals", { count: removals })
                : t("environment.pasteDialog.replaceDescriptionNoRemovals")}
          </p>
        </div>
      </form>
    </Dialog>
  );
}
