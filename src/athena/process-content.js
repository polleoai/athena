"use strict";

/**
 * Schema-validated JSON for llmProcessContent (Athena dev issue #181).
 *
 * llmProcessContent asks the claude CLI for a page's title, summary, tags,
 * related topics and cleaned body, and used to regex the first `{…}` out of
 * the reply and JSON.parse it. A fenced reply, a trailing comma or a smart
 * quote in a string failed the parse and the page lost its LLM fields with
 * one console line. Now the reply goes through Gryphon's own CLI
 * structured-output helpers (the ones its providers use for
 * send({structuredOutput})): the schema is appended to the prompt, the reply
 * is parsed (a ```json fence stripped) and validated, and a bad reply is
 * retried up to maxRetries. The old regex parse is kept only as the soft
 * landing once the budget is spent, as in synthesis.js.
 *
 * Why not provider.send: this call runs the claude CLI directly with a cheap
 * model (modelForCli), a 30 s cap and no session; Gryphon's provider would
 * use the chat model and keep a worker alive. The helpers give the same
 * validation without changing which model runs or what it costs.
 */

const {
  injectSchemaHint,
  parseAndValidate,
} = require("../../vendor/gryphon/packages/provider-runtime/src/cli-structured-output.ts");

const PAGE_FIELDS_SCHEMA = {
  type: "object",
  properties: {
    title: { type: "string" },
    summary: { type: "string" },
    tags: { type: "array", items: { type: "string" } },
    related: { type: "array", items: { type: "string" } },
    body: { type: "string" },
  },
  required: ["title", "summary", "tags", "related", "body"],
  additionalProperties: false,
};
const PAGE_FIELDS_OUTPUT = { name: "wiki_page_fields", schema: PAGE_FIELDS_SCHEMA, maxRetries: 2 };

/** The pre-#181 parse: first `{…}` in the text, kept when it has title and
 *  body. Used only after structured output gave up. */
function legacyParse(text) {
  const m = String(text || "").match(/\{[\s\S]*\}/);
  if (!m) return null;
  try {
    const parsed = JSON.parse(m[0]);
    return parsed && parsed.title && parsed.body ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * @param {(prompt: string) => Promise<string|null>} runOnce  one CLI call;
 *        null = it failed or timed out (not retried: a retry wouldn't help
 *        and would add another 30 s).
 * @param {string} basePrompt
 * @param {{warn?: Function}} [log]
 * @returns {Promise<{fields: object, via: "structured"|"fallback", attempts: number}|null>}
 */
async function processWithSchema(runOnce, basePrompt, log = console, output = PAGE_FIELDS_OUTPUT) {
  const prompt = injectSchemaHint(basePrompt, output);
  const tries = 1 + Math.max(0, output.maxRetries | 0);
  let last = null;
  let lastErr = null;
  for (let attempt = 1; attempt <= tries; attempt++) {
    const text = await runOnce(prompt);
    if (text == null) return null;
    last = text;
    try {
      return { fields: parseAndValidate(text, output.schema), via: "structured", attempts: attempt };
    } catch (e) {
      lastErr = e;
    }
  }
  const fields = legacyParse(last);
  log.warn("[athena] llmProcessContent: structured output failed after " + tries + " tries" +
    (fields ? "; kept the text parse of the last reply" : "; no usable fields"),
    { reason: lastErr && lastErr.reason, message: lastErr && lastErr.message,
      lastOutput: String(last).slice(0, 500) });
  return fields ? { fields, via: "fallback", attempts: tries } : null;
}

module.exports = { PAGE_FIELDS_SCHEMA, PAGE_FIELDS_OUTPUT, processWithSchema, legacyParse };
