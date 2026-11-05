import { useQuery } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { useMemo, useRef, useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { Stepper } from "../../components/page/Stepper";
import { WizardActions } from "../../components/page/Wizard";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";
import { downloadBytes } from "../../lib/clipboard";
import { recentErrors } from "../../lib/errorBuffer";
import { areaForPath, AREAS } from "./areas";
import { archive, archiveName, reportFiles, DESCRIPTION_FILE } from "./bundle";
import { browserBlock, currentFacts } from "./browserBlock";
import { reportLink } from "./githubUrl";
import type { ReportFields } from "./githubUrl";
import { supportReportQuery } from "./queries";
import { redact } from "./redact";
import { StepDone } from "./StepDone";
import { StepGithub } from "./StepGithub";
import { StepSend } from "./StepSend";
import { StepWhat } from "./StepWhat";
import type { WhatDraft } from "./StepWhat";
import type { ReportRequest } from "./store";
import { useImages } from "./useImages";

type StepId = "what" | "send" | "github";

/** How long the dialog's exit takes to be gone from the frame a capture of the tab grabs. */
const HIDE_MS = 300;

interface Made {
  name: string;
  size: number;
  url: string;
  blocked: boolean;
}

/** Hides every secret in the person's own text, and counts them. */
function masked(draft: WhatDraft, version: string): { fields: ReportFields; hidden: number } {
  const title = redact(draft.title.trim());
  const happened = redact(draft.happened.trim());
  const reproduce = redact(draft.reproduce.trim());
  return {
    fields: { title: title.text, version, area: draft.area, whatHappened: happened.text, reproduce: reproduce.text },
    hidden: title.holes + happened.holes + reproduce.holes,
  };
}

/**
 * "Report a problem": three steps and the end. What happened (the person's words and images), what is sent (the
 * one file, each part shown as it is written) and what happens on GitHub. Noust sends nothing: the file is
 * downloaded, GitHub's form is opened with its fields filled in, and the person attaches the file and submits.
 */
export default function ReportDialog({ request, onClose }: { request: ReportRequest; onClose: () => void }) {
  const t = useT();
  const { data: session } = useQuery(sessionQuery());
  const page = useRouterState({ select: (state) => state.location.href });
  const [step, setStep] = useState<StepId>("what");
  const [made, setMade] = useState<Made | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [draft, setDraft] = useState<WhatDraft>(() => ({
    title: "",
    area: areaForPath(request.pathname),
    happened: request.error === null ? "" : `${t("report.what.fromError")}\n${redact(request.error).text}`,
    reproduce: "",
  }));
  const [errors, setErrors] = useState<{ title?: string; happened?: string }>({});
  // One moment names the file and stamps what is in it, so the name on the last step is the name downloaded.
  const [openedAt] = useState(() => new Date());
  const name = archiveName(openedAt);
  const [includeSupport, setIncludeSupport] = useState(true);
  const [hideDomains, setHideDomains] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);
  const happenedRef = useRef<HTMLDivElement>(null);
  const images = useImages(step === "what" && made === null);
  const support = useQuery({ ...supportReportQuery(hideDomains), enabled: includeSupport });
  useAnnounceChange(step, t(`report.steps.${step}`));

  const version = session?.version ?? support.data?.version ?? "";
  const { fields, hidden } = useMemo(() => masked(draft, version), [draft, version]);
  const link = useMemo(() => reportLink(fields), [fields]);
  // What the browser knows is read when the person gets to the step that shows it, not while they are typing.
  const [browser, setBrowser] = useState(() => browserBlock(currentFacts(page, version === "" ? null : version, t.locale, recentErrors())));
  const files = reportFiles({
    support: includeSupport && support.data !== undefined ? support.data.markdown : null,
    browser,
    shots: images.shots,
    description: link.cut ? fields : null,
  });
  const cutName = link.cut ? DESCRIPTION_FILE : null;
  const areaLabel = t(AREAS.find((area) => area.value === draft.area)?.key ?? "report.areas.other");

  const change = (part: Partial<WhatDraft>): void => {
    setDraft((current) => ({ ...current, ...part }));
    // What was said to be missing is no longer once the person has written in it.
    setErrors(({ title, happened }) => ({
      ...(part.title === undefined && title !== undefined ? { title } : {}),
      ...(part.happened === undefined && happened !== undefined ? { happened } : {}),
    }));
  };

  const leaveWhat = (): void => {
    if (draft.title.trim() === "") {
      setErrors({ title: t("report.what.missingTitle") });
      titleRef.current?.focus();
      return;
    }
    if (draft.happened.trim() === "") {
      setErrors({ happened: t("report.what.missingHappened") });
      happenedRef.current?.querySelector("textarea")?.focus();
      return;
    }
    setBrowser(browserBlock(currentFacts(page, version === "" ? null : version, t.locale, recentErrors())));
    setStep("send");
  };

  const open = (): boolean => {
    const win = window.open(link.url, "_blank");
    if (win !== null) win.opener = null;
    return win !== null;
  };

  const download = (): { name: string; size: number } => {
    const bytes = archive(files, openedAt);
    downloadBytes(name, bytes, "application/zip");
    return { name, size: bytes.length };
  };

  // Both in the same press: a browser lets a page open a tab only as the answer to one.
  const send = (): void => {
    const file = download();
    setMade({ ...file, url: link.url, blocked: !open() });
  };

  const hide = (): Promise<void> => {
    setCapturing(true);
    return new Promise((resolve) => {
      setTimeout(resolve, HIDE_MS);
    });
  };

  const footer =
    made !== null ? (
      <Button variant="primary" onClick={onClose}>
        {t("common.words.close")}
      </Button>
    ) : step === "what" ? (
      <WizardActions
        className="w-full"
        back={{ label: t("common.words.cancel"), onClick: onClose }}
        next={{ label: t("report.what.next"), onClick: leaveWhat }}
      />
    ) : step === "send" ? (
      <WizardActions
        className="w-full"
        back={{
          label: t("common.words.back"),
          onClick: () => {
            setStep("what");
          },
        }}
        next={{
          label: t("report.send.next"),
          onClick: () => {
            setStep("github");
          },
        }}
      />
    ) : (
      <WizardActions
        className="w-full"
        back={{
          label: t("common.words.back"),
          onClick: () => {
            setStep("send");
          },
        }}
        next={{ label: t("report.github.submit"), onClick: send }}
      />
    );

  const steps = [
    { id: "what", label: t("report.steps.what") },
    { id: "send", label: t("report.steps.send") },
    { id: "github", label: t("report.steps.github") },
  ];

  return (
    <Dialog
      open={!capturing}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      size="lg"
      initialFocus={titleRef}
      title={t("report.title")}
      description={t("report.description")}
      footer={footer}
    >
      {made !== null ? (
        <StepDone
          fileName={made.name}
          size={made.size}
          blocked={made.blocked}
          onDownloadAgain={() => {
            download();
          }}
          onOpenAgain={() => {
            open();
          }}
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-5">
          <Stepper
            orientation="horizontal"
            steps={steps}
            current={step}
            onSelect={(id) => {
              setStep(id as StepId);
            }}
          />
          {step === "what" ? (
            <StepWhat
              draft={draft}
              onChange={change}
              images={images}
              onCapture={() => void images.capture(hide, () => setCapturing(false))}
              errors={errors}
              titleRef={titleRef}
              happenedRef={happenedRef}
            />
          ) : step === "send" ? (
            <StepSend
              includeSupport={includeSupport}
              onIncludeSupport={setIncludeSupport}
              hideDomains={hideDomains}
              onHideDomains={setHideDomains}
              support={support}
              files={files}
              cutName={cutName}
            />
          ) : (
            <StepGithub fields={link.fields} areaLabel={areaLabel} fileName={name} cutName={cutName} hiddenInText={hidden} />
          )}
        </div>
      )}
    </Dialog>
  );
}
