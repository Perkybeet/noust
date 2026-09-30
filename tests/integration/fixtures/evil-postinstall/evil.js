// A hostile postinstall: what a compromised dependency tries on a build server.
// Each attempt is recorded, never thrown: the install must succeed either way,
// so what the harness compares is what the script managed, sandboxed or not.
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const report = {};

function attempt(name, action) {
  try {
    const value = action();
    report[name] = { ok: true, value: value === undefined ? null : String(value).slice(0, 120) };
  } catch (error) {
    report[name] = { ok: false, code: error.code || String(error) };
  }
}

attempt("write_root", () => fs.writeFileSync("/root/pwned", "written by a postinstall\n"));
attempt("read_config", () => fs.readFileSync("/etc/noust/config.yaml", "utf8").length);
attempt("read_decoy", () => fs.readFileSync("/etc/noust/sandbox-decoy", "utf8"));
attempt("read_store", () => {
  for (const candidate of ["/var/lib/noust/noust.db", "/root/.local/share/noust/noust.db"]) {
    if (fs.existsSync(candidate)) return fs.readFileSync(candidate).length;
  }
  throw Object.assign(new Error("no store found"), { code: "ENOENT" });
});
attempt("read_other_env", () => fs.readFileSync("/var/www/apps/decoy-test/shared/.env", "utf8"));
attempt("write_cron", () => fs.writeFileSync("/etc/cron.d/noust-evil", "* * * * * root true\n"));
attempt("write_usr_local", () => fs.writeFileSync("/usr/local/bin/noust-evil", "#!/bin/sh\n"));
attempt("write_release", () => fs.writeFileSync(path.join(process.cwd(), "built.txt"), "built\n"));
attempt("interfaces", () => Object.keys(os.networkInterfaces()).length);
attempt("daemon", () => {
  const child = spawn("sleep", ["777"], { detached: true, stdio: "ignore" });
  child.unref();
  return child.pid;
});
report.uid = process.getuid();
report.sentinel = process.env.NOUST_SECRET_SENTINEL || null;

for (const [action, result] of Object.entries(report)) {
  console.log(JSON.stringify({ action, result }));
}
fs.writeFileSync(path.join(process.cwd(), "sandbox-report.json"), JSON.stringify(report));
