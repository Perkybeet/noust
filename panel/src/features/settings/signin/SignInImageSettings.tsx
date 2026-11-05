import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import { removeSignInImage, saveSignInImage, signInAppearanceQuery, signInImageKeys } from "../../../api/queries/signInImage";
import type { SignInFit, SignInLimits } from "../../../api/queries/signInImage";
import { useDocumentTitle } from "../../../app/documentTitle";
import { SignInPicture } from "../../../components/illustrations/SignInPicture";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { QueryState } from "../../../components/page/QueryState";
import { SaveBar } from "../../../components/page/SaveBar";
import { Sections } from "../../../components/page/Section";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Field } from "../../../components/ui/Field";
import { InlineMessage } from "../../../components/ui/InlineMessage";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Popover } from "../../../components/ui/Popover";
import { Skeleton } from "../../../components/ui/Skeleton";
import { ICONS } from "../../../components/ui/icons";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { useNode } from "../../../nodes/useNode";
import { useCan } from "../../variables/useCan";
import { FormFailure } from "../SettingsForm";
import { SettingsPrimaryAction } from "../SettingsPrimaryAction";
import { judgeFile, readPicture } from "./readPicture";
import type { ChosenPicture, PictureProblem } from "./readPicture";
import { SignInPreview } from "./SignInPreview";

/** What the browser's file chooser offers: the three types the server accepts. */
const ACCEPT = "image/png,image/jpeg,image/webp";

/** What a picture may be, for a server that does not say (it always does). */
const FALLBACK_LIMITS: SignInLimits = { bytes: 1024 * 1024, side: 4096, alt_chars: 120 };

/** The permission that changes what the sign-in screen shows (the sign-in policy's). */
const PERMISSION = "security.manage";

function problemWords(t: T, problem: PictureProblem, limitBytes: number): string {
  switch (problem.kind) {
    case "too-large":
      return t("signInImage.errors.tooLarge", { size: formatBytes(problem.size, t.locale), limit: formatBytes(limitBytes, t.locale) });
    case "not-allowed":
      return t("signInImage.errors.notAllowed");
    case "unreadable":
      return t("signInImage.errors.unreadable");
  }
}

/**
 * The primary action of a page its reader may not use: it stays where it is, turned off and with a lock,
 * and a press says why instead of doing nothing (docs/DESIGN.md, the roles in read-only: the action stays,
 * disabled, with its reason one press away).
 */
function LockedAction({ label }: { label: string }) {
  const t = useT();
  return (
    <Popover
      title={t("signInImage.locked.title")}
      trigger={
        <Button variant="primary" aria-disabled="true" icon={<ICONS.locked aria-hidden="true" />} className="opacity-50">
          {label}
        </Button>
      }
    >
      <p className="text-13 text-fg-muted">{t.rich("signInImage.locked.reason", { permission: <Mono>{PERMISSION}</Mono> })}</p>
    </Popover>
  );
}

/**
 * Settings > Sign-in image: where Noust draws a noust on the screens before the console, an operator can
 * put a company's logo or any picture (item 129). The page is the screen as it will be seen, with the
 * picture in it, and under it how the picture fills its space, what a screen reader says in its place, and
 * the picture's file. Nothing is written until Save; the server judges the file by its bytes and says why
 * it refuses one in its own words, which are shown as they come. Who may not change it reads the page and
 * finds its action turned off with the reason. The line that says everyone sees it is always there.
 */
