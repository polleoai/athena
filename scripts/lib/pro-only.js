#!/usr/bin/env node
/**
 * pro-only.js — keep Athena's VM/GUI tests and release gates off the Max.
 *
 * The user's rule (2026-11-16, relayed by PGM): every Athena test runs on the
 * Pro, including VM/GUI runs and release gates. The Max is for heavy lifting
 * and the user's own work. Light unit tests under `ergon light` are fine.
 *
 * It refuses on the Max rather than allowing only on the Pro, because
 * scripts/vm-tests' configs also load inside the Linux and Windows VMs, whose
 * hostnames are neither. An unreadable hostname counts as the Max
 * (can't tell = refuse). There's no override: run on the Pro instead.
 *
 *   node scripts/lib/pro-only.js <what>   exit 0 off the Max, 3 on it
 *   require(".../pro-only.js").refuseOnMax(what)   throws on the Max
 */
"use strict";

const os = require("os");

// The Max's hostname (`hostname -s`), lowercase. Rename the Max → update here.
const MAX_HOSTS = new Set(["chongs-m1-max-16"]);

function isMaxHost(host) {
  if (typeof host !== "string" || !host.trim()) return true;
  return MAX_HOSTS.has(host.trim().toLowerCase().replace(/\.local$/, ""));
}

function message(what) {
  return `${what}: NOT RUN on the Max. Athena's tests, VM/GUI runs and release ` +
    "gates run on the Pro (the user's rule, 2026-11-16); the Max is for heavy " +
    "lifting and the user's own work.";
}

function refuseOnMax(what, host = os.hostname()) {
  if (isMaxHost(host)) throw new Error(message(what));
}

if (require.main === module) {
  const what = process.argv[2] || "athena test";
  let host = "";
  try { host = os.hostname(); } catch { host = ""; }
  if (isMaxHost(host)) {
    console.error(message(what));
    process.exit(3);
  }
}

module.exports = { isMaxHost, refuseOnMax, MAX_HOSTS };
