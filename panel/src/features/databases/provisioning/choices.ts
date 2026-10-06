/**
 * Reading `GET /api/databases/provisioning/choices` for the picker of a new database: which
 * flavours can hold one, which versions of each, and what asking for them means. Pure, so the
 * rules are tested without a screen. The server decides everything that matters (a version that
 * is not the one that runs is refused with what runs); this only says, before asking, what the
 * server will say.
 */

import type { EngineChoice, VersionOffer } from "../../../api/queries/provisioning";

/** The operator's pick: a flavour, a version (null for the default) and whether to install it first. */
export interface ProvisionPick {
  flavour: string | null;
  version: string | null;
  install: boolean;
}

export const NO_PICK: ProvisionPick = { flavour: null, version: null, install: false };

/** The value the version select holds for "the default": it is not a version the server knows. */
export const DEFAULT_VERSION = "default";

/** A flavour a database can be created on: it is installed, or can be installed now. */
export function isPickable(choice: EngineChoice): boolean {
  return choice.installed || choice.installable;
}

/** Flavours in the order the server lists them, the usable ones first (the order inside each group is kept). */
export function orderedChoices(choices: readonly EngineChoice[]): EngineChoice[] {
  return [...choices.filter(isPickable), ...choices.filter((choice) => !isPickable(choice))];
}

/** The flavour to start on: the first that is installed, else the first that can be installed. */
export function initialFlavour(choices: readonly EngineChoice[]): string | null {
  return (choices.find((choice) => choice.installed) ?? choices.find((choice) => choice.installable))?.flavour ?? null;
}

/** The choice a flavour names. */
export function choiceFor(choices: readonly EngineChoice[], flavour: string | null): EngineChoice | undefined {
  return choices.find((choice) => choice.flavour === flavour);
}

/** Whether the flavour is Redis-like: its "databases" are numbered slots of one instance. */
export function isSlotFlavour(choice: Pick<EngineChoice, "engine"> | undefined): boolean {
  return choice?.engine === "redis";
}

/** A version the operator may pick: the one that runs when installed, an installable one otherwise. */
export function isSelectable(choice: EngineChoice, offer: VersionOffer): boolean {
  return choice.installed ? offer.state === "installed" : offer.state === "installable";
}

/** The version the server installs when none is asked for, when it says which. */
export function defaultOffer(choice: EngineChoice): VersionOffer | undefined {
  return choice.versions.find((offer) => offer.default);
}

/** Whether creating a database on this flavour first installs the engine. */
export function needsInstall(choice: EngineChoice | undefined): boolean {
  return choice !== undefined && !choice.installed && choice.installable;
}

/**
 * The version to put in the request: what was picked, unless it is the default. An installed
 * flavour with nothing picked asks for nothing, so the server uses what runs.
 */
export function requestedVersion(pick: ProvisionPick): string | null {
  return pick.version === null || pick.version === DEFAULT_VERSION ? null : pick.version;
}

/**
 * The version a plan or a summary names: the one asked for, else the one the default stands for
 * (what runs, or what would install).
 */
export function shownVersion(choice: EngineChoice | undefined, pick: ProvisionPick): string | null {
  if (choice === undefined) return null;
  const asked = requestedVersion(pick);
  if (asked !== null) return asked;
  return choice.installed ? (choice.running_version ?? null) : (defaultOffer(choice)?.version ?? null);
}

/** What a version offer is, as a catalog key suffix. */
export type OfferState = "installed" | "installable" | "unavailable";

export function offerState(offer: VersionOffer): OfferState {
  return offer.state === "installed" || offer.state === "installable" ? offer.state : "unavailable";
}