export function SignInImageSettings() {
  const t = useT();
  useDocumentTitle(t("signInImage.documentTitle"), 1);
  const { node } = useNode();
  const queryClient = useQueryClient();
  const canChange = useCan(PERMISSION);
  const query = useQuery(signInAppearanceQuery());
  const saved = query.data?.image ?? null;
  const limits = query.data === undefined ? undefined : (query.data.limits ?? FALLBACK_LIMITS);

  const [file, setFile] = useState<ChosenPicture | null>(null);
  const [fit, setFit] = useState<SignInFit | null>(null);
  const [alt, setAlt] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [removing, setRemoving] = useState(false);
  const chooser = useRef<HTMLInputElement>(null);

  const savedFit: SignInFit = saved?.fit === "cover" ? "cover" : "contain";
  const savedAlt = saved?.alt ?? "";
  const shownFit = fit ?? savedFit;
  const shownAlt = alt ?? savedAlt;
  const changes = (file !== null ? 1 : 0) + (shownFit !== savedFit ? 1 : 0) + (shownAlt.trim() !== savedAlt ? 1 : 0);
  const hasPicture = saved !== null || file !== null;

  const save = useMutation({
    mutationFn: () => saveSignInImage({ ...(file !== null ? { image: file.base64 } : {}), fit: shownFit, alt: shownAlt.trim() }),
    onSuccess: (answer) => {
      // The page says it: the screen above is the saved picture now, and the bar says there is nothing to save.
      queryClient.setQueryData(signInImageKeys.appearance, answer);
      setFile(null);
      setFit(null);
      setAlt(null);
    },
  });

  const discard = (): void => {
    setFile(null);
    setFit(null);
    setAlt(null);
    setProblem(null);
    save.reset();
  };

  /** Reads what was chosen or dropped and puts it on the screen above: nothing is sent yet. */
  const take = async (chosen: File | undefined): Promise<void> => {
    if (chosen === undefined || limits === undefined) return;
    setProblem(null);
    save.reset();
    const refused = judgeFile(chosen, limits);
    if (refused !== null) {
      setProblem(problemWords(t, refused, limits.bytes));
      return;
    }
    const picture = await readPicture(chosen);
    if (picture === null) {
      setProblem(problemWords(t, { kind: "unreadable" }, limits.bytes));
      return;
    }
    setFile(picture);
  };

  const onPick = (event: ChangeEvent<HTMLInputElement>): void => {
    void take(event.target.files?.[0]);
    // The same file chosen again must still be a change.
    event.target.value = "";
  };

  const carriesFiles = (event: DragEvent): boolean => event.dataTransfer.types.includes("Files");
  const onDragOver = (event: DragEvent): void => {
    if (!canChange || !carriesFiles(event)) return;
    event.preventDefault();
    setDragging(true);
  };
  const onDrop = (event: DragEvent): void => {
    if (!canChange || !carriesFiles(event)) return;
    event.preventDefault();
    setDragging(false);
    void take(event.dataTransfer.files[0]);
  };

  const art =
    file !== null ? (
      <SignInPicture
        key={file.dataUrl}
        src={file.dataUrl}
        fit={shownFit}
        alt={shownAlt}
        onFailed={() => {
          setFile(null);
          if (limits !== undefined) setProblem(t("signInImage.errors.notShown"));
        }}
      />
    ) : saved !== null ? (
      <SignInPicture key={saved.url} src={saved.url} fit={shownFit} alt={shownAlt} width={saved.width} height={saved.height} />
    ) : undefined;

  const choose = (): void => chooser.current?.click();
  const chooseLabel = hasPicture ? t("signInImage.actions.change") : t("signInImage.actions.choose");

  return (
    <Sections>
      {limits === undefined ? null : (
        <SettingsPrimaryAction>
          {canChange ? (
            <Button variant="primary" icon={<OBJECT_ICONS.signInImage aria-hidden="true" />} onClick={choose}>
              {chooseLabel}
            </Button>
          ) : (
            <LockedAction label={chooseLabel} />
          )}
        </SettingsPrimaryAction>
      )}

      <div className="flex flex-col gap-1.5">
        <p className="max-w-measure text-13 text-fg-muted">{t("signInImage.public")}</p>
        {canChange ? null : (
          <p className="flex max-w-measure items-start gap-2 text-13 text-fg-muted">
            <ICONS.locked aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0" />
            {t("signInImage.readOnly")}
          </p>
        )}
      </div>

      <QueryState
        query={query}
        sentences={{ loading: t("signInImage.loading"), failed: t("signInImage.loadFailed"), refreshFailed: t("signInImage.refreshFailed") }}
        skeleton={
          <div className="flex flex-col gap-6">
            <Skeleton className="h-96 w-full rounded-card" />
            <Skeleton className="h-32 w-full rounded-card" />
          </div>
        }
      >
        {() => (
          <>
            <figure aria-label={t("signInImage.preview.label")} className="m-0 flex flex-col gap-2">
              {/* Dropping a file is a shortcut for the button: the wrapper is presentation, and a keyboard has the button. */}
              <div
                role="presentation"
                data-drop-zone=""
                data-dragging={dragging || undefined}
                className="rounded-card outline-offset-2 data-dragging:outline-2 data-dragging:outline-focus"
                onDragEnter={onDragOver}
                onDragOver={onDragOver}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                <SignInPreview {...(art !== undefined ? { art } : {})} />
              </div>
              <figcaption aria-live="polite" className="text-12 text-fg-muted">
                {dragging
                  ? t("signInImage.file.dropActive")
                  : file !== null
                    ? t("signInImage.preview.chosen", { name: file.name })
                    : saved !== null
                      ? t("signInImage.preview.saved")
                      : t("signInImage.preview.none")}
              </figcaption>
            </figure>

            {/* The button above opens it: a file input is a second button of the same name to assistive technology and a second stop for the keyboard, so it is not drawn at all, and not for someone who cannot change the picture. */}
            {canChange ? <input ref={chooser} type="file" accept={ACCEPT} aria-label={chooseLabel} className="hidden" onChange={onPick} /> : null}

            {problem !== null ? (
              <InlineMessage tone="error" live>
                {problem}
              </InlineMessage>
            ) : null}
            <FormFailure error={save.error} title={t("signInImage.saveFailed")} />

            {hasPicture ? (
              <Card padding="md">
                {canChange ? (
                  <div className="flex flex-col gap-5">
                    {/* Not a Field: its label would name every radio of the group, and each is named by what it says. */}
                    <div className="flex flex-col gap-1.5">
                      <p aria-hidden="true" className="text-13 font-medium text-fg">
                        {t("signInImage.fit.label")}
                      </p>
                      <SegmentedControl<SignInFit>
                        label={t("signInImage.fit.label")}
                        className="self-start"
                        value={shownFit}
                        onValueChange={setFit}
                        options={[
                          { value: "cover", label: t("signInImage.fit.cover") },
                          { value: "contain", label: t("signInImage.fit.contain") },
                        ]}
                      />
                      <p className="max-w-measure text-13 text-fg-muted">{shownFit === "cover" ? t("signInImage.fit.coverHelp") : t("signInImage.fit.containHelp")}</p>
                    </div>
                    <Field label={t("signInImage.alt.label")} optional description={t("signInImage.alt.help")}>
                      <Input value={shownAlt} maxLength={limits?.alt_chars ?? 120} placeholder={t("signInImage.alt.placeholder")} onChange={(event) => setAlt(event.target.value)} />
                    </Field>
                    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                      <p className="text-13 text-fg-muted">{limits !== undefined ? t("signInImage.file.limits", { size: formatBytes(limits.bytes, t.locale), side: String(limits.side) }) : null}</p>
                      {saved !== null ? (
                        <Button variant="ghost" icon={<ICONS.delete aria-hidden="true" />} onClick={() => setRemoving(true)}>
                          {t("signInImage.actions.remove")}
                        </Button>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <KeyValueList
                    items={[
                      { label: t("signInImage.fit.label"), value: shownFit === "cover" ? t("signInImage.fit.cover") : t("signInImage.fit.contain"), mono: false, copy: false },
                      { label: t("signInImage.alt.label"), value: shownAlt, mono: false, copy: false },
                    ]}
                  />
                )}
              </Card>
            ) : canChange && limits !== undefined ? (
              <p className="text-13 text-fg-muted">
                {t("signInImage.file.drop")} {t("signInImage.file.limits", { size: formatBytes(limits.bytes, t.locale), side: String(limits.side) })}
              </p>
            ) : null}

            <CommandHint command='noust console sign-in-image set logo.png --fit contain --alt "Acme logo"' label={t("common.words.fromTerminal")} />

            {canChange ? <SaveBar changes={changes} saving={save.isPending} onDiscard={discard} onSave={() => save.mutate()} /> : null}
          </>
        )}
      </QueryState>

      <ConfirmDialog
        friction="simple"
        destructive
        open={removing}
        onOpenChange={setRemoving}
        title={t("signInImage.remove.title")}
        description={t("signInImage.remove.description")}
        actionLabel={t("signInImage.remove.action")}
        server={node}
        onConfirm={async () => {
          const answer = await removeSignInImage();
          queryClient.setQueryData(signInImageKeys.appearance, answer);
          discard();
        }}
      />
    </Sections>
  );
}
