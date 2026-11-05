import type { ReactNode } from "react";

import { KeyValueList } from "../../components/page/KeyValueList";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { TITLE_PREFIX } from "./githubUrl";
import type { ReportFields } from "./githubUrl";

/** How much of a long text the preview shows: what the person wrote they know; this is to check what is filled in. */
const PREVIEW_LENGTH = 400;

function shown(text: string): string {
  const flat = text.trim();
  return flat.length <= PREVIEW_LENGTH ? flat : `${flat.slice(0, PREVIEW_LENGTH).trimEnd()}…`;
}

/** One of the three things that happen when the button is pressed, numbered. */
function Item({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className="flex size-7 shrink-0 items-center justify-center rounded-pill border border-border-strong bg-surface text-12 font-semibold text-fg"
      >
        {number}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-14 font-medium text-fg">{title}</span>
        <span className="block text-13 text-fg-muted">{children}</span>
      </span>
    </li>
  );
}

/**
 * The third step: what happens when the button is pressed and what the person does next. Noust sends nothing; it
 * downloads the file and opens GitHub's form with the fields filled in, and the person attaches the file and
 * submits. What will already be written there is shown, field by field, as GitHub's form words them.
 */
export function StepGithub({
  fields,
  areaLabel,
  fileName,
  cutName,
  hiddenInText,
}: {
  /** The fields as the address carries them (cut where they did not fit, masked). */
  fields: ReportFields;
  /** The area said in the person's language, to show beside the form's own option. */
  areaLabel: string;
  fileName: string;
  /** The part that holds the whole of the text, when the address cut it; null when nothing was cut. */
  cutName: string | null;
  /** How many secrets were hidden in what the person wrote. */
  hiddenInText: number;
}) {
  const t = useT();
  const area = areaLabel === fields.area ? fields.area : `${fields.area} (${areaLabel})`;
  return (
    <div className="flex min-w-0 flex-col gap-5">
      <ol className="flex flex-col gap-3">
        <Item number={1} title={t("report.github.downloadTitle")}>
          {t.rich("report.github.downloadText", { name: <Mono key="name">{fileName}</Mono> })}
        </Item>
        <Item number={2} title={t("report.github.openTitle")}>
          {t("report.github.openText")}
        </Item>
        <Item number={3} title={t("report.github.attachTitle")}>
          {t.rich("report.github.attachText", { submit: <Mono key="submit">Submit new issue</Mono> })}
        </Item>
      </ol>
      <div className="flex flex-col gap-2">
        <p className="text-13 font-medium text-fg">{t("report.github.written")}</p>
        <KeyValueList
          wrap
          items={[
            { label: t("report.github.formTitle"), value: `${TITLE_PREFIX}${fields.title}`, mono: false, copy: false },
            { label: t("report.github.formVersion"), value: fields.version, mono: true, copy: false },
            { label: t("report.github.formArea"), value: area, mono: false, copy: false },
            { label: t("report.github.formHappened"), value: shown(fields.whatHappened), mono: false, copy: false },
            { label: t("report.github.formSteps"), value: shown(fields.reproduce), mono: false, copy: false },
          ]}
        />
      </div>
      {cutName !== null ? <Notice tone="info">{t("report.github.cutNote", { name: cutName })}</Notice> : null}
      {hiddenInText > 0 ? <Notice tone="info">{t("report.github.hiddenNote", { count: hiddenInText })}</Notice> : null}
      <p className="text-12 text-fg-muted">{t("report.github.security")}</p>
    </div>
  );
}
