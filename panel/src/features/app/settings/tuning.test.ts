import { describe, expect, it } from "vitest";

import { changedIn, devicesOf, draftOf, parseTuning, presetLines, requestOptions, withLines } from "./tuning";
import type { TuningDraft } from "./tuning";

const EMPTY = draftOf(undefined);

function draft(over: Partial<TuningDraft>): TuningDraft {
  return { ...EMPTY, ...over };
}

describe("reading the limits half of the tuning form", () => {
  it("sends nothing for an empty form", () => {
    const { values, errors } = parseTuning("limits", EMPTY, 4);

    expect(errors).toEqual({});
    expect(values).toEqual({
      memory_high_mb: null,
      memory_swap_max_mb: null,
      cpu_weight: null,
      io_weight: null,
      limit_nofile: null,
      limit_nproc: null,
      limit_memlock_mb: null,
      allowed_cpus: null,
      core_dumps: null,
    });
  });

  it("reads whole numbers, the CPUs, infinity and the core dumps", () => {
    const { values, errors } = parseTuning(
      "limits",
      draft({ memoryHigh: "256", swapMax: "0", cpuWeight: "200", allowedCpus: "0-1, 3", memlock: "infinity", core: "forbid", nofile: "100000" }),
      4,
    );

    expect(errors).toEqual({});
    expect(values).toMatchObject({
      memory_high_mb: 256,
      memory_swap_max_mb: 0,
      cpu_weight: 200,
      allowed_cpus: "0-1,3",
      limit_memlock_mb: "infinity",
      core_dumps: false,
      limit_nofile: 100000,
    });
  });

  it("says what is wrong with each value, with the server's bounds", () => {
    const { errors } = parseTuning("limits", draft({ memoryHigh: "8", cpuWeight: "0", nofile: "many", nproc: "5000000", allowedCpus: "0-9" }), 4);

    expect(errors.memoryHigh).toBe("8 is out of range: use 64 to 16777216.");
    expect(errors.cpuWeight).toBe("0 is out of range: use 1 to 10000.");
    expect(errors.nofile).toBe("Use a whole number, or leave it empty.");
    expect(errors.nproc).toBe("5000000 is out of range: use 16 to 4194304.");
    expect(errors.allowedCpus).toBe("CPU 9 does not exist: this server has 4, numbered from 0.");
  });

  it("refuses a set of CPUs that is not one, or runs backwards", () => {
    expect(parseTuning("limits", draft({ allowedCpus: "a" }), 4).errors.allowedCpus).toMatch(/Write CPUs as numbers/);
    expect(parseTuning("limits", draft({ allowedCpus: "3-1" }), 4).errors.allowedCpus).toMatch(/Write CPUs as numbers/);
  });

  it("does not know the CPUs of the server before it is told, and so does not refuse one", () => {
    expect(parseTuning("limits", draft({ allowedCpus: "0-63" }), null).errors).toEqual({});
  });
});

describe("reading the privileges half", () => {
  it("reads the capabilities ticked and the devices typed, a mode after the last colon", () => {
    const { values, errors } = parseTuning("privileges", draft({ capabilities: ["IPC_LOCK"], devices: "/dev/nvidia0:rw\nchar-drm:r\n/dev/kvm\n" }), 4);

    expect(errors).toEqual({});
    expect(values.capabilities).toEqual(["IPC_LOCK"]);
    expect(values.devices).toEqual([
      { path: "/dev/nvidia0", mode: "rw" },
      { path: "char-drm", mode: "r" },
      { path: "/dev/kvm", mode: "rw" },
    ]);
  });

  it.each(["/etc/shadow", "/dev/../etc/passwd", "nvidia0", "char-NVIDIA", "/dev/"])("refuses %s as a device", (path) => {
    expect(parseTuning("privileges", draft({ devices: path }), 4).errors.devices).toContain(path);
  });

  it("keeps a colon that is not a mode as part of the (then invalid) path", () => {
    expect(devicesOf("/dev/x:y")).toEqual([{ path: "/dev/x:y", mode: "rw" }]);
    expect(parseTuning("privileges", draft({ devices: "/dev/x:y" }), 4).errors.devices).toBeDefined();
  });
});

describe("what the form says it changed", () => {
  const stored = draftOf({ cpu_weight: 50, capabilities: ["IPC_LOCK"], devices: [{ path: "char-drm", mode: "rw" }] });

  it("counts a field only when its text differs, compared trimmed", () => {
    expect(changedIn("limits", { ...stored, cpuWeight: " 50 " }, stored)).toBe(0);
    expect(changedIn("limits", { ...stored, cpuWeight: "60", ioWeight: "5" }, stored)).toBe(2);
  });

  it("compares the capabilities as a set and the devices as parsed", () => {
    expect(changedIn("privileges", { ...stored, capabilities: ["IPC_LOCK"], devices: "char-drm:rw\n\n" }, stored)).toBe(0);
    expect(changedIn("privileges", { ...stored, capabilities: ["IPC_LOCK", "NET_RAW"] }, stored)).toBe(1);
    expect(changedIn("privileges", { ...stored, devices: "char-drm:r" }, stored)).toBe(1);
  });

  it("round trips the options the server sends", () => {
    expect(draftOf({ core_dumps: true, limit_memlock_mb: "infinity", allowed_cpus: "0-1" })).toMatchObject({ core: "allow", memlock: "infinity", allowedCpus: "0-1" });
    expect(draftOf({ core_dumps: false }).core).toBe("forbid");
  });
});

describe("what is sent", () => {
  it("is the whole tuning: the other half as the server has it, this half as the form says", () => {
    const stored = { cpu_weight: 50, io_weight: 20, capabilities: ["IPC_LOCK"] };

    const sent = requestOptions(stored, "limits", { cpu_weight: 80, io_weight: null, memory_high_mb: 256, allowed_cpus: null });

    expect(sent).toEqual({ cpu_weight: 80, memory_high_mb: 256, capabilities: ["IPC_LOCK"] });
  });

  it("drops an empty list, which is how a capability is taken away", () => {
    expect(requestOptions({ cpu_weight: 50, capabilities: ["IPC_LOCK"] }, "privileges", { capabilities: [], devices: [] })).toEqual({ cpu_weight: 50 });
  });
});

describe("the GPU presets", () => {
  it("add one line per device group, and never twice", () => {
    const lines = presetLines(["char-nvidia", "char-nvidia-uvm"]);

    expect(lines).toBe("char-nvidia:rw\nchar-nvidia-uvm:rw");
    expect(withLines("/dev/kvm:rw", lines)).toBe("/dev/kvm:rw\nchar-nvidia:rw\nchar-nvidia-uvm:rw");
    expect(withLines(withLines("", lines), lines)).toBe(lines);
  });
});
