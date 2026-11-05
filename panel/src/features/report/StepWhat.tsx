import type { RefObject } from "react";

import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { Textarea } from "../../components/ui/Textarea";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { AREAS } from "./areas";
import type { AreaValue } from "./areas";
import { ImagesField } from "./ImagesField";
import type { Images } from "./useImages";

export interface WhatDraft {
  title: string;
  area: AreaValue;
  happened: string;
  reproduce: string;
}

/**
 * The first step: what the person tells. The title and what happened are what the form asks, the steps are
 * optional, the area is chosen from the page they were on (they can change it), and the images are theirs.
 */
export function StepWhat({
  draft,
  onChange,
  images,
  onCapture,
  errors,
  titleRef,
  happenedRef,
}: {
  draft: WhatDraft;
  onChange: (change: Partial<WhatDraft>) => void;
  images: Images;
  onCapture: () => void;
  errors: { title?: string; happened?: string };
  titleRef: RefObject<HTMLInputElement | null>;
  happenedRef: RefObject<HTMLDivElement | null>;
}) {
  const t = useT();
  return (
    <form
      noValidate
      className="flex min-w-0 flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      <Field label={t("report.what.titleLabel")} error={errors.title}>
        <Input
          ref={titleRef}
          value={draft.title}
          onValueChange={(title: string) => {
            onChange({ title });
          }}
          autoComplete="off"
        />
      </Field>
      <Field label={t("report.what.areaLabel")} description={t("report.what.areaHelp")} nativeLabel={false}>
        <Select<AreaValue>
          options={AREAS.map((area) => ({ value: area.value, label: t(area.key) }))}
          value={draft.area}
          onValueChange={(area) => {
            onChange({ area });
          }}
        />
      </Field>
      <Field label={t("report.what.happenedLabel")} description={t("report.what.happenedHelp")} error={errors.happened}>
        <div ref={happenedRef} className="min-w-0">
          <Textarea
            rows={3}
            value={draft.happened}
            onChange={(event) => {
              onChange({ happened: event.target.value });
            }}
          />
        </div>
      </Field>
      <Field label={t("report.what.stepsLabel")} optional>
        <Textarea
          rows={2}
          value={draft.reproduce}
          onChange={(event) => {
            onChange({ reproduce: event.target.value });
          }}
        />
      </Field>
      <ImagesField images={images} onCapture={onCapture} />
      {images.notes.length > 0 ? (
        <div className="flex flex-col gap-2">
          {images.notes.map((note) => (
            <Notice key={note} tone="warning" live>
              {note}
            </Notice>
          ))}
        </div>
      ) : null}
    </form>
  );
}
