import { Camera, ClipboardPaste, ImageUp, X } from "lucide-react";
import { useRef } from "react";

import { Button } from "../../components/ui/Button";
import { IconButton } from "../../components/ui/IconButton";
import { Kbd } from "../../components/ui/Kbd";
import { modKeyLabel } from "../../app/shortcuts";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { IMAGE_TYPES } from "./images";
import type { Images } from "./useImages";

/**
 * The images of a report: the ones added (a thumbnail, its name and its weight), and the three ways to add one:
 * choose files, take a picture of this tab (the browser asks first) and paste with the keyboard.
 */
export function ImagesField({ images, onCapture }: { images: Images; onCapture: () => void }) {
  const t = useT();
  const picker = useRef<HTMLInputElement>(null);
  return (
    <fieldset className="flex min-w-0 flex-col gap-2">
      <legend className="mb-1.5 text-13 font-medium text-fg">{t("report.what.imagesLegend")}</legend>
      <div className="flex flex-wrap items-start gap-3">
        {images.shots.map((shot) => (
          <figure key={shot.id} className="flex w-32 flex-col gap-1">
            <img src={shot.dataUrl} alt="" className="h-20 w-32 rounded-control border border-border bg-bg-sunken object-cover" />
            <figcaption className="flex items-center gap-1 text-12 text-fg-muted">
              <span className="min-w-0 flex-1 truncate" title={`${shot.name}, ${formatBytes(shot.size, t.locale)}`}>
                {shot.name}
              </span>
              <IconButton
                size="sm"
                label={t("common.words.removeNamed", { name: shot.name })}
                icon={<X />}
                onClick={() => {
                  images.remove(shot.id);
                }}
              />
            </figcaption>
          </figure>
        ))}
        <div className="flex min-w-0 flex-col items-start gap-1.5">
          <input
            ref={picker}
            type="file"
            accept={IMAGE_TYPES.join(",")}
            multiple
            tabIndex={-1}
            aria-hidden="true"
            className="sr-only"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              void images.add(files, "file");
            }}
          />
          <Button
            size="sm"
            icon={<ImageUp />}
            onClick={() => {
              picker.current?.click();
            }}
          >
            {t("report.what.choose")}
          </Button>
          <Button size="sm" icon={<Camera />} onClick={onCapture}>
            {t("report.what.capture")}
          </Button>
          <p className="flex items-center gap-1.5 text-12 text-fg-muted">
            <ClipboardPaste aria-hidden="true" className="size-icon-xs shrink-0" />
            {t.rich("report.what.paste", {
              keys: (
                <span key="keys" className="inline-flex gap-0.5">
                  <Kbd>{modKeyLabel()}</Kbd>
                  <Kbd>V</Kbd>
                </span>
              ),
            })}
          </p>
        </div>
      </div>
    </fieldset>
  );
}
