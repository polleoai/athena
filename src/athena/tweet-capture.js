"use strict";

/**
 * Long X posts whose full text the syndication CDN no longer serves.
 *
 * bin/lib/fetch_tweet.py exits 3 with a raw marked `capture_incomplete` when a
 * post is a long (note) tweet but the JSON carries only the ~280-char preview
 * (witnessed: techNmak/status/2122711743369785617, 2026-11-17: the copy
 * stopped at "2/ Brendan"). The plugin then tries its browser capture and
 * keeps the browser text only when it really holds more of the same post:
 * it must contain the preview's opening and be longer. Otherwise a login wall
 * or an error page would replace a real fragment. Mirrors
 * fetch_tweet.fuller_capture on the CLI side.
 */

// Lowercased words with link pieces removed (mirrors fetch_tweet._norm). The
// preview has t.co links, the raw the expanded URL, and X's page splits a
// display URL over lines ("https://", "poloclub.github.io/transformer-ex",
// "plainer/", "…"). "2/"-style numbering is text. An ellipsis right after a
// link is X's display-URL marker; any other ellipsis stays.
const DOMAINISH = /^[\w-]+(?:\.[\w-]+)+\/?\S*$/;
function _isLinkPiece(tok) {
  if ("![<(".includes(tok[0])) return false;  // media/markup: kept as a stop signal
  if (/^https?:\/\//.test(tok) || DOMAINISH.test(tok)) return true;
  return tok.includes("/") && !/^\d+\/$/.test(tok);
}
function _norm(s) {
  const out = [];
  let prevLink = false;
  for (const tok of String(s || "").split(/\s+/).filter(Boolean)) {
    if (_isLinkPiece(tok)) { prevLink = true; continue; }
    if (prevLink && (tok === "\u2026" || tok === "...")) continue;
    prevLink = false;
    out.push(tok);
  }
  return out.join(" ").toLowerCase();
}

/** The post text inside a fetch_tweet raw: body minus frontmatter, byline,
 *  the incomplete note, media lines and t.co links. */
function previewFromRaw(raw) {
  const body = String(raw || "").replace(/^---\n[\s\S]*?\n---\n?/, "");
  const lines = [];
  for (const ln of body.split("\n")) {
    const t = ln.trim();
    if (!t) { lines.push(""); continue; }
    if (/^\*\*.+\*\*$/.test(t)) continue;            // byline
    if (t.startsWith(">")) break;                    // incomplete note / quote
    if (/^!\[|^<img\s/i.test(t)) continue;           // media
    if (t === "---") break;                          // quoted-tweet separator
    lines.push(ln);
  }
  return lines.join("\n").replace(/https?:\/\/t\.co\/\S+/g, "").replace(/…\s*$/, "").trim();
}

// What may follow the preview's last words when a page shows only the
// preview: an ellipsis, "Show more", markup or media, a quoted post, or X's timestamp line.
const NOT_CONTINUATION = /^\s*(?:\u2026|\.\.\.|show more|<|!\[|\[|\(|---|\*\*quoting|quote|>|\d{1,2}:\d{2}\b|$)/;

// The candidate must hold the post PAST the preview's last words, not just be
// longer: a page showing the same preview plus title, byline and chrome is
// longer but no fuller. Mirrors fetch_tweet.fuller_capture.
function isFullerCapture(candidateText, floorRaw) {
  const cand = _norm(candidateText);
  const head = _norm(previewFromRaw(floorRaw));
  if (!cand || !head || !cand.includes(head.slice(0, 60))) return false;
  const tail = head.slice(-30);
  const i = cand.indexOf(tail, cand.indexOf(head.slice(0, 60)));
  if (i < 0) return false;
  const rest = cand.slice(i + tail.length);
  return rest.trim().length >= 20 && !NOT_CONTINUATION.test(rest);
}

/** Interpret fetch_tweet.py's exit: 0 = complete, 3 = incomplete floor. */
function classifyTweetFetch(code, stdout) {
  const raw = String(stdout || "").trim();
  if (raw.length <= 80) return null;
  if (!code) return { raw, incomplete: false };
  if (code === 3) return { raw, incomplete: true };
  return null;
}

module.exports = { previewFromRaw, isFullerCapture, classifyTweetFetch };
