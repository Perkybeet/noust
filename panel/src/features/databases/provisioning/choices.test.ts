import { describe, expect, it } from "vitest";

import type { EngineChoice } from "../../../api/queries/provisioning";
import {
  DEFAULT_VERSION,
  choiceFor,
  defaultOffer,
  initialFlavour,
  isPickable,
  isSelectable,
  isSlotFlavour,
  needsInstall,
  offerState,
  orderedChoices,
  requestedVersion,
  shownVersion,
} from "./choices";
import { CHOICES } from "./testFixtures";


const [POSTGRES, MARIADB, MONGO, REDIS] = CHOICES as [EngineChoice, EngineChoice, EngineChoice, EngineChoice];

describe("the flavours a database can be created on", () => {
  it("are usable when installed or installable, and listed first", () => {
    expect(isPickable(POSTGRES)).toBe(true);
    expect(isPickable(MONGO)).toBe(true);
    expect(isPickable(MARIADB)).toBe(false);
    expect(orderedChoices(CHOICES).map((choice) => choice.flavour)).toEqual(["postgresql", "mongodb", "redis", "mariadb"]);
  });

  it("start on the first that is installed, else the first that can be installed", () => {
    expect(initialFlavour(CHOICES)).toBe("postgresql");
    expect(initialFlavour([MARIADB, MONGO])).toBe("mongodb");
    expect(initialFlavour([MARIADB])).toBeNull();
    expect(initialFlavour([])).toBeNull();
  });

  it("are found by flavour, and Redis-like ones are slots", () => {
    expect(choiceFor(CHOICES, "mongodb")?.display_name).toBe("MongoDB");
    expect(choiceFor(CHOICES, "nope")).toBeUndefined();
    expect(choiceFor(CHOICES, null)).toBeUndefined();
    expect(isSlotFlavour(REDIS)).toBe(true);
    expect(isSlotFlavour(POSTGRES)).toBe(false);
    expect(isSlotFlavour(undefined)).toBe(false);
  });
});

describe("the versions of a flavour", () => {
  it("offer only the running one when the engine is installed", () => {
    expect(POSTGRES.versions.filter((offer) => isSelectable(POSTGRES, offer)).map((offer) => offer.version)).toEqual(["16"]);
  });

  it("offer the installable ones when it is not, the default marked", () => {
    expect(MONGO.versions.filter((offer) => isSelectable(MONGO, offer)).map((offer) => offer.version)).toEqual(["7.0", "8.0"]);
    expect(defaultOffer(MONGO)?.version).toBe("8.0");
  });

  it("say what an offer is, whatever the server calls an unknown state", () => {
    expect(offerState({ version: "1", state: "installed", default: false })).toBe("installed");
    expect(offerState({ version: "1", state: "installable", default: false })).toBe("installable");
    expect(offerState({ version: "1", state: "unavailable", default: false })).toBe("unavailable");
    expect(offerState({ version: "1", state: "retired", default: false })).toBe("unavailable");
  });
});

describe("what is asked of the server", () => {
  it("installs first only for a flavour that is not installed and can be", () => {
    expect(needsInstall(MONGO)).toBe(true);
    expect(needsInstall(POSTGRES)).toBe(false);
    expect(needsInstall(MARIADB)).toBe(false);
    expect(needsInstall(undefined)).toBe(false);
  });

  it("asks for no version at all for the default", () => {
    expect(requestedVersion({ flavour: "postgresql", version: null, install: false })).toBeNull();
    expect(requestedVersion({ flavour: "postgresql", version: DEFAULT_VERSION, install: false })).toBeNull();
    expect(requestedVersion({ flavour: "mongodb", version: "7.0", install: true })).toBe("7.0");
  });

  it("names the version a default stands for: what runs, or what would install", () => {
    expect(shownVersion(POSTGRES, { flavour: "postgresql", version: null, install: false })).toBe("16.4");
    expect(shownVersion(MONGO, { flavour: "mongodb", version: null, install: true })).toBe("8.0");
    expect(shownVersion(MONGO, { flavour: "mongodb", version: "7.0", install: true })).toBe("7.0");
    expect(shownVersion(REDIS, { flavour: "redis", version: null, install: false })).toBeNull();
    expect(shownVersion(undefined, { flavour: null, version: null, install: false })).toBeNull();
  });
});
