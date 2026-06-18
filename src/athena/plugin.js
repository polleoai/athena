/**
 * Athena — Obsidian plugin for capturing + synthesizing knowledge.
 *
 * Chat UI comes from GryphonChatView (vendor/gryphon/src/chat-view.js). Athena
 * configures it via composition, not inheritance — an options bag passed
 * at construction time. Extension points used:
 *   - extraToolStatus      — adds KB MCP tool status messages
 *   - extraProcessArgs     — --disable-slash-commands, --allowedTools,
 *                            --append-system-prompt
 *   - onBeforeSend         — intercepts mechanical `kb` commands before
 *                            they reach Claude Code
 *   - autocompleteSources  — registers `kb ...` completion alongside `/`
 *   - stopStreamingHooks   — aborts mechanical subprocess + browser capture
 *
 * KB features (ingest pipeline, duplicate detection, browser capture,
 * Web Clipper watcher, url-new.txt watcher, wiki page builder) live on
 * AthenaPlugin itself.
 */

const {
  Plugin, PluginSettingTab, Setting, Notice, Modal,
} = require("obsidian");
const { spawn, execFile, execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// Gryphon is consumed as a git submodule at vendor/gryphon. The chat UI
// is normally extended through Gryphon's documented extension points
// (options bag on GryphonChatView). One narrow exception: the welcome
// panel's hardcoded brand strings — Athena owns those via a thin
// AthenaChatView subclass that swaps text nodes after super renders.
// See src/athena/athena-chat-view.js for the architectural rationale.
const { AthenaChatView } = require("./athena-chat-view");
const {
  DEFAULT_SETTINGS, MODELS, EFFORTS, PERMS, PROVIDER_PREFS,
  resolveConnectionTimeoutMs,
} = require("../../vendor/gryphon/src/constants");
const { findClaudeBinary, buildEnhancedPath } = require("../../vendor/gryphon/src/utils");
const { SkillRegistry } = require("../../vendor/gryphon/src/skills");
// Windows-safe spawn helper for `.cmd` / `.bat` shims (npm-installed CLIs
// land as claude.cmd / codex.cmd / etc. on Windows). Node 20+ refuses to
// spawn `.cmd` directly without shell:true or windowsVerbatimArguments:true
// (CVE-2024-27980 mitigation) — bare spawn returns EINVAL. See win-spawn.js
// in Gryphon for the full quoting+escaping rationale.
const { isWindowsShim, wrapForCmdShim } = require("../../vendor/gryphon/packages/protect/src/win-spawn");

// Resolve the Python 3 executable for this OS. POSIX has `python3` on
// PATH by convention; Windows defaults to `python.exe` (no `python3.exe`
// unless the user explicitly installed it that way). Cache the lookup so
// every spawn doesn't re-scan.
// JS executed inside BrowserWindow/webview during chat `kb add` to
// extract page title + main text + image refs in one round-trip.
// Returns { title, text, images: [{src, alt}] }. Filters out tiny
// tracking pixels and data: URLs. Same script used by both the
// BrowserWindow and webview code paths so a regression in one stays
// in lockstep with the other.
// 1.1.0-cap-quality: proper DOM walker → markdown instead of innerText.
// innerText strips structural markup: <ul><li> becomes text-with-newlines
// (no `-`), <pre><code> becomes plain text (no fences), and CSS-line-broken
// <span>-per-glyph math (X.com renders LaTeX this way) becomes one
// character per line. The walker visits each element and emits the
// correct markdown for it. Post-processors then (a) collapse runs of
// single-Unicode-math-char lines into one line and (b) normalize
// Unicode bullet glyphs (•, ‧, ⁃) to `- `.
//
// Runs inside the browser context (BrowserWindow.executeJavaScript or
// webview.executeJavaScript) — no Node access, pure DOM.
const _BROWSER_EXTRACT_JS = `
  (function() {
    var titleEl = document.querySelector('title');
    var title = titleEl ? (titleEl.innerText || '').trim() : '';

    // ── Walker: DOM node → markdown string ──
    // Recursive; respects structure for the element types that dominate
    // technical content. Skips script/style/noscript/etc. Falls through
    // to walkChildren() for unknown tags so unknown wrapper divs don't
    // break the walk.
    var SKIP_TAGS = { script: 1, style: 1, noscript: 1, meta: 1, link: 1, head: 1, svg: 1 };
    // Image chrome filter — referenced inside the walker AND the separate
    // image-list extractor at the bottom. Hoisted so both see it.
    function _isXcomChromeImageEarly(src) {
      if (/\\/profile_images\\//i.test(src)) return true;
      if (/abs\\.twimg\\.com\\/hashflags\\//i.test(src)) return true;
      if (/abs\\.twimg\\.com\\/emoji\\//i.test(src)) return true;
      return false;
    }
    function _hostIsXcomEarly() {
      var h = (window.location && window.location.hostname) || '';
      return /(^|\\.)x\\.com$/i.test(h) || /(^|\\.)twitter\\.com$/i.test(h);
    }
    function _htmlAttrEsc(s) { return String(s == null ? '' : s).replace(/"/g, '&quot;'); }
    function walk(node) {
      if (!node) return '';
      if (node.nodeType === 3) return node.nodeValue || '';  // TEXT_NODE
      if (node.nodeType !== 1) return '';                     // not ELEMENT_NODE
      var tag = (node.tagName || '').toLowerCase();
      if (SKIP_TAGS[tag]) return '';
      function walkChildren() {
        var out = '';
        for (var i = 0; i < node.childNodes.length; i++) {
          out += walk(node.childNodes[i]);
        }
        return out;
      }
      switch (tag) {
        case 'h1': return '\\n\\n# ' + walkChildren().trim() + '\\n\\n';
        case 'h2': return '\\n\\n## ' + walkChildren().trim() + '\\n\\n';
        case 'h3': return '\\n\\n### ' + walkChildren().trim() + '\\n\\n';
        case 'h4': return '\\n\\n#### ' + walkChildren().trim() + '\\n\\n';
        case 'h5': return '\\n\\n##### ' + walkChildren().trim() + '\\n\\n';
        case 'h6': return '\\n\\n###### ' + walkChildren().trim() + '\\n\\n';
        case 'p':
        case 'div': {
          // <p> always gets paragraph breaks. <div> only when its content
          // looks like a paragraph (has direct text or inline children) —
          // wrapping <div>s should pass through cleanly.
          var inner = walkChildren();
          if (tag === 'p') return '\\n\\n' + inner.trim() + '\\n\\n';
          // For <div>: emit a break only if the content has substance
          // and isn't already block-formatted from a child element.
          if (!/\\S/.test(inner)) return inner;
          // If inner already ends with double newlines (child added them), no extra break.
          if (/\\n\\n\\s*$/.test(inner)) return inner;
          return inner + '\\n';
        }
        case 'br': return '\\n';
        case 'hr': return '\\n\\n---\\n\\n';
        case 'strong':
        case 'b': return '**' + walkChildren() + '**';
        case 'em':
        case 'i': return '*' + walkChildren() + '*';
        case 'code': {
          // Inline code unless inside <pre>. The <pre> branch handles
          // the fence; <pre><code> double-emit would otherwise produce
          // \`\`\`x\`\`\` shapes.
          if (node.parentElement && node.parentElement.tagName === 'PRE') {
            return walkChildren();
          }
          return '\` ' + walkChildren().replace(/\`/g, '') + ' \`';
        }
        case 'pre': {
          var content = walkChildren().replace(/^\\n+|\\n+$/g, '');
          // Use 4 backticks to survive embedded triple-backticks in source.
          return '\\n\\n\`\`\`\\n' + content + '\\n\`\`\`\\n\\n';
        }
        case 'blockquote': {
          var quoted = walkChildren().trim();
          var quotedLines = quoted.split('\\n').map(function(l) { return '> ' + l; }).join('\\n');
          return '\\n\\n' + quotedLines + '\\n\\n';
        }
        case 'a': {
          var href = node.getAttribute('href') || '';
          var linkText = walkChildren().trim();
          if (href && /^https?:/.test(href) && linkText && linkText !== href) {
            return '[' + linkText + '](' + href + ')';
          }
          return linkText || href || '';
        }
        case 'ul': {
          var lis = [];
          for (var i = 0; i < node.children.length; i++) {
            if (node.children[i].tagName === 'LI') lis.push(node.children[i]);
          }
          if (lis.length === 0) return walkChildren();
          var ulItems = lis.map(function(li) {
            var liText = '';
            for (var j = 0; j < li.childNodes.length; j++) liText += walk(li.childNodes[j]);
            return '- ' + liText.trim().replace(/\\n+/g, ' ');
          }).join('\\n');
          return '\\n\\n' + ulItems + '\\n\\n';
        }
        case 'ol': {
          var olis = [];
          for (var i = 0; i < node.children.length; i++) {
            if (node.children[i].tagName === 'LI') olis.push(node.children[i]);
          }
          if (olis.length === 0) return walkChildren();
          var olItems = olis.map(function(li, idx) {
            var liText = '';
            for (var j = 0; j < li.childNodes.length; j++) liText += walk(li.childNodes[j]);
            return (idx + 1) + '. ' + liText.trim().replace(/\\n+/g, ' ');
          }).join('\\n');
          return '\\n\\n' + olItems + '\\n\\n';
        }
        case 'table': {
          // Only emit a markdown table if there are real <tr> rows. Some
          // sites use <table> for layout — those fall through to
          // walkChildren() and get text concatenation, which is fine.
          var rows = node.querySelectorAll('tr');
          if (rows.length === 0) return walkChildren();
          var mdRows = [];
          for (var r = 0; r < rows.length; r++) {
            var cells = rows[r].children;
            var cellMd = [];
            for (var c = 0; c < cells.length; c++) {
              var cell = '';
              for (var k = 0; k < cells[c].childNodes.length; k++) {
                cell += walk(cells[c].childNodes[k]);
              }
              cellMd.push(cell.trim().replace(/\\|/g, '\\\\|').replace(/\\n+/g, ' '));
            }
            mdRows.push('| ' + cellMd.join(' | ') + ' |');
          }
          if (mdRows.length > 1) {
            var n = (rows[0].children || []).length || 1;
            var sep = '|' + new Array(n + 1).join(' --- |');
            mdRows.splice(1, 0, sep);
          }
          return '\\n\\n' + mdRows.join('\\n') + '\\n\\n';
        }
        case 'img': {
          // Inline images at their natural DOM position so a post with
          // the image at the top doesn't end up with the image at the
          // bottom of the captured markdown. Apply the same chrome
          // filter + size threshold that the separate images list uses
          // so we don't emit avatars / hashflags / sub-50px decorations.
          var imgSrc = node.currentSrc || node.src || node.getAttribute('src') || '';
          if (!imgSrc || imgSrc.indexOf('http') !== 0) return '';
          var imgW = node.naturalWidth || node.width || 0;
          var imgH = node.naturalHeight || node.height || 0;
          if (imgW > 0 && imgW < 50) return '';
          if (imgH > 0 && imgH < 50) return '';
          if (_hostIsXcomEarly() && _isXcomChromeImageEarly(imgSrc)) return '';
          var imgAlt = (node.alt || '').trim() || 'Image';
          return '\\n\\n<img src="' + _htmlAttrEsc(imgSrc) + '" alt="' + _htmlAttrEsc(imgAlt) + '" width="600">\\n\\n';
        }
        default: return walkChildren();
      }
    }

    // ── Post-processor 1: collapse runs of single-character math lines ──
    // X.com renders inline LaTeX as one <span> per character with CSS
    // line-breaks; the walker honors them, producing 35-line "formulas".
    // Detect runs of ≥3 single-char lines drawn from Mathematical
    // Alphanumeric Symbols (𝐴 etc.), Greek (α–ω), math operators (∑ ∇ √),
    // brackets, and operators, then concatenate into one line.
    function isMathChar(s) {
      if (!s || s.length > 2) return false;
      var cp = s.codePointAt(0);
      if (cp >= 0x1D400 && cp <= 0x1D7FF) return true;   // math alphanumerics
      if (cp >= 0x0370 && cp <= 0x03FF) return true;     // Greek/Coptic
      if (cp >= 0x2200 && cp <= 0x22FF) return true;     // math operators
      if (cp >= 0x27C0 && cp <= 0x27EF) return true;     // misc math symbols A
      if (cp >= 0x2980 && cp <= 0x29FF) return true;     // misc math symbols B
      if ('()[]{}+-*/=<>√^_,.|·'.indexOf(s) >= 0) return true;
      if (cp >= 0x30 && cp <= 0x39) return true;          // digits 0-9
      return false;
    }
    function collapseMathExplosion(text) {
      var lines = text.split('\\n');
      var out = [];
      var buf = [];
      function flush() {
        if (buf.length >= 3) out.push(buf.join(''));
        else if (buf.length > 0) for (var k = 0; k < buf.length; k++) out.push(buf[k]);
        buf = [];
      }
      for (var i = 0; i < lines.length; i++) {
        var t = lines[i].trim();
        if (t && isMathChar(t)) {
          buf.push(t);
        } else {
          flush();
          out.push(lines[i]);
        }
      }
      flush();
      return out.join('\\n');
    }

    // ── Post-processor 2: normalize Unicode bullets to markdown ──
    // X.com (and many sites) renders bullets as Unicode glyphs prepended
    // to a paragraph rather than semantic <li>. Catch the common ones.
    function normalizeUnicodeBullets(text) {
      var bulletClass = '[\\u2022\\u2023\\u2043\\u25E6\\u25AA\\u25AB\\u25B8\\u25CF\\u25CB\\u00B7\\u2190-\\u2199]';
      return text.split('\\n').map(function(line) {
        return line.replace(new RegExp('^(\\\\s*)' + bulletClass + '\\\\s+'), '$1- ');
      }).join('\\n');
    }

    // ── Post-processor 3: collapse runs of 3+ blank lines ──
    function normalizeBlankRuns(text) {
      return text.replace(/\\n{3,}/g, '\\n\\n').trim();
    }

    // ── Post-processor 4: X.com chrome strip ──
    // X.com (and twitter.com) wraps every post with leading + trailing
    // engagement counters, plus footer/sidebar/replies. The timestamp
    // signature is the anchor: every post ends with one of
    //   "3:52 AM · May 14, 2026 · 43.9K Views"
    //   "3:52 PM · Mar 14, 2026"
    // After the timestamp there's typically one more block of short
    // numeric lines (replies/RTs/likes/bookmarks) and then chrome
    // (Trending, More replies, "Show this thread"). We keep through
    // the timestamp + first post-timestamp counter block, drop after.
    //
    // Leading chrome: known string markers + a run of standalone short
    // numeric lines at the top get dropped. Conservative — only short
    // numeric-looking lines, not any line that happens to start with a
    // digit (those could be legit "5 things to know" prose).
    function isXcomHost() {
      var h = (window.location && window.location.hostname) || '';
      return /(^|\\.)x\\.com$/i.test(h) || /(^|\\.)twitter\\.com$/i.test(h);
    }
    var LEADING_CHROME_STRINGS = [
      'see new posts', 'conversation', 'article', 'show more',
      'show this thread', 'more replies', 'replying to',
    ];
    function isCounterLine(line) {
      // Standalone short number (1-4 digits) — typical engagement count
      if (/^\\d{1,4}$/.test(line)) return true;
      // Abbreviated count like 43K, 1.2M, 100K
      if (/^\\d{1,4}(\\.\\d{1,2})?[KM]$/.test(line)) return true;
      return false;
    }
    var TIMESTAMP_RE = /\\b\\d{1,2}:\\d{2}\\s+(AM|PM)\\s+·\\s+\\w+\\s+\\d{1,2},\\s+\\d{4}\\b/;
    // "Prose" = a line substantial enough that it's almost certainly the
    // post body (or post title), not chrome. Generous: short headlines
    // like "5 things you missed" still qualify because they're ≥4 words.
    function looksLikeProseLine(line) {
      if (!line) return false;
      if (/^#+\\s/.test(line)) return false;          // markdown heading
      if (/^\\[\\[.*\\]\\]$/.test(line)) return false; // wikilink
      if (/^\\/[a-zA-Z0-9_]/.test(line)) return false; // URL path
      if (/^@?[a-zA-Z0-9_-]{1,20}$/.test(line)) return false; // bare handle/identifier
      if (isCounterLine(line)) return false;
      if (LEADING_CHROME_STRINGS.indexOf(line.toLowerCase()) >= 0) return false;
      // Multi-word OR long OR sentence-punctuated = prose
      if (line.length >= 30) return true;
      if (/[.!?:]/.test(line)) return true;
      if (line.split(/\\s+/).length >= 4) return true;
      return false;
    }
    // X.com chrome strings that can appear anywhere in the post body
    // — typically between content and timestamp (the Premium upsell on
    // every Article page) or scattered (engagement promos). Drop on
    // sight regardless of position.
    var XCOM_CHROME_LINE_PATTERNS = [
      /^want to publish your own article\\??$/i,
      /^upgrade to premium\\??$/i,
      /^read \\d+ repl(y|ies)$/i,
      /^show this thread$/i,
      /^show more$/i,
    ];
    function isXcomChromeLine(line) {
      for (var i = 0; i < XCOM_CHROME_LINE_PATTERNS.length; i++) {
        if (XCOM_CHROME_LINE_PATTERNS[i].test(line)) return true;
      }
      return false;
    }
    // Drop runs of ≥3 consecutive counter lines (blank-separated) from
    // a given line slice. Returns the filtered array. Used to scrub the
    // engagement-counter blocks X.com inlines in MULTIPLE places: the
    // hover-preview at the top, the metric strip between title and body,
    // and any others. Post-timestamp counters are NOT touched (we apply
    // this only to the pre-timestamp region).
    function dropCounterRuns(srcLines) {
      var out = [];
      var buf = [];          // counter lines + intermediate blanks
      var counterCount = 0;
      function flush() {
        if (counterCount >= 3) {
          // Drop the whole buffer (counters + interstitial blanks)
        } else {
          for (var k = 0; k < buf.length; k++) out.push(buf[k]);
        }
        buf = [];
        counterCount = 0;
      }
      for (var i = 0; i < srcLines.length; i++) {
        var t = srcLines[i].trim();
        if (!t) {
          if (counterCount > 0) buf.push(srcLines[i]);
          else out.push(srcLines[i]);
          continue;
        }
        if (isCounterLine(t)) {
          buf.push(srcLines[i]);
          counterCount++;
          continue;
        }
        // Non-counter, non-blank: end of current run
        flush();
        out.push(srcLines[i]);
      }
      flush();
      return out;
    }
    function cleanupXcomChrome(text) {
      if (!isXcomHost()) return text;
      var lines = text.split('\\n');

      // ── Step 0: drop X.com chrome strings anywhere in the text ──
      // Handles the "Want to publish your own Article? Upgrade to Premium"
      // banner X shows on every Article page to non-Premium viewers, plus
      // engagement prompts ("Read N replies", "Show this thread") that
      // get rendered inline with the post.
      lines = lines.filter(function(line) { return !isXcomChromeLine(line.trim()); });

      // ── Step 0.5: drop counter runs from the pre-timestamp region ──
      // X.com renders engagement counter blocks (5/16/49/43K) in
      // multiple positions: hover-preview at top, metric strip between
      // title and body. Strip any ≥3-counter run before the timestamp.
      // The post-bottom counters are kept separate by the footer
      // consolidation; they're not in the pre-timestamp region.
      var preTsIdx = -1;
      for (var pi = lines.length - 1; pi >= 0; pi--) {
        if (TIMESTAMP_RE.test(lines[pi])) { preTsIdx = pi; break; }
      }
      if (preTsIdx > 0) {
        var preTs = dropCounterRuns(lines.slice(0, preTsIdx));
        lines = preTs.concat(lines.slice(preTsIdx));
      } else if (preTsIdx < 0) {
        // No timestamp in the captured text — apply to whole document.
        lines = dropCounterRuns(lines);
      }

      // ── Leading strip ──
      // Walk forward until the first line that looksLikeProse. Everything
      // before is chrome (counters, handles, URL paths, headers). This is
      // safe because the post title or body always has ≥4 words / ≥30
      // chars / sentence punctuation — none of which the chrome lines do.
      var startIdx = 0;
      while (startIdx < lines.length) {
        var raw = lines[startIdx].trim();
        if (looksLikeProseLine(raw)) break;
        startIdx++;
      }

      // ── Interstitial counter-run drop ──
      // X.com renders the engagement quartet (replies/RTs/likes/views)
      // BETWEEN the title and the body. After landing at the title, look
      // for ≥3 consecutive counter lines (blanks-skipped) and splice
      // them out. The bottom-of-post engagement quartet survives because
      // the trailing-truncation step handles it separately.
      var afterTitle = startIdx + 1;
      while (afterTitle < lines.length && !lines[afterTitle].trim()) afterTitle++;
      var counterRunEnd = afterTitle;
      var counterCount = 0;
      while (counterRunEnd < lines.length) {
        var ct = lines[counterRunEnd].trim();
        if (!ct) { counterRunEnd++; continue; }
        if (!isCounterLine(ct)) break;
        counterRunEnd++;
        counterCount++;
      }
      if (counterCount >= 3) {
        lines = lines.slice(0, afterTitle).concat(lines.slice(counterRunEnd));
      }

      // ── Trailing consolidation ──
      // Find the LAST timestamp signature; that's the end of the main
      // post. Then consolidate the timestamp + view count + engagement
      // counters into ONE readable footer line, replacing the fragmented
      // multi-line block that X.com renders (each glyph/word on its own
      // line). User-visible result is a clean "Posted: ... · ... · ..."
      // footer rather than a scattered list of disconnected numbers.
      var tsIdx = -1;
      for (var i = lines.length - 1; i >= startIdx; i--) {
        if (TIMESTAMP_RE.test(lines[i])) { tsIdx = i; break; }
      }
      var endIdx = lines.length;
      if (tsIdx >= 0) {
        var tsLine = lines[tsIdx].trim();
        var viewCountPhrase = '';    // e.g., "43.9K Views" (counter + label pair)
        var counters = [];            // engagement quartet: ["5","16","49","130"]
        var COUNTER_LABEL_RE = /^(views?|likes?|replies?|reposts?|retweets?|bookmarks?|comments?|shares?)$/i;
        var STOP_RE = /^(trending|show more|show this thread|more replies|reply|©|terms of service|privacy policy|cookie policy)/i;
        var j = tsIdx + 1;
        while (j < lines.length && counters.length < 4) {
          var t = lines[j].trim();
          if (!t || t === '·') { j++; continue; }
          if (STOP_RE.test(t)) break;
          if (/^##?\\s/.test(t)) break;
          if (isCounterLine(t)) {
            // Look ahead for a label — "43.9K" \\n "Views" is a single
            // view-count phrase, not two engagement counters.
            var k = j + 1;
            while (k < lines.length) {
              var nt = lines[k].trim();
              if (!nt || nt === '·') { k++; continue; }
              break;
            }
            if (k < lines.length && COUNTER_LABEL_RE.test(lines[k].trim()) && !viewCountPhrase) {
              viewCountPhrase = t + ' ' + lines[k].trim();
              j = k + 1;
              continue;
            }
            counters.push(t);
            j++;
            continue;
          }
          // Standalone non-counter word that wasn't paired with a counter
          // — usually stray chrome that slipped past the leading filter
          // (e.g., a "·" rendered as a word). Skip it.
          j++;
        }
        // Build the consolidated footer. Per user direction: keep ONLY
        // the timestamp + view-count phrase (e.g. "43.9K Views"). Drop
        // the engagement quartet (replies/RTs/likes/bookmarks) — those
        // counters are X chrome that doesn't carry information about
        // the post itself, just about reader activity. We still collect
        // them above (to find the end of the trailing block) but don't
        // emit them in the footer string.
        var footerParts = [tsLine];
        if (viewCountPhrase) footerParts.push(viewCountPhrase);
        var footer = footerParts.join(' · ');
        // Trim trailing blank lines from the body before appending footer
        var bodyEnd = tsIdx;
        while (bodyEnd > startIdx && !lines[bodyEnd - 1].trim()) bodyEnd--;
        var prefix = lines.slice(startIdx, bodyEnd);
        return prefix.concat(['', footer]).join('\\n');
      }
      return lines.slice(startIdx, endIdx).join('\\n');
    }

    // ── Extract: X.com tweets first (focused), then article/main/body ──
    var text = '';
    var tweets = document.querySelectorAll('[data-testid="tweetText"]');
    if (tweets.length > 0) {
      var tweetMd = [];
      for (var t = 0; t < tweets.length; t++) tweetMd.push(walk(tweets[t]).trim());
      text = tweetMd.join('\\n\\n');
    } else {
      // No tweetText nodes (X.com Article view, or page that's not
      // yet hydrated): prefer the main article wrapper over the whole
      // body. Falls back to body if no article element exists.
      var article = document.querySelector('article[data-testid="tweet"][role="article"]')
                 || document.querySelector('article[role="article"]')
                 || document.querySelector('main article')
                 || document.querySelector('article, main, [role="main"]')
                 || document.body;
      text = walk(article);
    }
    text = cleanupXcomChrome(text);
    text = normalizeBlankRuns(normalizeUnicodeBullets(collapseMathExplosion(text)));

    // ── Image extraction with chrome-URL filtering ──
    // For X.com captures, filter out URLs that are obvious chrome:
    //   - /profile_images/  → author avatar (chrome)
    //   - abs.twimg.com/hashflags/  → branded hashtag images (chrome)
    //   - abs.twimg.com/emoji/      → emoji renderings (chrome)
    //   - /card_img/        → link-preview thumbnails (questionable; keep)
    // Content images live at pbs.twimg.com/media/.
    function isXcomChromeImage(src) {
      if (/\\/profile_images\\//i.test(src)) return true;
      if (/abs\\.twimg\\.com\\/hashflags\\//i.test(src)) return true;
      if (/abs\\.twimg\\.com\\/emoji\\//i.test(src)) return true;
      return false;
    }
    var scope = document.querySelector('article, main, [role="main"]') || document.body;
    var seen = {};
    var images = [];
    var hostIsXcom = isXcomHost();
    Array.from(scope.querySelectorAll('img')).forEach(function(img) {
      var src = img.currentSrc || img.src || '';
      if (!src || src.indexOf('http') !== 0) return;
      if (seen[src]) return;
      var w = img.naturalWidth || img.width || 0;
      var h = img.naturalHeight || img.height || 0;
      if (w > 0 && w < 50) return;
      if (h > 0 && h < 50) return;
      if (hostIsXcom && isXcomChromeImage(src)) return;
      seen[src] = 1;
      images.push({ src: src, alt: (img.alt || '').trim() || 'Image' });
    });

    return { title: title, text: text, images: images };
  })()
`;

let _pythonCmd = null;
let _pythonValidated = false;  // true iff pythonCmd() found a candidate that
                               // passed validation (vs. falling back to a
                               // default that may not work) — drives the
                               // load-time "run kb doctor --fix" guidance.

// Validate an interpreter can actually run Athena's ingest: Python >= 3.11
// (arcus floor) AND a working pydantic/pydantic-core pairing. The second check
// matters because Obsidian launches from a minimal GUI PATH and may resolve a
// `python3` that is too old OR has a corrupt pydantic install (witnessed:
// host python3.14 with pydantic-core mismatch) — which silently breaks clip
// ingest. An absolute-path candidate (the ~/.athena/venv interpreter) bypasses
// the PATH problem entirely.
function _pythonInterpreterWorks(cmd) {
  try {
    // Short timeout: `python -c "import pydantic"` is fast; a missing
    // interpreter ENOENTs instantly. The cap only bounds a hung interpreter,
    // and pythonCmd() runs this synchronously on the UI thread, so keep it
    // small (worst case ~3s × candidates, once, cached thereafter).
    execFileSync(cmd,
      ["-c", "import sys; assert sys.version_info >= (3, 11); import pydantic"],
      { stdio: "ignore", timeout: 3000 });
    return true;
  } catch {
    return false;
  }
}

// Candidate interpreters, best first. ATHENA_PYTHON (explicit override) wins if
// it works; then Athena's own venv (absolute path — immune to the GUI PATH gap);
// then versioned names; then bare python3/python.
function _pythonCandidates() {
  const home = os.homedir();
  const win = process.platform === "win32";
  const candidates = [];
  if (process.env.ATHENA_PYTHON) candidates.push(process.env.ATHENA_PYTHON);
  candidates.push(win
    ? path.join(home, ".athena", "venv", "Scripts", "python.exe")
    : path.join(home, ".athena", "venv", "bin", "python"));
  candidates.push(...(win
    ? ["python", "py"]
    : ["python3.13", "python3.12", "python3.11", "python3"]));
  return candidates;
}

const _PY_VALIDATE_ARGS = ["-c", "import sys; assert sys.version_info >= (3, 11); import pydantic"];

function pythonCmd() {
  if (_pythonCmd) return _pythonCmd;
  // We pick the first candidate that PASSES validation rather than blindly
  // trusting `python3`, so a broken/too-old host interpreter no longer breaks
  // ingest. NOTE: this is synchronous (callers need the path before spawning);
  // the load-time preflight uses the async variant so it never blocks the UI.
  const win = process.platform === "win32";
  for (const c of _pythonCandidates()) {
    if (_pythonInterpreterWorks(c)) { _pythonCmd = c; _pythonValidated = true; return _pythonCmd; }
  }
  // Nothing validated — fall back to the configured/default so the eventual
  // spawn error is actionable rather than silent. _pythonValidated stays false
  // so the plugin can surface the "run kb doctor --fix" guidance.
  _pythonValidated = false;
  _pythonCmd = process.env.ATHENA_PYTHON || (win ? "python" : "python3");
  return _pythonCmd;
}

// Returns true iff pythonCmd() resolved to an interpreter that PASSED
// validation. Triggers resolution (cached) on first call.
function pythonInterpreterValidated() {
  pythonCmd();
  return _pythonValidated;
}

// Resolve a path to one of Athena's bundled Python sources. As of 1.0.9
// the build copies bin/lib/ + bin/config/ into the plugin's install
// directory so the JS plugin can spawn them without requiring the user
// to clone the full Athena vault. Fallback path (vault root) preserves
// the old "vault IS the Athena source tree" workflow for dev vaults
// and for users who used the --full-vault flag in earlier releases.
//
// `plugin` argument is the Athena plugin instance (we need this.manifest
// and this.app.vault.adapter.basePath). Returns an absolute path that
// is guaranteed to exist if either source location has the file, OR
// returns the plugin-dir path (which may not exist) as a deterministic
// default — the spawn will then ENOENT and the caller surfaces the
// honest "synthesis was skipped" message.
function resolvePythonScript(plugin, relPath) {
  const vaultPath = plugin.app.vault.adapter.basePath;
  // Plugin's install dir. manifest.dir is documented to be a vault-relative
  // path on most Obsidian versions; treat it as such with a defensive fallback.
  const pluginRel = (plugin.manifest && plugin.manifest.dir)
    || `.obsidian/plugins/${(plugin.manifest && plugin.manifest.id) || "athena"}`;
  const pluginAbs = path.isAbsolute(pluginRel)
    ? pluginRel
    : path.join(vaultPath, pluginRel);
  const inPlugin = path.join(pluginAbs, relPath);
  if (fs.existsSync(inPlugin)) return inPlugin;
  // Fallback: vault-root layout (Athena dev vault or pre-1.0.9
  // --full-vault deploys). If neither location has the file, return
  // the plugin-dir path so the error message is deterministic.
  const inVault = path.join(vaultPath, relPath);
  if (fs.existsSync(inVault)) return inVault;
  return inPlugin;
}
const { TOOL_STATUS_KB } = require("./kb-constants");
const {
  KB_COMMANDS, STATUS_MAP, DONE_STATUS_MAP, detectMechanicalCommand,
} = require("./kb-commands");
// 1.1: JS-side wiki synthesis. Replaces the macOS-only launchd dependency
// (com.athena.autoingest) for fill-in-the-placeholder behavior with an
// inline call through Gryphon's provider abstraction — works on Linux
// + Windows + macOS for whichever LLM the user has configured for chat.
const {
  synthesizeWikiPage, findPendingPages,
} = require("./synthesis");

const VIEW_TYPE = "athena-view";
const ICON = "brain";

// ── Athena-specific CLI args for the persistent Claude process ─────

const ALLOWED_TOOLS = [
  "Bash", "Read", "Write", "Edit", "Glob", "Grep",
  "WebFetch", "WebSearch",
  "mcp__athena__kb_add", "mcp__athena__kb_add_content",
  "mcp__athena__kb_create", "mcp__athena__kb_export",
  "mcp__athena__kb_index", "mcp__athena__kb_insight",
  "mcp__athena__kb_journal", "mcp__athena__kb_lint", "mcp__athena__kb_list",
  "mcp__athena__kb_merge", "mcp__athena__kb_move",
  "mcp__athena__kb_purge", "mcp__athena__kb_query",
  "mcp__athena__kb_reflect", "mcp__athena__kb_remove",
  "mcp__athena__kb_rename", "mcp__athena__kb_search",
  "mcp__athena__kb_stats", "mcp__athena__kb_trash",
  "mcp__athena__kb_undo", "mcp__athena__kb_ungroup",
];

const ATHENA_SYSTEM_PROMPT =
  "You are running inside the Athena vault via the Athena Obsidian plugin. " +
  "This IS the Athena session. Follow the rules in CLAUDE.md. " +
  "IMPORTANT RULES FOR THIS SESSION: " +
  "1) Wiki pages in wiki/format/ are the AUTHORITATIVE source. Always check them before raw files. " +
  "Never report a page as auth-blocked or thin without checking if a wiki page exists for that URL. " +
  "2) When the user asks to analyze, research, update, or compare content, SEARCH for relevant existing " +
  "insight/analysis pages first (wiki/insights/, wiki/comparisons/). If you find a match, ask the user: " +
  "'I found [[Page Name]] \u2014 should I update this, or create a new analysis?' " +
  "If no existing page matches, offer to create a new insight page. " +
  "3) Use [[wikilinks]] for all page references so they are clickable in Obsidian. " +
  "4) WIKI PAGE SUMMARIES: when creating or updating a wiki page, the `summary` " +
  "frontmatter field MUST be 200-400 characters (2-3 sentences max), self-contained " +
  "and scannable. The summary is rendered in dashboards (Recently Added, Browse by " +
  "Tag, etc.) where space is constrained. Long descriptions belong in the body, not " +
  "the summary. NEVER let summary exceed 500 characters \u2014 the page-builder's " +
  "1500-char hard cap is a safety net, not a target. Short summaries make the " +
  "knowledge base scannable; long summaries make dashboards unusable.";

// ── Athena autocomplete source ─────────────────────────────────────
//
// Plugs into GryphonChatView's autocomplete-source registry. Core handles
// "/" input before this source is consulted, so this only fires on `kb ...`.
// Matches on startsWith OR substring — users often remember a word from
// the command without the leading `kb`.

const athenaKbAutocompleteSource = {
  name: "athena-kb",
  matches: (text) => text.toLowerCase().startsWith("kb"),
  suggest: (text) => {
    const query = text.toLowerCase();
    return KB_COMMANDS.filter((c) =>
      c.cmd.toLowerCase().startsWith(query) || c.cmd.toLowerCase().includes(query)
    );
  },
};

// ── AthenaPlugin — KB orchestration lives here ─────────────────────

class AthenaPlugin extends Plugin {
  async onload() {
    console.log("[athena] Plugin loaded \u2014 version", (this.manifest && this.manifest.version) || "?");

    // Default settings SYNCHRONOUSLY before any await below. runMechanical
    // (and other methods) are reachable via window.app the instant the plugin
    // object exists \u2014 e.g. a fast command, or a vm-test spec \u2014 which can land
    // during onload's async init (notably the `await disableFn` / loadSettings
    // gap on a slow guest). Without this, `this.settings` is undefined and
    // runMechanical throws on `this.settings.connectionTimeoutMs`. loadSettings()
    // below refines this with persisted values.
    this.settings = Object.assign({}, DEFAULT_SETTINGS);

    // Mutual exclusivity: Athena includes all Gryphon features, disable
    // Gryphon if enabled. Use disablePluginAndSave so the change persists
    // across restarts — disablePlugin alone is in-memory only and Gryphon
    // would re-enable on the next Obsidian launch.
    if (this.app.plugins.enabledPlugins.has("gryphon")) {
      const plugins = this.app.plugins;
      const disableFn = plugins.disablePluginAndSave
        ? plugins.disablePluginAndSave.bind(plugins)
        : plugins.disablePlugin.bind(plugins);
      try {
        await disableFn("gryphon");
        // Confirm the disable actually took effect before claiming so.
        if (plugins.enabledPlugins.has("gryphon")) {
          console.warn("[athena] disable returned but Gryphon is still in enabledPlugins");
          new Notice(
            "Athena: could not disable Gryphon automatically. " +
            "Please disable it manually in Settings \u2192 Community plugins.",
            8000
          );
        } else {
          new Notice(
            "Athena includes Gryphon features \u2014 Gryphon has been disabled.",
            5000
          );
        }
      } catch (e) {
        console.warn("[athena] could not disable gryphon plugin:", e && e.message);
        new Notice(
          "Athena: could not disable Gryphon automatically. " +
          "Please disable it manually in Settings \u2192 Community plugins.",
          8000
        );
      }
    }

    await this.loadSettings();

    this.skillRegistry = new SkillRegistry(this.app);
    this.skillRegistry.init().catch((e) =>
      console.warn("[athena] SkillRegistry init failed:", e)
    );

    // Ensure each configured Web Clipper target directory exists, then
    // attach a watcher to each one. Paths come from a comma-separated
    // setting so users can cover both Obsidian Web Clipper's factory
    // default (`clippings/` at vault root) and any legacy or custom
    // location simultaneously.
    this._clipWatchers = [];
    for (const clipDir of this._resolveClipDirs()) {
      try {
        if (!fs.existsSync(clipDir)) fs.mkdirSync(clipDir, { recursive: true });
      } catch {}
      this._setupClipWatcher(clipDir);
    }

    // Ensure Athena's three-layer dir structure exists in the vault.
    // The Python backend creates these on first kb run, but Community-
    // Plugins / BRAT users start from a vault that has none of them. A
    // browser-captured raw file would otherwise hit ENOENT on write,
    // surfacing as a confusing "KB command error" in chat. Belt-and-
    // suspenders: each writeFileSync in the ingest path also calls
    // mkdirSync({recursive:true}) before writing, so even if a category
    // is missed here the write still succeeds.
    const vaultRoot = this.app.vault.adapter.basePath;
    for (const sub of [
      "raw/webpages/artifacts",
      "raw/papers/artifacts",
      "raw/repos/artifacts",
      "raw/videos/artifacts",
      "inbox",
    ]) {
      try {
        const dir = path.join(vaultRoot, sub);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      } catch {}
    }
    // 1.0.10: create inbox/url-resolved.tsv as empty on first run if
    // missing. Read sites inside the ingest path catch ENOENT, but the
    // catch logs `[athena] ingest: url-resolved update failed: ENOENT...`
    // every time — noise that masks real failures. Same pattern as 1.0.5
    // for url-new.txt.
    try {
      const tsv = path.join(vaultRoot, "inbox", "url-resolved.tsv");
      if (!fs.existsSync(tsv)) fs.writeFileSync(tsv, "");
    } catch {}

    // Watch inbox/url-new.txt for new URLs
    this._setupUrlNewWatcher();

    // First-run setup wizard
    if (!this.settings._setupComplete) {
      const cp = this.settings.claudePath || findClaudeBinary();
      if (!cp) {
        new Notice("Athena: Claude Code CLI not found. Set the path in Settings > Athena.", 10000);
      }
      const vaultName = this.app.vault.getName();
      new AthenaSetupWizard(this.app, vaultName).open();
      this.settings._setupComplete = true;
      await this.saveSettings();
    }

    // Register the view — composition for behavior, thin subclass for
    // brand text. Athena configures the chat view through the options
    // bag (Gryphon's documented extension API) and uses AthenaChatView
    // (subclass of GryphonChatView) solely to swap welcome-panel
    // strings — see src/athena/athena-chat-view.js. Extension points:
    //   autocompleteSources — adds `kb ...` completions next to core's `/`
    //   stopStreamingHooks  — kills mechanical subprocess + browser capture
    this.registerView(VIEW_TYPE, (leaf) => {
      // extraProcessArgs are Claude-Code-CLI-specific flags. Other providers
      // (Codex CLI, Gemini CLI, *-api) reject these and the spawn fails.
      // Only pass them when Claude Code is the active provider. See #121.
      // For non-Claude providers, Athena loses the safety guardrails that
      // these flags express (allowedTools allowlist, system-prompt append).
      // That's a real loss; tracked in #39 (Gryphon-side intent translation).
      const provider = this.settings.providerPreference || "auto";
      const usesClaudeCodeCLI = provider === "claude-code" || provider === "auto";
      const claudeCodeArgs = usesClaudeCodeCLI
        ? [
            "--disable-slash-commands",
            "--allowedTools", ...ALLOWED_TOOLS,
            "--append-system-prompt", ATHENA_SYSTEM_PROMPT,
          ]
        : [];
      return new AthenaChatView(leaf, this, {
        viewType: VIEW_TYPE,
        displayText: "Athena",
        icon: ICON,
        extraToolStatus: TOOL_STATUS_KB,
        extraProcessArgs: claudeCodeArgs,
        onBeforeSend: (text) => this._handleKbCommand(text),
        autocompleteSources: [athenaKbAutocompleteSource],
        stopStreamingHooks: [
          (view) => {
            if (view._mechanicalProc) {
              try { view._mechanicalProc.kill("SIGTERM"); } catch {}
              view._mechanicalProc = null;
            }
          },
          () => this.abortBrowserCapture(),
        ],
      });
    });

    this.addRibbonIcon(ICON, "Open Athena", () => this.activateView());
    this.addRibbonIcon("search", "Athena Search", () => {
      new SearchModal(this.app, this.app.vault.adapter.basePath).open();
    });

    this.addCommand({ id: "open-chat", name: "Open chat", callback: () => this.activateView() });

    // Mirrors Gryphon's hotkey path. Uses `callback` (not `editorCallback`)
    // so the command is available from Reading mode too. Cascades through
    // three selection sources — see _pickSelectionForInjection.
    this.addCommand({
      id: "quote-highlight-into-chat",
      name: "Quote highlighted text into chat",
      callback: async () => {
        const picked = this._pickSelectionForInjection();
        if (!picked) {
          new Notice("Athena: no text selected");
          return;
        }
        await this.activateView();
        const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
        const chatView = leaves[0] && leaves[0].view;
        if (chatView && typeof chatView.insertSelectionIntoInput === "function") {
          chatView.insertSelectionIntoInput(picked.text, picked.file);
        } else {
          new Notice("Athena: chat view not available");
        }
      },
    });

    this.addCommand({
      id: "open-inbox",
      name: "Open URL inbox",
      callback: async () => {
        const mdPath = "inbox/Add URLs.md";
        const txtPath = "inbox/url-new.txt";
        let file = this.app.vault.getAbstractFileByPath(mdPath);
        if (!file) {
          const header = `Paste URLs below (one per line). Athena processes them automatically.\n\n---\n\n`;
          let existing = "";
          try { existing = fs.readFileSync(path.join(this.app.vault.adapter.basePath, txtPath), "utf8").trim(); } catch {}
          await this.app.vault.create(mdPath, header + existing);
          file = this.app.vault.getAbstractFileByPath(mdPath);
        }
        if (file) {
          const leaf = this.app.workspace.getLeaf(false);
          await leaf.openFile(file);
        }
      },
    });

    // Sync: when Add URLs.md is modified, extract URLs to url-new.txt
    this.registerEvent(this.app.vault.on("modify", (file) => {
      if (file.path === "inbox/Add URLs.md") {
        setTimeout(async () => {
          try {
            const content = await this.app.vault.read(file);
            const parts = content.split("---");
            const urlSection = parts.length > 1 ? parts.slice(1).join("---") : content;
            // Sanitize URLs against markdown syntax leaks. Users routinely
            // paste URLs from rendered markdown / formatted post text where
            // the URL is wrapped in **bold**, [link](url), `code`, etc.
            // Without stripping, the URL kept in url-new.txt becomes
            // "https://lnkd.in/X**" or "[https://x](https://x)" — kb-capture
            // then fetches the malformed URL, gets a 404 / interstitial,
            // and creates a sparse wiki page with garbage title and URL.
            // Bug class surfaced 0.10.16 (Discord/decodingtrust/arxiv sparse pages).
            const sanitizeUrl = (raw) => {
              let u = raw.trim();
              // Strip markdown link form: [https://x](https://x) → https://x
              const linkForm = u.match(/^\[(https?:\/\/[^\]]+)\]\((https?:\/\/[^)]+)\)/);
              if (linkForm) {
                u = linkForm[2];  // prefer the parenthesized URL (the actual target)
              }
              // Strip surrounding parens/brackets/braces/backticks/quotes
              u = u.replace(/^[\[\(\{`'"<]+|[\]\)\}`'">]+$/g, "");
              // Strip trailing markdown emphasis (bold/italic) and punctuation
              // Common forms: **, __, *, _, ., ,, ;, !, ?, …
              while (u.length && /[\*_.,;!?…]$/.test(u)) {
                u = u.slice(0, -1);
              }
              return u;
            };
            const urls = urlSection
              .split("\n")
              .map(sanitizeUrl)
              .filter(l => l.startsWith("http"));
            if (urls.length > 0) {
              const txtPath = path.join(this.app.vault.adapter.basePath, "inbox", "url-new.txt");
              fs.writeFileSync(txtPath, urls.join("\n") + "\n");
              const header = content.split("---")[0] + "---\n\n";
              await this.app.vault.modify(file, header);
            }
          } catch (e) { console.log("[athena] Add URLs sync error:", e.message); }
        }, 2000);
      }
    }));

    this.addCommand({
      id: "new-session",
      name: "New session",
      callback: () => {
        const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
        if (leaves.length > 0) {
          leaves[0].view.handleChatCommand("/clear");
          this.app.workspace.revealLeaf(leaves[0]);
        } else {
          this.activateView();
        }
      },
    });

    // kb commands in Obsidian command palette
    const sendKbCommand = async (cmd) => {
      await this.activateView();
      const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
      if (leaves.length > 0) {
        const view = leaves[0].view;
        view.inputEl.value = cmd;
        view.sendMessage();
      }
    };

    const paletteCommands = [
      { id: "kb-stats",        name: "KB: Show stats",              cmd: "kb stats" },
      { id: "kb-lint",         name: "KB: Health check (lint)",     cmd: "kb lint" },
      { id: "kb-list",         name: "KB: List all pages",          cmd: "kb list" },
      { id: "kb-list-topics",  name: "KB: List topics",             cmd: "kb list --topics" },
      { id: "kb-list-insights",name: "KB: List insights",           cmd: "kb list --insights" },
      { id: "kb-list-projects",name: "KB: List projects",           cmd: "kb list --projects" },
      { id: "kb-list-recent",  name: "KB: Recently added",          cmd: "kb list --recent" },
      { id: "kb-search",       name: "KB: Search (chat)",           cmd: "kb search " },
      { id: "kb-rules",        name: "KB: Show processing rules",   cmd: "kb rules" },
      { id: "kb-reflect",      name: "KB: Reflect on journal",      cmd: "kb reflect" },
      { id: "kb-index",        name: "KB: Rebuild search index",    cmd: "kb index" },
    ];

    for (const pc of paletteCommands) {
      this.addCommand({
        id: pc.id,
        name: pc.name,
        callback: () => {
          if (pc.cmd.endsWith(" ")) {
            this.activateView().then(() => {
              const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
              if (leaves.length > 0) {
                const view = leaves[0].view;
                view.inputEl.value = pc.cmd;
                view.inputEl.focus();
              }
            });
          } else {
            sendKbCommand(pc.cmd);
          }
        },
      });
    }

    this.addCommand({
      id: "search-modal",
      name: "Search knowledge base",
      callback: () => new SearchModal(this.app, this.app.vault.adapter.basePath).open(),
    });

    this.addCommand({
      id: "setup-wizard",
      name: "Setup wizard (Web Clipper configuration)",
      callback: () => {
        new AthenaSetupWizard(this.app, this.app.vault.getName()).open();
      },
    });

    this.addSettingTab(new AthenaSettingTab(this.app, this));

    // Watchdog — initial check shortly after UI loads + periodic
    setTimeout(() => this._watchdogCheck(), 5000);
    this._watchdogInterval = setInterval(() => this._watchdogCheck(), 60000);

    // Environment preflight — if no working Python interpreter validated,
    // surface the one-command fix instead of letting the first clip fail with
    // a cryptic spawn error. One-time, non-blocking.
    setTimeout(() => this._warnIfPythonBroken(), 7000);
  }

  /** Obtain an Athena view to ingest a clip into, opening the panel if needed
   *  but BOUNDING the auto-open attempts per clip. A vault where activateView
   *  can't place a leaf would otherwise re-open + retry every 60s forever. After
   *  the cap we stop forcing the panel open (we still use one if the user opens
   *  it) and surface a single Notice. Returns the view or null. */
  async _tryOpenViewForClip(filePath) {
    let view = this._getActiveView();
    if (view) return view;
    if (!this._clipOpenTries) this._clipOpenTries = new Map();
    const tries = this._clipOpenTries.get(filePath) || 0;
    if (tries < 3) {
      this._clipOpenTries.set(filePath, tries + 1);
      try { await this.activateView(); } catch {}
      return this._getActiveView();
    }
    if (!this._clipOpenWarned) {
      this._clipOpenWarned = true;
      new Notice("Athena: open the Athena panel to finish processing saved clips.", 10000);
    }
    return null;
  }

  /** One-time Notice when no validated Python interpreter was found, pointing
   *  the user at `kb doctor --fix`. Validates ASYNCHRONOUSLY so it never blocks
   *  the UI thread — the synchronous probe in pythonCmd() could otherwise stall
   *  rendering on a slow/broken-python machine if it ran here. Fires at most once. */
  async _warnIfPythonBroken() {
    if (this._pythonWarned) return;
    try {
      if (await this._anyValidatedPythonAsync()) return;  // a good interpreter exists
      if (this._pythonWarned) return;
      this._pythonWarned = true;
      new Notice(
        "Athena: no working Python found (need 3.11+ with arcus/pydantic). " +
        "Run `kb doctor --fix` in a terminal, or set ATHENA_PYTHON.",
        15000,
      );
    } catch { /* preflight is best-effort */ }
  }

  /** Async (non-UI-blocking) probe: does any candidate interpreter validate
   *  (Python >= 3.11 + importable pydantic)? Mirrors pythonCmd()'s candidate
   *  list but uses async execFile so it never freezes the renderer. */
  async _anyValidatedPythonAsync() {
    for (const c of _pythonCandidates()) {
      const ok = await new Promise((resolve) => {
        try {
          execFile(c, _PY_VALIDATE_ARGS, { timeout: 3000 }, (err) => resolve(!err));
        } catch { resolve(false); }
      });
      if (ok) return true;
    }
    return false;
  }

  async onunload() {
    // Abort any in-flight browser capture so hidden webview/BrowserWindow is cleaned up
    if (typeof this.abortBrowserCapture === "function") {
      try { this.abortBrowserCapture(); } catch {}
    }
    for (const w of (this._clipWatchers || [])) {
      try { w.watcher.close(); } catch {}
    }
    this._clipWatchers = [];
    if (this._urlNewWatcher) { try { this._urlNewWatcher.close(); } catch {} }
    if (this._urlNewTimer) clearTimeout(this._urlNewTimer);
    if (this._watchdogInterval) clearInterval(this._watchdogInterval);
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE)) {
      if (leaf.view.claudeProcess) leaf.view.claudeProcess.abort();
    }
    if (this.skillRegistry) this.skillRegistry.unload();
  }

  async activateView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      const nl = this.settings.openInMainTab ? workspace.getLeaf("tab") : workspace.getRightLeaf(false);
      if (nl) { await nl.setViewState({ type: VIEW_TYPE, active: true }); leaf = nl; }
    }
    if (leaf) {
      workspace.revealLeaf(leaf);
      requestAnimationFrame(() => { if (leaf.view.inputEl) leaf.view.inputEl.focus(); });
    }
  }

  /**
   * Cascade through selection sources for the "insert selection" command.
   * Order: chat view's cached selection → active editor selection →
   * current window DOM selection. First hit wins. Mirrors the same
   * helper on GryphonPlugin; kept symmetric so both plugins behave the
   * same way. Returns {text, file} or null.
   */
  _pickSelectionForInjection() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    const viewCache = leaves[0] && leaves[0].view && leaves[0].view._cachedSelection;
    if (viewCache && viewCache.text) {
      return { text: viewCache.text, file: viewCache.file };
    }
    const { MarkdownView } = require("obsidian");
    const mdView = this.app.workspace.getActiveViewOfType(MarkdownView);
    if (mdView && mdView.editor) {
      const sel = mdView.editor.getSelection();
      if (sel) return { text: sel, file: mdView.file || null };
    }
    const winSel = document.getSelection();
    if (winSel && !winSel.isCollapsed) {
      const text = winSel.toString();
      if (text) return { text, file: this.app.workspace.getActiveFile() };
    }
    return null;
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
    // Athena has its own guardrails (--allowedTools whitelist + system
    // prompt + scoped MCP). Gryphon's protected-mode IPC infrastructure
    // (GRYPHON_PERMISSION_SOCKET, hook-settings.json, IPC server lifecycle)
    // is not wired up on AthenaPlugin, so leaving protectedMode ON would
    // cause the CC provider to spawn with a dead socket path and fail.
    // Force these off here every load — saving back to disk so a future
    // toggle in Gryphon Settings (if Gryphon is ever re-enabled separately)
    // doesn't drift Athena's runtime back into a broken state.
    this.settings.protectedMode = false;
    this.settings.protectedPathsEnabled = false;
    this.settings.protectedCommandsEnabled = false;
  }

  async saveSettings() {
    await this.saveData(this.settings);
    // Issue #132: notify the GryphonChatView mounted inside Athena's panel
    // that settings changed, so it can refresh the toolbar model/effort/
    // permission badges. Gryphon-as-a-plugin fires this event from its own
    // saveSettings (#40), but Athena is a consumer with its OWN settings
    // tab + saveSettings — without this, Athena's panel badges stay frozen
    // at the values present when the view was first opened.
    if (this.app && this.app.workspace && typeof this.app.workspace.trigger === "function") {
      this.app.workspace.trigger("gryphon:settings-changed", this.settings);
    }
  }

  // GryphonChatView's send pipeline calls plugin.ensureIpcListening before
  // every spawn to confirm the permission-classification IPC server is up.
  // Athena doesn't run that server (see loadSettings — protected-mode is
  // disabled), so we return true to let chat-view proceed without firing
  // the "guardrail IPC offline" notice. Returning false would also work
  // (the notice is gated on providerPreference === "claude-code" and
  // Athena defaults to "auto"), but true is the honest answer for
  // "is the permission server in a state that won't break the spawn?"
  // — yes, because we configured the spawn to not use it.
  async ensureIpcListening(_timeoutMs) {
    return true;
  }

  _getActiveView() {
    const leaves = this.app.workspace.getLeavesOfType(VIEW_TYPE);
    return leaves.length > 0 ? leaves[0].view : null;
  }

  // ── onBeforeSend hook: intercept mechanical KB commands ──────────

  _handleKbCommand(text) {
    const mechanical = detectMechanicalCommand(text);
    if (!mechanical) return false;

    const view = this._getActiveView();
    if (!view) return false;  // no view to render into — let it pass through

    // Dispatch async — don't await. Hook must return synchronously.
    this._runKbCommandAsync(text, mechanical, view).catch((e) => {
      console.error("[athena] KB command error:", e);
      try {
        view.addSystemMessage("KB command error: " + (e && e.message ? e.message : String(e)));
      } catch {}
      view.isStreaming = false;
      // Per Gryphon issue #3: input stays enabled while streaming. Don't
      // re-disable here either — Gryphon's queue logic gates new sends
      // off view.isStreaming alone.
    });

    return true;  // consumed
  }

  async _runKbCommandAsync(text, mechanical, view) {
    view.addUserMessage(text, "mechanical");
    view.isStreaming = true;
    // Per Gryphon issue #3: don't disable inputEl. Gryphon's queue
    // logic gates new sends off view.isStreaming so the user can type
    // and queue a follow-up while a KB command runs.

    try {
      // kb add: capture is mechanical ($0)
      if (mechanical.command === "add") {
        console.log("[athena] kb add start", { args: mechanical.args });
        view.startStreamingMessage();
        // Strip surrounding quotes — same regex-capture-with-quotes
        // class of bug as _kbRemove/_kbRename. A user typing
        // `kb add "https://x.com/foo"` lands here with literal quote
        // chars in args[0], which then poisons canonicalize() and
        // every downstream lookup. Defensive strip keeps the URL
        // path working regardless of how the user typed it.
        const rawUrlArg = (mechanical.args[0] || "").trim().replace(/^["']|["']$/g, "");
        const hasUrl = rawUrlArg.length > 0;

        if (!hasUrl) {
          await this._kbAddNoArgs(view);
          view.addCostInfo(0, null);
          if (view.inputEl) view.inputEl.focus();
          return;
        }

        // URL-specific add
        const url = rawUrlArg;
        const result = await this.ingestContent({ url, source: "kb-add", view });

        if (result.status === "duplicate") {
          view.finalizeStreamingMessage(
            `**Already in knowledge base:** [[${result.dupPage}]]\n\n` +
            `Matched by ${result.dupMethod}.\n` +
            `To update with newer content, say "update [[${result.dupPage}]]"`,
            `Already captured: ${result.dupPage}`
          );
        } else if (result.status === "updated") {
          view.finalizeStreamingMessage(
            `**Updated:** [[${result.pageName}]]\n\nRaw content and wiki page refreshed with better content.` +
            (result.summary ? `\n\n${result.summary}` : ""),
            `Updated: ${result.pageName}`
          );
        } else if (result.status === "failed") {
          if (result.summary && result.summary.includes("authentication")) {
            view.finalizeStreamingMessage(
              `Could not capture full content (requires authentication).\n\n` +
              `**Options:**\n` +
              `1. Open the link in your browser, use Web Clipper, then \`kb add\`\n` +
              `2. Copy the text and paste it here\n` +
              `3. Say "skip" to move on`
            );
          } else {
            view.finalizeStreamingMessage(result.summary || "Capture failed.", result.summary || "Capture failed");
          }
        } else {
          // created
          const lines = [];
          // Detect the capture-only (no Python backend) case so the user
          // gets an honest message about what actually happened, instead
          // of the vague "Page added to knowledge base." we used to show
          // when synthesis silently failed on fresh-vault installs.
          // 1.0.9+: checks plugin-bundled location first, then vault-side.
          // resolvePythonScript returns the plugin-dir path even when neither
          // exists, so we wrap with fs.existsSync to detect the truly-absent
          // case (Python lib missing → capture-only message fires).
          const pyBackend = fs.existsSync(
            resolvePythonScript(this, "bin/lib/wiki_page.py")
          );
          if (result.pageName) {
            lines.push(`**Captured:** ${url}`);
            lines.push(`**Page created:** [[${result.pageName}]]`);
            if (result.summary) lines.push(`\n${result.summary}`);
            // 1.1: synthesis result is now part of the kb add flow. On
            // failure the page exists with the placeholder, so guide the
            // user to `kb regen` rather than re-running kb add.
            if (result.synthesis && !result.synthesis.ok) {
              lines.push("");
              if (result.synthesis.reason === "no-provider") {
                lines.push(`*Synthesis skipped:* no LLM provider configured. ${result.synthesis.detail || "Set up Claude Code or an API key in Settings → Gryphon."}`);
              } else {
                lines.push(`*Synthesis failed* (${result.synthesis.reason}). Run \`kb regen ${url}\` to retry.`);
              }
            }
          } else if (!pyBackend) {
            lines.push(`**Raw saved:** ${url}`);
            lines.push("");
            lines.push("Wiki synthesis was skipped — Athena's Python backend isn't installed in this vault. Capture-only mode is what Community Plugins users currently get.");
            lines.push("");
            lines.push("For full wiki synthesis, clone the Athena vault from https://github.com/polleoai/athena and open it in Obsidian instead.");
          } else {
            // Python backend IS present but produced no pageName. That
            // means wiki_page.py ran but failed (OSError, schema error,
            // pydantic error, etc.) — the previous "Page added to
            // knowledge base." was a lie that hid real failures. Now
            // surface this honestly so the user knows to check the
            // console for the actual error. Common 1.0.x cause: titles
            // with chars that are valid on macOS but invalid on Windows
            // (the `:`-in-filename bug fixed in 1.0.11).
            lines.push(`**Raw saved:** ${url}`);
            lines.push("");
            lines.push("Wiki synthesis ran but did not return a page name. The raw file was saved, but no wiki page was created — likely a Python error from `wiki_page.py`. Open the dev console (Ctrl+Shift+I) and look for `[athena] wiki_page.py stderr:` for the actual error.");
          }
          view.finalizeStreamingMessage(
            lines.join("\n"),
            result.pageName ? `Page created: ${result.pageName}`
              : (!pyBackend ? "Raw saved (capture-only mode)" : "Raw saved (synthesis error)")
          );
        }
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      // kb refresh <url>: regenerate an existing wiki page from its raw
      // source using the current code path. Bypasses dedup so stale
      // pages (e.g. created before a YAML/wikilink fix landed) can be
      // rewritten without manual file delete + re-add. JS-side
      // implementation = works on Windows where the bash `bin/kb` is
      // unavailable.
      if (mechanical.command === "refresh") {
        // Same defensive quote-strip as kb add — see kb add block above.
        const url = (mechanical.args[0] || "").trim().replace(/^["']|["']$/g, "");
        view.startStreamingMessage();
        view.updateStatus("Refreshing wiki page...");
        const result = await this._refreshWikiByUrl(url, view);
        if (result.status === "refreshed") {
          view.finalizeStreamingMessage(
            `**Refreshed:** [[${result.pageName}]]\n\nWiki page regenerated with current code's YAML + wikilink format. Raw source unchanged.`,
            `Refreshed: ${result.pageName}`
          );
        } else if (result.status === "not-found") {
          view.finalizeStreamingMessage(
            `No existing wiki page found for ${url}. Run \`kb add ${url}\` to capture it.`,
            "Page not found"
          );
        } else {
          view.finalizeStreamingMessage(
            result.error || "Refresh failed.",
            "Refresh failed"
          );
        }
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      // 1.1: kb regen — re-run synthesis on an existing wiki page (or
      // every page still showing "Pending synthesis"). Different from
      // kb refresh (rebuilds the page scaffolding) — regen targets only
      // the digest body. Useful for: (a) backfilling pages that hit a
      // synthesis failure during kb add, (b) regenerating after the
      // raw source was updated, (c) bulk backfill of pre-1.1 pages.
      if (mechanical.command === "regen") {
        // Same defensive quote-strip as kb add — see kb add block above.
        // `--all-pending` has no quotes so the strip is a no-op for it;
        // URL forms benefit from the strip.
        const arg = (mechanical.args[0] || "").trim().replace(/^["']|["']$/g, "");
        view.startStreamingMessage();
        if (arg === "--all-pending") {
          const pending = findPendingPages(this);
          if (pending.length === 0) {
            view.finalizeStreamingMessage(
              "No pages with the *Pending synthesis* placeholder. Everything's already digested.",
              "Nothing pending"
            );
            view.addCostInfo(0, null);
            if (view.inputEl) view.inputEl.focus();
            return;
          }
          view.updateStatus(`Synthesizing 0 / ${pending.length}...`);
          let ok = 0, failed = 0, totalCost = 0;
          const failures = [];
          for (let i = 0; i < pending.length; i++) {
            const wikiAbsPath = pending[i];
            view.updateStatus(`Synthesizing ${i + 1} / ${pending.length} — ${path.basename(wikiAbsPath, ".md")}`);
            const r = await synthesizeWikiPage(this, wikiAbsPath, {
              onStatus: () => {},  // outer status is per-page; inner is per-attempt
            });
            if (r.ok) { ok++; totalCost += r.cost || 0; }
            else {
              failed++;
              failures.push(`- [[${path.basename(wikiAbsPath, ".md")}]] — ${r.reason}`);
              // If the first page fails with no-provider, every page will
              // fail the same way. Stop early so the user doesn't wait
              // through N identical failures.
              if (r.reason === "no-provider") {
                view.finalizeStreamingMessage(
                  `Bulk regen aborted — no LLM provider configured.\n\n${r.detail || ""}`,
                  "No provider"
                );
                view.addCostInfo(0, null);
                if (view.inputEl) view.inputEl.focus();
                return;
              }
            }
          }
          const summary = [
            `**Bulk regen complete:** ${ok} succeeded, ${failed} failed.`,
            totalCost > 0 ? `Cost: $${totalCost.toFixed(4)}` : null,
            failures.length ? `\n**Failures:**\n${failures.slice(0, 10).join("\n")}` : null,
            failures.length > 10 ? `\n_(${failures.length - 10} more — see console.)_` : null,
          ].filter(Boolean).join("\n");
          view.finalizeStreamingMessage(summary, `Regen: ${ok}/${pending.length}`);
        } else {
          // Single-URL regen.
          const url = arg;
          view.updateStatus("Looking up wiki page...");
          const wikiAbsPath = this._lookupWikiPathByUrl(url);
          if (!wikiAbsPath) {
            view.finalizeStreamingMessage(
              `No existing wiki page found for ${url}. Run \`kb add ${url}\` first.`,
              "Page not found"
            );
            view.addCostInfo(0, null);
            if (view.inputEl) view.inputEl.focus();
            return;
          }
          const r = await synthesizeWikiPage(this, wikiAbsPath, {
            onStatus: (msg) => view.updateStatus(msg),
          });
          if (r.ok) {
            const pageName = path.basename(wikiAbsPath, ".md");
            view.finalizeStreamingMessage(
              `**Regenerated:** [[${pageName}]]\n\n${r.summary}`,
              `Regenerated: ${pageName}`
            );
          } else if (r.reason === "no-provider") {
            view.finalizeStreamingMessage(
              `No LLM provider configured.\n\n${r.detail || ""}`,
              "No provider"
            );
          } else {
            view.finalizeStreamingMessage(
              `Synthesis failed (${r.reason}). ${r.detail || ""}`,
              `Regen failed: ${r.reason}`
            );
          }
        }
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      // 1.0.17 consolidation: JS-implemented kb verbs that work
      // identically on macOS / Linux / Windows. The previous fallback
      // for these (bash `bin/kb`) didn't run on Windows AND for
      // unrecognized verbs the LLM would explore the codebase for 7+
      // turns to infer behavior. Both gone now — these run inline.
      if (mechanical.command === "remove") {
        view.startStreamingMessage();
        view.updateStatus("Removing page...");
        const result = await this._kbRemove(mechanical.args);
        view.finalizeStreamingMessage(result.message, result.summary || result.message.split("\n")[0]);
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }
      if (mechanical.command === "undo") {
        view.startStreamingMessage();
        view.updateStatus("Restoring from trash...");
        const result = await this._kbUndo();
        view.finalizeStreamingMessage(result.message, result.summary || result.message.split("\n")[0]);
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }
      if (mechanical.command === "trash") {
        view.startStreamingMessage();
        const result = await this._kbTrashList();
        view.finalizeStreamingMessage(result.message, "Trash listing");
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }
      if (mechanical.command === "rename") {
        view.startStreamingMessage();
        view.updateStatus("Renaming page...");
        const result = await this._kbRename(mechanical.args);
        view.finalizeStreamingMessage(result.message, result.summary || result.message.split("\n")[0]);
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      // All other kb commands: run `bin/kb <cmd> [args]` and format output
      view.startStreamingMessage();
      view.updateStatus(STATUS_MAP[mechanical.command] || "Processing...");
      const result = await this.runMechanical(mechanical.command, mechanical.args, null, view);
      const stdout = (result.stdout || "").trim();
      const stderr = (result.stderr || "").trim();

      if (!result.ok) {
        view.finalizeStreamingMessage(stderr || stdout || "Command failed.");
        view.addCostInfo(0, null);
        if (view.inputEl) view.inputEl.focus();
        return;
      }

      let output = stdout;
      switch (mechanical.command) {
        case "lint":
        case "stats":
        case "rules":
          // Already user-friendly
          break;
        case "search":
          if (!stdout) output = "No results found.";
          break;
        case "index":
          output = stdout || "Search index rebuilt.";
          break;
        case "journal": {
          const entryMatch = stdout.match(/Created:\s*(.+\.md)/);
          output = entryMatch
            ? `**Journal entry saved:** [[${entryMatch[1].replace(/\.md$/, "").split("/").pop()}]]`
            : stdout || "Journal entry saved.";
          break;
        }
        case "undo":
          output = stdout || "Nothing to undo.";
          break;
        case "purge":
          output = stdout || "Trash is empty \u2014 nothing to purge.";
          break;
        case "trash":
          output = stdout || "Trash is empty.";
          break;
        case "list":
          if (!stdout) output = "No pages found.";
          break;
        default:
          output = stdout || "Done.";
      }
      view.finalizeStreamingMessage(output, DONE_STATUS_MAP[mechanical.command] || "Done");
      view.addCostInfo(0, null);
      if (view.inputEl) view.inputEl.focus();
    } finally {
      view.isStreaming = false;
      // Per Gryphon issue #3: input stays enabled throughout. Drain any
      // prompts the user queued while this KB command was running so
      // they fire against the now-idle session.
      if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
    }
  }

  /** `kb add` with no args: retry failed + scan for orphan raw files. */
  async _kbAddNoArgs(view) {
    const vaultPath = this.app.vault.adapter.basePath;
    const output = [];
    let created = 0;

    // Step 1: Retry "Needs Attention" URLs from url-resolved.tsv
    view.updateStatus("Checking for unprocessed URLs...");
    const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
    try {
      const tsv = fs.readFileSync(tsvPath, "utf8");
      const needsRetry = [];
      for (const line of tsv.split("\n")) {
        const parts = line.split("\t");
        if (parts.length >= 3 && (parts[0] === "uncapturable" || parts[0] === "thin")) {
          needsRetry.push({ url: parts[2].trim(), title: parts[1] });
        }
      }
      if (needsRetry.length > 0) {
        view.updateStatus(`Retrying ${needsRetry.length} previously failed URLs...`);
        for (const entry of needsRetry) {
          view.updateStatus(`Processing ${entry.title || entry.url.substring(0, 40)}...`);
          const result = await this.ingestContent({ url: entry.url, source: "retry", view });
          if (result.status === "created" || result.status === "updated") {
            created++;
            output.push(`**Captured:** [[${result.pageName}]]`);
          }
        }
      }
    } catch (e) { console.log("[athena] retry scan failed:", e.message); }

    // Step 2: Orphan raw-file scan via pipeline
    view.updateStatus("Checking for orphan raw files...");
    try {
      const referencedRaw = new Set();
      let wikiContentIndex = "";
      const wikiDirs = ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers",
                        "wiki/format/videos", "wiki/format/images", "wiki/topics", "wiki/insights"];
      for (const wd of wikiDirs) {
        const fullWd = path.join(vaultPath, wd);
        if (!fs.existsSync(fullWd)) continue;
        for (const wf of fs.readdirSync(fullWd)) {
          if (!wf.endsWith(".md")) continue;
          try {
            const content = fs.readFileSync(path.join(fullWd, wf), "utf8");
            const rpMatch = content.substring(0, 500).match(/raw_path:\s*"?(\S+?)"?\s*$/m);
            if (rpMatch) referencedRaw.add(rpMatch[1]);
            wikiContentIndex += content.substring(0, 5000) + "\n";
          } catch {}
        }
      }
      for (const rd of ["raw/webpages/artifacts", "raw/repos/artifacts", "raw/papers/artifacts", "raw/videos/artifacts"]) {
        const fullRd = path.join(vaultPath, rd);
        if (!fs.existsSync(fullRd)) continue;
        for (const rf of fs.readdirSync(fullRd)) {
          if (!rf.endsWith(".md") || rf.startsWith("_")) continue;
          const relPath = rd + "/" + rf;
          const slug = rf.replace(".md", "");
          if (referencedRaw.has(relPath)) continue;
          if (wikiContentIndex.includes(slug)) continue;
          const rfPath = path.join(fullRd, rf);
          let rfContent = "";
          try { rfContent = fs.readFileSync(rfPath, "utf8"); } catch { continue; }
          const urlMatch = rfContent.match(/\*\*URL:\*\*\s*(https?:\/\/\S+)/) ||
                           rfContent.match(/^source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                           rfContent.match(/^url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
          const rfUrl = urlMatch ? urlMatch[1] : null;
          if (rfUrl && wikiContentIndex.includes(rfUrl)) continue;
          if (rfUrl) {
            const ytMatch = rfUrl.match(/(?:v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
            if (ytMatch && wikiContentIndex.includes(ytMatch[1])) continue;
          }
          view.updateStatus(`Processing orphan: ${rf.replace(".md", "").substring(0, 40)}...`);
          const result = await this.ingestContent({
            url: rfUrl, content: rfContent, rawPath: relPath, source: "orphan", view,
          });
          if (result.status === "created") {
            created++;
            output.push(`**Page created:** [[${result.pageName}]]`);
          }
        }
      }
    } catch (e) { console.log("[athena] orphan scan error:", e.message); }

    const finalOutput = output.filter(Boolean).join("\n");
    view.finalizeStreamingMessage(
      finalOutput || "Nothing to process.",
      created > 0 ? `${created} page${created > 1 ? "s" : ""} processed` : "Nothing to process"
    );
  }

  // ── Mechanical shell command runner (bin/kb) ──────────────────────

  // Issue #133 (continued): runs a `bin/kb` mechanical command with an
  // *idle* timeout instead of a total-wallclock one. The watchdog resets
  // every time the child writes to stdout or stderr — so a long-but-alive
  // kb operation (e.g. `kb add` doing a multi-step LLM ingest) keeps
  // running as long as it's producing progress lines, but a wedged child
  // (no output for `idleTimeoutMs`) is killed.
  //
  // Budget is resolved from settings.connectionTimeoutMs via Gryphon's
  // resolveConnectionTimeoutMs, so the same single knob ("Connection
  // timeout" in Settings → Athena) governs both Gryphon chat and Athena
  // mechanical commands. Pass an explicit `idleTimeoutMs` only when a
  // call site needs to override the resolved default (rare).
  runMechanical(command, args, idleTimeoutMs = null, view = null) {
    const vaultPath = this.app.vault.adapter.basePath;
    const kbPath = path.join(vaultPath, "bin", "kb");
    const effectiveMs = (typeof idleTimeoutMs === "number" && idleTimeoutMs > 0)
      ? idleTimeoutMs
      : resolveConnectionTimeoutMs({
          override: this.settings.connectionTimeoutMs,
          model: this.settings.model,
        });
    return new Promise((resolve) => {
      // Invoke kb through the Python interpreter rather than exec-ing the
      // script path directly: bin/kb is a Python program, and on Windows there
      // is no shebang mechanism, so `spawn(kbPath, ...)` ENOENTs. pythonCmd()
      // resolves `python` on Windows / `python3` on POSIX; the shebang is
      // simply ignored when the interpreter is explicit. Behaviour-neutral on
      // POSIX (`python3 bin/kb <cmd>` == shebang-exec of bin/kb).
      const proc = spawn(pythonCmd(), [kbPath, command, ...args], {
        cwd: vaultPath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
      });
      if (view) view._mechanicalProc = proc;
      let stdout = "", stderr = "";
      let resolved = false;
      let timer = null;

      const armTimer = () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => {
          if (resolved) return;
          resolved = true;
          try { proc.kill("SIGTERM"); } catch {}
          if (view) view._mechanicalProc = null;
          resolve({
            ok: false,
            stdout: stdout.trim(),
            stderr: "No output from `kb " + command + "` for "
              + Math.round(effectiveMs / 1000) + "s (idle timeout). "
              + "Raise Settings → Athena → Connection timeout if your runs need longer.",
          });
        }, effectiveMs);
      };
      armTimer();

      proc.stdout.on("data", (d) => { stdout += d.toString(); armTimer(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); armTimer(); });
      proc.on("close", (code) => {
        if (!resolved) {
          resolved = true;
          if (timer) clearTimeout(timer);
          if (view) view._mechanicalProc = null;
          resolve({ ok: code === 0, stdout: stdout.trim(), stderr: stderr.trim() });
        }
      });
      proc.on("error", (err) => {
        if (!resolved) {
          resolved = true;
          if (timer) clearTimeout(timer);
          if (view) view._mechanicalProc = null;
          resolve({ ok: false, stdout: "", stderr: err.message });
        }
      });
    });
  }

  // ── Content Pre-processor (strip Web Clipper YAML) ────────────────

  _preprocessContent(rawContent) {
    if (!rawContent) return { body: "", url: null, title: null, description: null };

    let body = rawContent;
    let url = null, title = null, description = null;

    const yamlMatch = rawContent.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
    if (yamlMatch) {
      const yaml = yamlMatch[1];
      body = yamlMatch[2].trim();

      const urlMatch = yaml.match(/^source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                       yaml.match(/^url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
      if (urlMatch) url = urlMatch[1];

      const titleMatch = yaml.match(/^title:\s*['"]?(.+?)['"]?\s*$/m);
      if (titleMatch) title = titleMatch[1].trim();

      const descMatch = yaml.match(/^description:\s*['"]?(.+?)['"]?\s*$/m);
      if (descMatch) description = descMatch[1].trim().replace(/^"(.*)"$/, "$1");
    }

    if (!url) {
      const mdUrl = body.match(/\*\*URL:\*\*\s*(https?:\/\/\S+)/);
      if (mdUrl) url = mdUrl[1];
    }
    if (!title) {
      const headingMatch = body.match(/^#\s+(.+)$/m);
      if (headingMatch) title = headingMatch[1].trim();
    }
    return { body, url, title, description };
  }

  // ── Unified Ingest Pipeline ──────────────────────────────────────
  //
  // All entry points (kb add, Web Clipper, url-new.txt, paste, orphan) call
  // this. 7 steps: preprocess → normalize → dup check → capture → LLM
  // processing → wiki page creation → tracking → post-processing.
  //
  // opts:
  //   - url:     source URL (optional if content provided)
  //   - content: pre-captured content (Web Clipper, paste)
  //   - title:   hint title
  //   - rawPath: path to existing raw file (Web Clipper case)
  //   - source:  "kb-add" | "web-clipper" | "url-new" | "paste" | "retry" | "orphan"
  //   - view:    optional view for status updates
  async ingestContent(opts) {
    const vaultPath = this.app.vault.adapter.basePath;
    const source = opts.source || "unknown";
    const view = opts.view || this._getActiveView();
    const updateStatus = (msg) => { if (view) view.updateStatus(msg); };

    // ── Step 0: PRE-PROCESS ──
    let contentBody = opts.content || "";
    let contentUrl = opts.url || "";
    let contentTitle = opts.title || "";
    if (contentBody) {
      const parsed = this._preprocessContent(contentBody);
      contentBody = parsed.body;
      if (!contentUrl && parsed.url) contentUrl = parsed.url;
      if (!contentTitle && parsed.title) contentTitle = parsed.title;
    }

    // ── Step 1: NORMALIZE ──
    let cleanUrl = contentUrl
      ? contentUrl.replace(/[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*/g, "")
           .replace(/[?&]$/, "").replace(/\/+$/, "")
      : "";
    const isRepo = /github\.com\/[^/]+\/[^/]+/i.test(cleanUrl);
    const isTweet = /x\.com|twitter\.com/i.test(cleanUrl);
    const isPaper = /arxiv\.org|aclanthology\.org/i.test(cleanUrl);
    const isVideo = /youtube\.com|youtu\.be/i.test(cleanUrl);
    const rawSubdir = isRepo ? "raw/repos/artifacts" : isPaper ? "raw/papers/artifacts" : isVideo ? "raw/videos/artifacts" : "raw/webpages/artifacts";

    if (cleanUrl && (/\.(jpg|jpeg|png|gif|webp|svg|bmp)(\?|$)/i.test(cleanUrl) || /pbs\.twimg\.com\/media/i.test(cleanUrl))) {
      return { status: "failed", pageName: null, summary: "Image URL \u2014 use `kb add` with the page URL instead." };
    }

    // ── Step 2: DUPLICATE CHECK ──
    updateStatus("Checking for duplicates...");
    const dupResult = this.findDuplicate(cleanUrl || null, contentTitle || null, contentBody ? contentBody.split("\n") : null);
    if (dupResult) {
      if (contentBody) {
        let existingRawSize = 0;
        for (const d of ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos"]) {
          const dp = path.join(vaultPath, d, dupResult.page + ".md");
          if (fs.existsSync(dp)) {
            const head = fs.readFileSync(dp, "utf8").substring(0, 500);
            const rpMatch = head.match(/raw_path:\s*"?(\S+?)"?\s*$/m);
            if (rpMatch && fs.existsSync(path.join(vaultPath, rpMatch[1]))) {
              existingRawSize = fs.statSync(path.join(vaultPath, rpMatch[1])).size;
            }
            break;
          }
        }
        if (contentBody.length > existingRawSize * 1.1 && existingRawSize > 0) {
          console.log("[athena] ingest: content update", contentBody.length, "vs", existingRawSize);
          const existingRawPath = this._findRawPathForPage(dupResult.page);
          if (existingRawPath) {
            fs.writeFileSync(path.join(vaultPath, existingRawPath), contentBody);
          }
          const topicNames = this._getTopicNames();
          updateStatus("Re-summarizing with updated content...");
          const llmResult = await this.llmProcessContent(contentBody, cleanUrl, topicNames);
          const updateInput = {
            vault: vaultPath, raw_path: existingRawPath || rawSubdir + "/unknown.md",
            url: cleanUrl || null, source: source,
          };
          if (llmResult) updateInput.llm_result = llmResult;
          await this._runWikiPageBuilder(updateInput);
          updateStatus("Updating cross-references...");
          await this.runMechanical("lint", [], null, view);
          return { status: "updated", pageName: dupResult.page, summary: llmResult ? llmResult.summary : null };
        }
      }
      return { status: "duplicate", pageName: null, dupPage: dupResult.page, dupMethod: dupResult.method };
    }

    // ── Step 3: CAPTURE ──
    let rawContent = contentBody || "";
    let rawSlug = "";
    let rawFilePath = opts.rawPath || "";

    if (cleanUrl && !rawContent) {
      // Slug from canonical Python (single source of truth) — replaces the
      // previous local approximation that produced 'www-linkedin-com-...'
      // while Python writes 'linkedin-com-...'. Without this, the read-back
      // at line ~942 looks for a file kb-capture never wrote and silently
      // falls through to empty rawContent, producing sparse wiki pages.
      // Falls back to the local approximation if the subprocess fails
      // (Python missing, slug derivation rejects, etc.) so capture flow
      // still works in degraded environments.
      const _categoryFromSubdir = (sd) =>
        sd.startsWith("raw/repos") ? "repos" :
        sd.startsWith("raw/papers") ? "papers" :
        sd.startsWith("raw/videos") ? "videos" :
        "webpages";
      try {
        rawSlug = execFileSync(pythonCmd(), [
          "-c",
          "import sys; sys.path.insert(0, sys.argv[1]); " +
          "from slug import derive_slug; " +
          "print(derive_slug(sys.argv[2], sys.argv[3] or None, sys.argv[4] or None))",
          // Plugin-bundled bin/lib/ as of 1.0.9; falls back to
          // vault-side bin/lib/ for legacy / --full-vault dev layouts.
          path.dirname(resolvePythonScript(this, "bin/lib/slug.py")),
          _categoryFromSubdir(rawSubdir),
          cleanUrl,
          contentTitle || "",
        ], { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "pipe"] }).trim();
      } catch (e) {
        console.warn("[athena] canonical slug derivation failed, falling back:", e.message);
        rawSlug = cleanUrl.replace(/https?:\/\//, "").replace(/[^a-z0-9]/gi, "-").replace(/-{2,}/g, "-").substring(0, 60);
      }
      rawFilePath = path.join(vaultPath, rawSubdir, rawSlug + ".md");

      try {
        if (fs.existsSync(rawFilePath) && fs.statSync(rawFilePath).size < 600) {
          fs.unlinkSync(rawFilePath);
        }
      } catch {}

      // GitHub repos: fetch the real README (gh-free) FIRST. The bundled
      // Python helper hits the public GitHub REST API over plain HTTP and
      // rewrites relative image paths to absolute raw.githubusercontent URLs
      // — github.com-matching markdown with every ![]() thumbnail + correctly
      // sized image, and NO `gh` dependency (so it works on end-user
      // machines). If it fails (private repo / offline / rate-limited),
      // repoReadmeRaw stays null and we fall through to the kb-capture (gh)
      // path in the `else if (!repoReadmeRaw)` branch below.
      let repoReadmeRaw = null;
      if (isRepo) {
        const _rm = cleanUrl.match(/github\.com\/([^/]+)\/([^/?#]+)/i);
        if (_rm) {
          updateStatus("Fetching README...");
          repoReadmeRaw = await this._captureGithubReadme(_rm[1], _rm[2], cleanUrl);
        }
      }
      if (repoReadmeRaw) {
        try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
        fs.writeFileSync(rawFilePath, repoReadmeRaw);
        rawContent = repoReadmeRaw;
      }

      // X/Twitter status: fetch via the public syndication CDN FIRST (gh-free,
      // no Playwright). The generic DOM walker mangles tweets — for a long-form
      // X Article the visible body is just a t.co shortlink (lang="zxx"), so
      // the walker titles the page with the shortlink and grabs a truncated
      // preview (witnessed: FakeMaidenMaker/status/2064900447375085823,
      // 2026-06-12). The bundled helper reads cdn.syndication.twimg.com, which
      // returns the real author, full note_tweet text, media, and the Article
      // title/preview/cover over plain HTTP with no auth. On failure (deleted /
      // protected / offline) tweetRaw stays null and we fall through to the
      // browser-capture path below — degraded-but-present floor preserved.
      let tweetRaw = null;
      if (isTweet) {
        updateStatus("Fetching tweet...");
        tweetRaw = await this._captureTweet(cleanUrl);
        if (tweetRaw) {
          try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
          fs.writeFileSync(rawFilePath, tweetRaw);
          rawContent = tweetRaw;
        }
      }

      if (!repoReadmeRaw && !tweetRaw) updateStatus("Capturing URL...");
      // 1.0.16: browserCapture returns { title, text, images } instead
      // of just text. We use title for the real frontmatter title
      // (was hardcoded "Page" / "X Post" / "Git \u2014 repo" fallback) and
      // append images as HTML <img> markdown so image-heavy pages
      // (Cisco blog etc.) actually carry their images into the raw.
      // GitHub repos MUST NOT go through the generic DOM walker WHEN the
      // dedicated helper succeeds — it force-normalizes every <img> to
      // width="600"/alt="Image", keeps invisible spacer/icon images, mangles
      // link-wrapped [<img>](href) structures, and drops the README's markdown
      // ![]() thumbnails (witnessed: roboflow/notebooks, 2026-06-08).
      // Gate on the RESULT (repoReadmeRaw / tweetRaw), not the intent: when the
      // helper SUCCEEDS we skip the DOM walker; when it FAILS (private /
      // rate-limited / offline repo, or a non-repo github URL) repoReadmeRaw is
      // null → fall back to browserCapture for a degraded-but-present floor
      // rather than hard-failing on plugin-only installs that have no
      // bin/kb-capture. (kb-capture's repo path hits the same api.github.com /
      // raw.githubusercontent.com endpoints, so it would fail identically — the
      // browser floor loses nothing.) The "captured via DOM walker" lint check
      // then surfaces the degraded raw for re-capture once the cause clears.
      const browserResult = (repoReadmeRaw || tweetRaw)
        ? null
        : await this.browserCapture(cleanUrl, updateStatus);
      if (browserResult) {
        const browserText = browserResult.text || "";
        const browserTitle = (browserResult.title || "").trim();
        const browserImages = browserResult.images || [];
        console.log("[athena] ingest: BrowserWindow captured",
          browserText.length, "chars,",
          browserImages.length, "image(s)");
        const repoMatch = cleanUrl.match(/github\.com\/([^/]+)\/([^/?#]+)/i);
        // Title precedence: the page's <title> element, then per-host
        // fallback as a last-ditch label. Strip trailing site-name
        // chrome that page <title>s commonly append (" | LinkedIn",
        // " - Twitter / X", " \u2014 Cisco Blogs").
        let rawTitle = browserTitle || (
          isRepo && repoMatch ? `Git \u2014 ${repoMatch[2]}`
          : isTweet ? "X Post"
          : "Page"
        );
        if (browserTitle) {
          // 1.1: X.com title cleanup. Page <title> for both Articles and
          // regular tweets is `<author> on X: "<actual title>" / X`. Strip
          // the wrapper to keep just the article title \u2014 that's also what
          // the user sees as the post heading on the rendered page, and
          // it's what should drive the filename + frontmatter title.
          // Pattern handles straight + curly quotes; tolerates whitespace
          // around the slash separator.
          if (isTweet) {
            const xMatch = rawTitle.match(/^[^:]+\s+on\s+X:\s*[\u201c"\u2018'](.+)[\u201d"\u2019']\s*\/\s*X\s*$/);
            if (xMatch && xMatch[1].trim()) {
              rawTitle = xMatch[1].trim();
            }
          }
          // Generic site-name suffix strip: " | LinkedIn", " \u2014 Cisco Blogs",
          // " - Substack", etc. Conservative \u2014 only strips if there's a
          // separator AND the suffix is \u226440 chars (longer = probably content).
          rawTitle = rawTitle.replace(/\s*[|\u2014\u2013-]\s*[^|\u2014\u2013-]{1,40}$/, '').trim() || rawTitle;
        }

        // 1.1: title-based slug override for X.com Article-style posts.
        // The general slug policy (bin/lib/slug.py) is URL_DERIVED for
        // webpages \u2014 intentional, to prevent collision-bait slugs like
        // `post-linkedin` or `untitled` on generic-titled pages. But
        // X.com Articles routinely have distinctive titles (4+ words,
        // 25+ chars) that make MUCH better filenames than the URL stub.
        // Override only when the title passes a "distinctive enough"
        // bar \u2014 generic-titled posts still fall back to the URL slug.
        const _titleIsDistinctive =
          isTweet
          && rawTitle
          && rawTitle.split(/\s+/).length >= 4
          && rawTitle.length >= 25
          && !/^(page|x post|untitled|tweet|home|status)$/i.test(rawTitle);
        if (_titleIsDistinctive) {
          const titleSlug = rawTitle
            .toLowerCase()
            .replace(/[\u201c\u201d\u2018\u2019"']/g, '')           // strip curly + straight quotes
            .replace(/[^a-z0-9]+/g, '-')        // non-alnum \u2192 hyphen
            .replace(/^-+|-+$/g, '')            // trim leading/trailing
            .substring(0, 80)                   // length cap
            .replace(/-+$/, '');                // re-trim after truncation
          if (titleSlug && titleSlug.length >= 8 && titleSlug !== rawSlug) {
            const newPath = path.join(vaultPath, rawSubdir, titleSlug + ".md");
            // Collision guard: only adopt the title-based path if no
            // different file already lives there.
            if (!fs.existsSync(newPath) || newPath === rawFilePath) {
              console.log("[athena] using title-based slug for X.com post:",
                titleSlug, "(was:", rawSlug + ")");
              rawSlug = titleSlug;
              rawFilePath = newPath;
            }
          }
        }
        // 1.1: the walker now emits images inline at their natural DOM
        // position (see _BROWSER_EXTRACT_JS, case 'img'). If any image
        // markdown is already in browserText, suppress the trailing
        // block to avoid duplication. Body-position images are vastly
        // better UX (image at top of post stays at top, not orphaned at
        // bottom). The fallback below is for the rare case where the
        // walker didn't visit any imgs but the separate scan did find
        // some (e.g. images inside a sibling element).
        const _htmlAttr = (s) => String(s || "").replace(/"/g, "&quot;");
        const walkerEmittedImages = /<img\s+src=/i.test(browserText);
        const imagesMd = (!walkerEmittedImages && browserImages.length > 0)
          ? "\n\n" + browserImages.map(img =>
              `<img src="${_htmlAttr(img.src)}" alt="${_htmlAttr(img.alt || "Image")}" width="600">`
            ).join("\n\n")
          : "";
        // YAML frontmatter is REQUIRED so create_wiki_page → preprocess_content
        // can extract the source URL when later turning this raw into a wiki
        // page. The previous "URL intentionally omitted" version produced wiki
        // pages with no `url:` field and therefore no Source link in the body
        // (the user-reported missing-source bug fixed in 0.9.10).
        const _rawTitleEsc = rawTitle.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        const _urlEsc = cleanUrl.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        rawContent = `---\ntitle: "${_rawTitleEsc}"\nsource: "${_urlEsc}"\ncaptured_at: "${new Date().toISOString()}"\nclipped_via: "browser-capture"\n---\n\n# ${rawTitle}\n\n${browserText}${imagesMd}\n`;
        // Ensure raw/<type>/artifacts/ exists — Community-Plugins / fresh
        // vault installs lack the three-layer dir structure (the Python
        // backend creates it). Without this, writeFileSync throws ENOENT
        // even though browser capture succeeded — opaque user-facing error.
        try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
        fs.writeFileSync(rawFilePath, rawContent);
      } else if (!repoReadmeRaw && !tweetRaw) {
        updateStatus("Webview capture failed, trying Python backend...");
        const captureResult = await this.runMechanical("add", [cleanUrl], null, view);
        const captureOutput = (captureResult.stdout || "") + (captureResult.stderr || "");
        if (captureOutput.includes("Saved:") || captureOutput.includes("already exists")) {
          try { rawContent = fs.readFileSync(rawFilePath, "utf8"); } catch {
            for (const d of ["raw/webpages/artifacts", "raw/repos/artifacts", "raw/papers/artifacts", "raw/videos/artifacts"]) {
              try { rawContent = fs.readFileSync(path.join(vaultPath, d, rawSlug + ".md"), "utf8"); rawFilePath = path.join(vaultPath, d, rawSlug + ".md"); break; } catch {}
            }
          }
        } else if ((captureResult.stdout || "").includes("THIN_CONTENT")) {
          return { status: "failed", pageName: null, summary: "Could not capture full content (requires authentication)." };
        } else {
          // All three capture paths exhausted: BrowserWindow + webview
          // returned <100 chars or threw, and the bin/kb shell fallback
          // either reported an error or isn't present (Community-Plugin
          // installs don't ship the Python backend). Common root causes:
          // page blocks automation (Cloudflare et al.), Linux sandbox
          // restrictions on the BrowserWindow path, or missing Python
          // backend in the vault. Web Clipper sidesteps all three.
          return {
            status: "failed",
            pageName: null,
            summary:
              "Browser capture failed (this can happen on Linux with sandbox restrictions, " +
              "or with pages that block automation like Cloudflare-protected sites). " +
              "Try the Obsidian Web Clipper extension instead.",
          };
        }
      }
    } else if (rawFilePath && !rawContent) {
      let fileContent = "";
      try { fileContent = fs.readFileSync(path.join(vaultPath, rawFilePath), "utf8"); } catch {
        try { fileContent = fs.readFileSync(rawFilePath, "utf8"); } catch {}
      }
      if (fileContent) {
        const parsed = this._preprocessContent(fileContent);
        rawContent = parsed.body;
        if (!cleanUrl && parsed.url) {
          cleanUrl = parsed.url.replace(/[?&](utm_\w+|s|t|rcm|ref|usp|si|igsh|fbclid)=[^&]*/g, "")
            .replace(/[?&]$/, "").replace(/\/+$/, "");
        }
      }
      rawSlug = path.basename(rawFilePath, ".md");
    } else if (rawContent && !rawFilePath) {
      rawSlug = (contentTitle || "paste-" + Date.now()).toLowerCase().replace(/[^a-z0-9]/gi, "-").replace(/-{2,}/g, "-").substring(0, 60);
      rawFilePath = path.join(vaultPath, rawSubdir, rawSlug + ".md");
      // Wrap with YAML frontmatter so create_wiki_page can extract source URL
      // (same rationale as the browserCapture branch above). Skip wrapping if
      // the user-pasted content already has its own `---` frontmatter — avoid
      // double-wrapping (was the lint #48 bug class on the writer side).
      if (!rawContent.startsWith("---")) {
        const _titleEsc = (contentTitle || rawSlug).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
        const _urlLine = cleanUrl ? `source: "${cleanUrl.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"\n` : "";
        rawContent = `---\ntitle: "${_titleEsc}"\n${_urlLine}captured_at: "${new Date().toISOString()}"\nclipped_via: "paste"\n---\n\n${rawContent}\n`;
      }
      // Ensure raw/<type>/ exists for fresh-vault installs (see line 1001 fix).
      try { fs.mkdirSync(path.dirname(rawFilePath), { recursive: true }); } catch {}
      fs.writeFileSync(rawFilePath, rawContent);
    }

    if (!rawContent) {
      return { status: "failed", pageName: null, summary: "No content captured." };
    }

    // Post-capture dup check (title + content fingerprint)
    const titleMatch = rawContent.match(/^#\s+(.+)/m) || rawContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m);
    const rawTitle = titleMatch ? titleMatch[1].trim() : null;
    const postCaptureDup = this.findDuplicate(null, rawTitle, rawContent.split("\n"));
    if (postCaptureDup) {
      console.log("[athena] ingest: post-capture duplicate", postCaptureDup);
      return { status: "duplicate", pageName: null, dupPage: postCaptureDup.page, dupMethod: postCaptureDup.method };
    }

    // ── Step 4: LLM PROCESSING ──
    const topicNames = this._getTopicNames();
    updateStatus("Reading and summarizing...");
    const llmResult = await this.llmProcessContent(rawContent, cleanUrl || rawSlug, topicNames);

    // ── Step 5: WIKI PAGE CREATION ──
    updateStatus("Creating wiki page...");
    const relRawPath = rawFilePath.startsWith(vaultPath)
      ? rawFilePath.substring(vaultPath.length + 1)
      : (rawSubdir + "/" + (rawSlug || path.basename(rawFilePath, ".md")) + ".md");
    const wikiInput = {
      vault: vaultPath,
      raw_path: relRawPath,
      url: cleanUrl || null,
      title: contentTitle || null,
      source: source,
    };
    if (llmResult) {
      console.log("[athena] ingest: LLM result", { title: llmResult.title, tags: llmResult.tags?.length, related: llmResult.related?.length });
      wikiInput.llm_result = llmResult;
    } else {
      console.log("[athena] ingest: no LLM, Python fallback will apply naming conventions");
    }
    const wikiResult = await this._runWikiPageBuilder(wikiInput);
    const pageName = wikiResult ? wikiResult.page_name : null;
    if (wikiResult) {
      console.log("[athena] ingest: wiki result", { status: wikiResult.status, page: wikiResult.page_name });
    }

    // ── Step 5.5: INLINE SYNTHESIS (1.1) ──
    // Replace the "Pending synthesis" placeholder with a real digest, in
    // the same chat turn the user kicked off. Pre-1.1 this happened
    // asynchronously via a macOS-only launchd job, leaving Linux + Windows
    // wiki pages permanently stubbed. Inline means the user waits a few
    // seconds longer for kb add to return, but they see a finished page
    // instead of a stub-that-might-or-might-not-fill-in-later.
    //
    // Failures here are SOFT — the wiki page already exists with the
    // placeholder, so a synthesis failure just means the user has to
    // run `kb regen <url>` later. We surface the failure reason but
    // don't fail the ingest.
    let synthesisResult = null;
    if (wikiResult && wikiResult.wiki_path && pageName) {
      // wiki_path from wiki_page.py is vault-relative; resolve to absolute.
      const wikiAbsPath = path.isAbsolute(wikiResult.wiki_path)
        ? wikiResult.wiki_path
        : path.join(vaultPath, wikiResult.wiki_path);
      try {
        synthesisResult = await synthesizeWikiPage(this, wikiAbsPath, {
          onStatus: updateStatus,
        });
        if (synthesisResult.ok) {
          console.log("[athena] synthesis ok", {
            attempts: synthesisResult.attempts,
            cost: synthesisResult.cost,
            summaryLen: (synthesisResult.summary || "").length,
          });
        } else {
          console.warn("[athena] synthesis failed",
            { reason: synthesisResult.reason, detail: synthesisResult.detail });
        }
      } catch (e) {
        console.warn("[athena] synthesis threw:", e && e.message);
        synthesisResult = { ok: false, reason: "exception", detail: (e && e.message) || String(e) };
      }
    }

    // ── Step 6: TRACKING (fallback if Python missed it) ──
    if (cleanUrl && !wikiResult) {
      const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
      try {
        let tsv = fs.readFileSync(tsvPath, "utf8");
        const urlLower = cleanUrl.toLowerCase();
        tsv = tsv.split("\n").filter(l => !l.toLowerCase().includes(urlLower)).join("\n");
        const ts = new Date().toISOString();
        tsv += `\ncaptured\t${pageName || rawSlug}\t${cleanUrl}\t${ts}\n`;
        tsv = tsv.replace(/\n{3,}/g, "\n");
        fs.writeFileSync(tsvPath, tsv);
      } catch (e) { console.log("[athena] ingest: url-resolved update failed:", e.message); }
    }

    // ── Step 7: POST-PROCESSING ──
    updateStatus("Updating cross-references...");
    await this.runMechanical("lint", [], null, view);

    // Synthesis summary overrides the LLM-naming summary in the chat
    // bubble — it's the higher-quality, body-validated version.
    const finalSummary = synthesisResult && synthesisResult.ok
      ? synthesisResult.summary
      : (llmResult ? llmResult.summary : null);
    return {
      status: "created",
      pageName,
      summary: finalSummary,
      synthesis: synthesisResult,
    };
  }

  /** Call the shared Python wiki page builder (bin/lib/wiki_page.py). */
  _runWikiPageBuilder(input) {
    const vaultPath = this.app.vault.adapter.basePath;
    // 1.0.9+: prefer the plugin-bundled wiki_page.py over the
    // vault-side copy. End users no longer need a cloned Athena vault
    // — just a Python install + `pip install pydantic`.
    const scriptPath = resolvePythonScript(this, "bin/lib/wiki_page.py");
    return new Promise((resolve) => {
      const proc = spawn(pythonCmd(), [scriptPath, "--stdin"], {
        cwd: vaultPath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
      });
      let stdout = "", stderr = "";
      proc.stdout.on("data", (d) => { stdout += d.toString(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });
      proc.stdin.write(JSON.stringify(input));
      proc.stdin.end();
      const timer = setTimeout(() => {
        try { proc.kill("SIGTERM"); } catch {}
        console.log("[athena] wiki page builder timed out");
        resolve(null);
      }, 15000);
      proc.on("close", (code) => {
        clearTimeout(timer);
        // Bumped 200 → 2000 chars in 1.0.10 — Python tracebacks regularly
        // exceed 200 chars and the 200-char cap routinely truncated before
        // the actual "<ErrorType>: message" tail, leaving the user with a
        // file/line pointer and no error class. 2000 covers any reasonable
        // single-frame traceback; multi-frame traces may still need the
        // manual reproduction command in CHANGELOG to see fully.
        if (stderr) console.log("[athena] wiki_page.py stderr:", stderr.substring(0, 2000));
        try {
          const result = JSON.parse(stdout.trim());
          resolve(result);
        } catch (e) {
          console.log("[athena] wiki_page.py parse error:", e.message, "raw:", stdout.substring(0, 200));
          resolve(null);
        }
      });
      proc.on("error", (err) => {
        clearTimeout(timer);
        console.log("[athena] wiki_page.py spawn error:", err.message);
        resolve(null);
      });
    });
  }

  /** Regenerate the wiki page for a previously-captured URL using the
   *  current code's wiki_page.py. Looks up the page by URL via the
   *  inbox/url-resolved.tsv index, then calls the Python builder with
   *  overwrite=true so dedup doesn't short-circuit. Returns
   *  { status: 'refreshed' | 'not-found' | 'failed', pageName?, error? }.
   *  The raw file on disk is NOT re-captured — only the wiki layer is
   *  regenerated from the existing raw. Use kb remove + kb add if you
   *  want a fresh browser fetch.
   */
  /**
   * Look up the absolute on-disk path of a wiki page by URL. Returns
   * null if no matching captured row exists in url-resolved.tsv or the
   * named page can't be located in any wiki/format/* subdir.
   *
   * Shared by kb refresh + kb regen. Page-name lookup is via the TSV
   * (the canonical URL→page-name index) and file-location is a fixed
   * scan over the known content-type subdirs.
   */
  _lookupWikiPathByUrl(rawUrl) {
    const vaultPath = this.app.vault.adapter.basePath;
    const url = (rawUrl || "").trim().replace(/\/+$/, "");
    if (!url) return null;
    const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
    if (!fs.existsSync(tsvPath)) return null;
    const urlLower = url.toLowerCase();
    let pageName = null;
    try {
      const tsv = fs.readFileSync(tsvPath, "utf8");
      for (const line of tsv.split("\n")) {
        const parts = line.split("\t");
        if (parts.length < 3) continue;
        const status = parts[0].trim();
        if (status !== "captured" && status !== "has-content") continue;
        const rowUrl = (parts[2] || "").trim().toLowerCase().replace(/\/+$/, "");
        if (rowUrl !== urlLower) continue;
        pageName = (parts[1] || "").trim();
        break;
      }
    } catch (_) {
      return null;
    }
    if (!pageName) return null;
    for (const d of ["wiki/format/webpages", "wiki/format/repos",
                     "wiki/format/papers", "wiki/format/videos",
                     "wiki/format/images"]) {
      const fp = path.join(vaultPath, d, pageName + ".md");
      if (fs.existsSync(fp)) return fp;
    }
    return null;
  }

  async _refreshWikiByUrl(rawUrl, view) {
    const vaultPath = this.app.vault.adapter.basePath;
    const url = (rawUrl || "").trim().replace(/\/+$/, "");
    if (!url) return { status: "failed", error: "No URL provided." };

    // 1) Find the wiki page name from the TSV index. Format:
    //    status \t page_name \t url \t timestamp
    //    (older 5-col rows: status \t title \t source_url \t resolved \t type)
    //    parts[1] is page name, parts[2] is URL in both shapes.
    const tsvPath = path.join(vaultPath, "inbox", "url-resolved.tsv");
    if (!fs.existsSync(tsvPath)) {
      return { status: "not-found", error: "url-resolved.tsv not found" };
    }
    const urlLower = url.toLowerCase();
    let pageName = null;
    try {
      const tsv = fs.readFileSync(tsvPath, "utf8");
      for (const line of tsv.split("\n")) {
        const parts = line.split("\t");
        if (parts.length < 3) continue;
        const status = parts[0].trim();
        if (status !== "captured" && status !== "has-content") continue;
        const rowUrl = (parts[2] || "").trim().toLowerCase().replace(/\/+$/, "");
        if (rowUrl !== urlLower) continue;
        pageName = (parts[1] || "").trim();
        break;
      }
    } catch (e) {
      return { status: "failed", error: `Cannot read url-resolved.tsv: ${e.message}` };
    }
    if (!pageName) return { status: "not-found" };

    // 2) Find the on-disk wiki file and read its raw_path frontmatter.
    let wikiFile = null;
    for (const d of ["wiki/format/webpages", "wiki/format/repos",
                     "wiki/format/papers", "wiki/format/videos",
                     "wiki/format/images"]) {
      const fp = path.join(vaultPath, d, pageName + ".md");
      if (fs.existsSync(fp)) { wikiFile = fp; break; }
    }
    if (!wikiFile) return { status: "not-found" };
    let rawPath = null;
    try {
      const head = fs.readFileSync(wikiFile, "utf8").substring(0, 1000);
      const m = head.match(/raw_path:\s*"?([^"\n]+?)"?\s*$/m);
      if (m) rawPath = m[1].trim();
    } catch {}
    if (!rawPath) return { status: "failed", error: "wiki page has no raw_path field" };

    // 3) Call wiki_page.py with overwrite=true. The Python builder will
    //    snapshot the existing wiki page to .kb-trash/ before writing,
    //    so the refresh is reversible via kb undo.
    const result = await this._runWikiPageBuilder({
      vault: vaultPath,
      raw_path: rawPath,
      url: url,
      source: "refresh",
      overwrite: true,
    });
    if (!result) return { status: "failed", error: "wiki_page.py returned no result (check dev console for stderr)" };
    if (result.status === "failed" || result.status === "schema_error") {
      return { status: "failed", error: result.summary || result.error || "Unknown error" };
    }
    return { status: "refreshed", pageName: result.page_name || pageName };
  }

  // ─────────────────────────────────────────────────────────────────
  // 1.0.17: JS implementations for kb verbs that previously fell
  // through to the LLM. Soft-delete to .kb-trash/<ts>_<verb>/, atomic
  // restore via .kb-trash/ enumeration, TSV status updates. All
  // cross-platform — no bash/python dependency. The Python backend's
  // bin/kb still has its own implementations (used by terminal users);
  // these are the plugin-side equivalents for the chat surface.
  // ─────────────────────────────────────────────────────────────────

  /** Find a wiki page file by its name (basename without .md). Scans
   *  the standard format/* subdirs. Returns absolute path or null. */
  _findWikiFileByName(pageName) {
    const vault = this.app.vault.adapter.basePath;
    const subdirs = [
      "wiki/format/webpages", "wiki/format/repos", "wiki/format/papers",
      "wiki/format/videos", "wiki/format/images",
      "wiki/topics", "wiki/entities", "wiki/comparisons",
      "wiki/insights", "wiki/dashboards",
    ];
    for (const d of subdirs) {
      const fp = path.join(vault, d, pageName + ".md");
      if (fs.existsSync(fp)) return fp;
    }
    return null;
  }

  /** Parse first 1000 chars of a wiki page for raw_path + url. */
  _readWikiHead(wikiPath) {
    try {
      const head = fs.readFileSync(wikiPath, "utf8").substring(0, 1000);
      const rp = head.match(/^raw_path:\s*"?([^"\n]+?)"?\s*$/m);
      const u = head.match(/^url:\s*"?([^"\n]+?)"?\s*$/m);
      return { rawPath: rp ? rp[1].trim() : null, url: u ? u[1].trim() : null };
    } catch { return { rawPath: null, url: null }; }
  }

  /** Create a fresh .kb-trash bundle directory and return its path. */
  _kbTrashBundle(verb) {
    const vault = this.app.vault.adapter.basePath;
    const ts = new Date().toISOString().replace(/[-:.]/g, "").substring(0, 15);  // 20260517T143012
    const dir = path.join(vault, ".kb-trash", `${ts}_kb-${verb}`);
    fs.mkdirSync(dir, { recursive: true });
    return dir;
  }

  /** Rewrite a TSV row's status (e.g. captured → removed) for a given URL.
   *  Returns true if any row was changed. */
  _updateTsvStatus(url, newStatus) {
    if (!url) return false;
    const vault = this.app.vault.adapter.basePath;
    const tsvPath = path.join(vault, "inbox", "url-resolved.tsv");
    if (!fs.existsSync(tsvPath)) return false;
    const urlLower = url.toLowerCase().replace(/\/+$/, "");
    let changed = false;
    try {
      const tsv = fs.readFileSync(tsvPath, "utf8");
      const out = tsv.split("\n").map(line => {
        const parts = line.split("\t");
        if (parts.length < 3) return line;
        const rowUrl = (parts[2] || "").trim().toLowerCase().replace(/\/+$/, "");
        if (rowUrl !== urlLower) return line;
        if (parts[0] === newStatus) return line;
        changed = true;
        parts[0] = newStatus;
        return parts.join("\t");
      }).join("\n");
      if (changed) fs.writeFileSync(tsvPath, out);
    } catch (e) { console.log("[athena] _updateTsvStatus failed:", e.message); }
    return changed;
  }

  /** kb remove <page>. Soft-deletes the wiki page AND its raw source
   *  to .kb-trash/ together — there's no reason to keep an orphan raw
   *  whose wiki companion is gone, and leaving one produces auto-
   *  recreate cycles on the next lint pass. Updates TSV status. */
  async _kbRemove(args) {
    // Strip surrounding quotes — the `kb remove (.+)` regex captures the
    // user's quoting verbatim, so `kb remove "Foo Bar"` lands here with
    // args[0] = '"Foo Bar"' (literal quote chars). Without stripping, the
    // filename lookup looks for a file containing the quote chars, which
    // never matches, and the error message wraps the already-quoted value
    // in another set of quotes producing ""Foo Bar"" in the chat bubble.
    const pageName = (args[0] || "").trim().replace(/^["']|["']$/g, "");
    if (!pageName) return { message: "Usage: `kb remove <page-name>`" };
    const wikiPath = this._findWikiFileByName(pageName);
    if (!wikiPath) {
      return { message: `**Not found**: no wiki page named "${pageName}". Run \`kb list\` to see existing pages.` };
    }
    const { rawPath, url } = this._readWikiHead(wikiPath);
    const vault = this.app.vault.adapter.basePath;
    const bundle = this._kbTrashBundle("remove");
    const moved = [];
    try {
      const wikiRel = path.relative(vault, wikiPath);
      const wikiDest = path.join(bundle, wikiRel);
      fs.mkdirSync(path.dirname(wikiDest), { recursive: true });
      fs.renameSync(wikiPath, wikiDest);
      moved.push(wikiRel);
    } catch (e) { return { message: `**Remove failed**: ${e.message}` }; }
    if (rawPath) {
      const rawAbs = path.join(vault, rawPath);
      if (fs.existsSync(rawAbs)) {
        try {
          const rawDest = path.join(bundle, rawPath);
          fs.mkdirSync(path.dirname(rawDest), { recursive: true });
          fs.renameSync(rawAbs, rawDest);
          moved.push(rawPath);
        } catch (e) { console.log("[athena] raw move failed:", e.message); }
      }
    }
    if (url) this._updateTsvStatus(url, "removed");
    return {
      message: `**Removed**: [[${pageName}]] (and ${moved.length - 1 > 0 ? "its raw source" : "wiki only — no raw_path"})\n\nMoved to \`.kb-trash/${path.basename(bundle)}/\` — restore with \`kb undo\` within 30 days.`,
      summary: `Removed: ${pageName}`,
    };
  }

  /** kb undo: restore the latest .kb-trash bundle to its original
   *  locations. Walks the bundle dir tree, moves each file back to
   *  its corresponding vault-rooted path. */
  async _kbUndo() {
    const vault = this.app.vault.adapter.basePath;
    const trashRoot = path.join(vault, ".kb-trash");
    if (!fs.existsSync(trashRoot)) {
      return { message: "Trash is empty — nothing to restore." };
    }
    const bundles = fs.readdirSync(trashRoot)
      .filter(n => fs.statSync(path.join(trashRoot, n)).isDirectory())
      .sort();  // ISO timestamps sort chronologically
    if (bundles.length === 0) {
      return { message: "Trash is empty — nothing to restore." };
    }
    const latest = bundles[bundles.length - 1];
    const bundleDir = path.join(trashRoot, latest);
    const restored = [];
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const src = path.join(dir, entry.name);
        if (entry.isDirectory()) { walk(src); continue; }
        const rel = path.relative(bundleDir, src);
        const dst = path.join(vault, rel);
        try {
          fs.mkdirSync(path.dirname(dst), { recursive: true });
          fs.renameSync(src, dst);
          restored.push(rel);
        } catch (e) { console.log("[athena] undo restore failed:", rel, e.message); }
      }
    };
    walk(bundleDir);
    // Clean up the now-empty bundle dir (best-effort).
    try { fs.rmSync(bundleDir, { recursive: true, force: true }); } catch {}
    // Revert TSV status for any wiki page restored. (Best-effort —
    // multiple URLs may be involved in one bundle; we rewrite all
    // 'removed' rows whose page name matches a restored wiki file.)
    for (const rel of restored) {
      if (!rel.startsWith("wiki/")) continue;
      const wikiAbs = path.join(vault, rel);
      const { url } = this._readWikiHead(wikiAbs);
      if (url) this._updateTsvStatus(url, "captured");
    }
    return {
      message: `**Restored** ${restored.length} file(s) from \`.kb-trash/${latest}/\`:\n${restored.map(r => "- " + r).join("\n")}`,
      summary: `Restored ${restored.length} file(s)`,
    };
  }

  /** kb trash: list .kb-trash bundles with item counts and ages. */
  async _kbTrashList() {
    const vault = this.app.vault.adapter.basePath;
    const trashRoot = path.join(vault, ".kb-trash");
    if (!fs.existsSync(trashRoot)) {
      return { message: "Trash is empty." };
    }
    const now = Date.now();
    const bundles = fs.readdirSync(trashRoot)
      .filter(n => fs.statSync(path.join(trashRoot, n)).isDirectory())
      .sort().reverse();  // newest first
    if (bundles.length === 0) return { message: "Trash is empty." };
    const lines = ["**Trash bundles** (newest first):", ""];
    let totalFiles = 0;
    for (const name of bundles) {
      const dir = path.join(trashRoot, name);
      const stat = fs.statSync(dir);
      const ageDays = Math.floor((now - stat.mtimeMs) / (1000 * 60 * 60 * 24));
      let count = 0;
      const walk = (d) => {
        for (const e of fs.readdirSync(d, { withFileTypes: true })) {
          if (e.isDirectory()) walk(path.join(d, e.name));
          else count++;
        }
      };
      try { walk(dir); } catch {}
      totalFiles += count;
      const purgeFlag = ageDays >= 30 ? " ⚠️ purge-eligible" : "";
      lines.push(`- \`${name}\` — ${count} file(s), ${ageDays}d old${purgeFlag}`);
    }
    lines.push("", `**Total**: ${bundles.length} bundle(s), ${totalFiles} file(s). Run \`kb undo\` to restore the most recent.`);
    return { message: lines.join("\n") };
  }

  /** kb rename <page> --to "new name". Renames the wiki page file and
   *  updates wikilinks across the vault. Backslashes in new name are
   *  rejected (file system safety). */
  async _kbRename(args) {
    // args layout: [oldName, "--to", newName]
    if (args.length < 3 || args[1] !== "--to") {
      return { message: 'Usage: `kb rename <page> --to "New Name"`' };
    }
    // Strip surrounding quotes from BOTH args — see _kbRemove for the
    // same-shaped bug (regex captures user's quotes verbatim, lookup
    // then fails on filenames-with-quote-chars).
    const oldName = args[0].trim().replace(/^["']|["']$/g, "");
    const newName = args[2].trim().replace(/^["']|["']$/g, "");
    if (!oldName || !newName) {
      return { message: 'Usage: `kb rename <page> --to "New Name"`' };
    }
    if (/[\\\/]/.test(newName)) {
      return { message: `**Rename failed**: new name cannot contain "/" or "\\" — those are path separators.` };
    }
    const oldPath = this._findWikiFileByName(oldName);
    if (!oldPath) {
      return { message: `**Not found**: no wiki page named "${oldName}".` };
    }
    const dir = path.dirname(oldPath);
    // Apply the same Windows-safe sanitization Python does for the
    // file system layer (`:` → ` —` on Windows; passthrough on POSIX).
    const sanitized = process.platform === "win32"
      ? newName.replace(/:/g, " —").replace(/[<>"|?*\x00-\x1f]/g, "").replace(/[ .]+$/, "")
      : newName;
    const newPath = path.join(dir, sanitized + ".md");
    if (fs.existsSync(newPath) && newPath !== oldPath) {
      return { message: `**Rename failed**: a page already exists at "${sanitized}". Choose a different name or remove the existing page first.` };
    }
    try {
      fs.renameSync(oldPath, newPath);
    } catch (e) { return { message: `**Rename failed**: ${e.message}` }; }
    // Update wikilinks across the vault: [[oldName]] → [[sanitized]]
    // Walks wiki/**/*.md and inbox/**/*.md (skips raw/ — raw bodies
    // shouldn't reference wiki pages by wikilink). Simple string
    // replace inside [[...]] only; ignores incidental occurrences in
    // body text.
    const escaped = oldName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const linkRe = new RegExp("\\[\\[" + escaped + "(\\|[^\\]]+)?\\]\\]", "g");
    const vault = this.app.vault.adapter.basePath;
    let updatedFiles = 0;
    const scanDirs = ["wiki", "inbox"];
    const walk = (d) => {
      for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
        if (entry.name === ".kb-trash" || entry.name.startsWith(".")) continue;
        const fp = path.join(d, entry.name);
        if (entry.isDirectory()) { walk(fp); continue; }
        if (!fp.endsWith(".md")) continue;
        try {
          const text = fs.readFileSync(fp, "utf8");
          const newText = text.replace(linkRe, (m, alias) => `[[${sanitized}${alias || ""}]]`);
          if (newText !== text) {
            fs.writeFileSync(fp, newText);
            updatedFiles++;
          }
        } catch {}
      }
    };
    for (const sd of scanDirs) {
      const abs = path.join(vault, sd);
      if (fs.existsSync(abs)) try { walk(abs); } catch {}
    }
    return {
      message: `**Renamed**: [[${sanitized}]] (was "${oldName}"). Updated wikilinks in ${updatedFiles} file(s).`,
      summary: `Renamed: ${sanitized}`,
    };
  }

  _getTopicNames() {
    const topicNames = [];
    try {
      const topicDir = path.join(this.app.vault.adapter.basePath, "wiki", "topics");
      if (fs.existsSync(topicDir)) {
        for (const f of fs.readdirSync(topicDir)) {
          if (f.endsWith(".md") && !f.startsWith("_")) topicNames.push(f.replace(/\.md$/, ""));
        }
      }
    } catch {}
    return topicNames;
  }

  _findRawPathForPage(pageName) {
    const vaultPath = this.app.vault.adapter.basePath;
    for (const d of ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos", "wiki/format/images"]) {
      const fp = path.join(vaultPath, d, pageName + ".md");
      if (fs.existsSync(fp)) {
        const head = fs.readFileSync(fp, "utf8").substring(0, 500);
        const match = head.match(/raw_path:\s*"?(\S+?)"?\s*$/m);
        return match ? match[1] : null;
      }
    }
    return null;
  }

  // ── Duplicate detection (URL + title + content fingerprint) ──────

  findDuplicate(url, title, bodyLines) {
    const vaultPath = this.app.vault.adapter.basePath;
    const wikiDirs = ["wiki/format/webpages", "wiki/format/repos", "wiki/format/papers", "wiki/format/videos", "wiki/format/images"];

    const cleanUrl = url ? url.replace(/[?&](utm_\w+|s|t|rcm|ref|usp)=[^&]*/g, "").replace(/[?&]$/, "").replace(/\/+$/, "").toLowerCase() : "";
    const normTitle = title ? title.toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim() : "";
    const fingerprint = [];
    if (bodyLines && bodyLines.length > 0) {
      const fullText = bodyLines.join("\n");
      const paragraphs = fullText.split(/\n\s*\n/);
      for (const p of paragraphs) {
        const cleaned = p.replace(/\s+/g, " ").trim().toLowerCase();
        if (cleaned.length < 50) continue;
        if (/^\d|^http|^published|^created|^date|^updated|^tags|^source|^author|^views|^likes|^\*\*url/i.test(cleaned)) continue;
        fingerprint.push(cleaned);
        if (fingerprint.length >= 3) break;
      }
    }

    try {
      for (const dir of wikiDirs) {
        const fullDir = path.join(vaultPath, dir);
        if (!fs.existsSync(fullDir)) continue;
        for (const file of fs.readdirSync(fullDir)) {
          if (!file.endsWith(".md") || file.startsWith("_")) continue;
          const filePath = path.join(fullDir, file);
          const content = fs.readFileSync(filePath, "utf8");
          const pageName = file.replace(/\.md$/, "");
          const contentLower = content.substring(0, 600).toLowerCase();

          if (cleanUrl && (contentLower.includes(cleanUrl) || contentLower.includes(url.toLowerCase()))) {
            return { page: pageName, method: "URL" };
          }

          if (normTitle && normTitle.length > 10) {
            const titleMatch = content.match(/^title:\s*"?(.+?)"?\s*$/m);
            if (titleMatch) {
              const pageTitle = titleMatch[1].toLowerCase().replace(/[^a-z0-9\s]/g, "").replace(/\s+/g, " ").trim();
              if (pageTitle === normTitle || (pageTitle.length > 15 && normTitle.includes(pageTitle)) || (normTitle.length > 15 && pageTitle.includes(normTitle))) {
                return { page: pageName, method: "title" };
              }
              const stopWords = new Set(["the", "a", "an", "of", "for", "and", "in", "on", "to", "with", "is", "at"]);
              const wordsA = normTitle.split(" ").filter(w => w.length > 2 && !stopWords.has(w));
              const wordsB = pageTitle.split(" ").filter(w => w.length > 2 && !stopWords.has(w));
              if (wordsA.length >= 3 && wordsB.length >= 3) {
                const setA = new Set(wordsA);
                const overlap = wordsB.filter(w => setA.has(w)).length;
                const ratio = overlap / Math.min(wordsA.length, wordsB.length);
                if (ratio >= 0.6) {
                  return { page: pageName, method: "title (similar)" };
                }
              }
            }
          }

          if (fingerprint.length >= 2) {
            const bodyStart = content.indexOf("\n---", 3);
            if (bodyStart > 0) {
              const pageBody = content.substring(bodyStart + 4);
              const pageParagraphs = pageBody.split(/\n\s*\n/)
                .map(p => p.replace(/\s+/g, " ").trim().toLowerCase())
                .filter(p => p.length >= 50)
                .slice(0, 5);
              let matches = 0;
              for (const fp of fingerprint) {
                for (const pp of pageParagraphs) {
                  const shorter = fp.length < pp.length ? fp : pp;
                  const longer = fp.length < pp.length ? pp : fp;
                  if (longer.includes(shorter) || shorter.includes(longer.substring(0, shorter.length))) {
                    matches++;
                    break;
                  }
                }
              }
              if (matches >= 2) {
                return { page: pageName, method: "content" };
              }
            }
          }
        }
      }
    } catch (e) {
      console.log("[athena] dup check error:", e.message);
    }
    return null;
  }

  // ── LLM content processing (haiku/sonnet one-shot, JSON response) ──

  async llmProcessContent(rawContent, url, topicNames) {
    const claudePath = this.settings.claudePath || findClaudeBinary();
    if (!claudePath) return null;

    const isTweet = /x\.com|twitter\.com/i.test(url);
    const isRepo = /github\.com\/[^/]+\/[^/]+/i.test(url);
    const sourceHint = isTweet ? "tweet/social media post" : isRepo ? "GitHub repository" : "webpage";

    const content = rawContent.substring(0, 4000);
    const topicList = topicNames.slice(0, 30).join(", ");

    let namingRules = "";
    let taggingRules = "";
    try {
      const rulesPath = path.join(this.app.vault.adapter.basePath, "RULES.md");
      const rulesContent = fs.readFileSync(rulesPath, "utf8");
      const namingMatch = rulesContent.match(/## Naming Convention\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\n$)/);
      if (namingMatch) namingRules = namingMatch[1].trim();
      const taggingMatch = rulesContent.match(/## Tagging Rules\n[\s\S]*?\n([\s\S]*?)(?=\n## |\n---|\n$)/);
      if (taggingMatch) taggingRules = taggingMatch[1].trim();
    } catch {}
    if (!namingRules) {
      namingRules = `- Twitter/X posts: "X \u2014 <topic description>" (never include @username)
- GitHub repos: "Git \u2014 <repo-name> \u2014 <short description>" (never include owner/org)
- LinkedIn: plain topic title (no username, no platform prefix)
- Other: descriptive topic title
- Max 65 characters`;
    }

    const prompt = `You are a knowledge base assistant. Given this captured ${sourceHint}, return ONLY a JSON object (no markdown, no explanation).

Source URL: ${url}

Raw content:
${content}

Existing topic pages in the knowledge base: ${topicList}

Return this exact JSON structure:
{
  "title": "descriptive title following the naming convention below",
  "summary": "2-3 sentence summary of the key insight or content",
  "tags": ["tag1", "tag2"],
  "related": ["Exact Topic Page Name", "Another Topic"],
  "body": "cleaned markdown content \u2014 remove UI artifacts (navigation, follower counts, 'See new posts', etc), keep the substance"
}

Rules:
- title: Follow this EXACT naming convention:
${namingRules}
- title: no colons, no special filename chars (*?"<>|)
- title: use em dash (\u2014) not hyphen (-) in page titles
${taggingRules ? `- tags: follow these tagging rules:\n${taggingRules}` : "- tags: pick from [ai-agents, claude-code, llm, ml, security, deep-learning, memory, obsidian, python, rag, tools, skills, course, paper, repo, webpage, video]"}
- related: only include topic names from the list above that are genuinely related
- body: max 3000 chars, clean markdown, no HTML, no UI junk
- summary: 2-3 sentences, not one-liners. Focus on the key insight, not generic description`;

    return new Promise((resolve) => {
      const model = this.settings.model || "sonnet";
      // Windows .cmd shim handling: claudePath is typically claude.cmd
      // on Windows (npm install path). Node 20+ refuses to spawn .cmd
      // bare — returns EINVAL. wrapForCmdShim wraps with cmd.exe + the
      // windowsVerbatimArguments flag and handles arg quoting safely.
      // No-op on POSIX (isWindowsShim returns false).
      let spawnBin = claudePath;
      let spawnArgs = ["-p", prompt, "--model", model, "--max-turns", "1"];
      let extraSpawnOpts = {};
      if (isWindowsShim(claudePath)) {
        const wrapped = wrapForCmdShim(claudePath, spawnArgs);
        spawnBin = wrapped.command;
        spawnArgs = wrapped.args;
        extraSpawnOpts = wrapped.options || {};
      }
      const proc = spawn(spawnBin, spawnArgs, {
        cwd: this.app.vault.adapter.basePath,
        env: { ...process.env, PATH: buildEnhancedPath() },
        stdio: ["pipe", "pipe", "pipe"],
        ...extraSpawnOpts,
      });

      let stdout = "", stderr = "";
      const timer = setTimeout(() => {
        try { proc.kill("SIGTERM"); } catch {}
        console.log("[athena] LLM process timed out");
        resolve(null);
      }, 30000);

      proc.stdout.on("data", (d) => { stdout += d.toString(); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });
      proc.on("close", (code) => {
        clearTimeout(timer);
        console.log("[athena] LLM done", { code, len: stdout.length });
        // Surface stderr whenever claude.cmd exits non-zero. Without this,
        // a code:255 (auth failure, missing API key, model error, etc.)
        // is invisible — the user only sees "LLM done {code: 255, len: 0}"
        // followed by "LLM parse error: Unexpected end of JSON input" and
        // has no diagnostic to act on.
        if (code !== 0 && stderr) {
          console.log("[athena] LLM stderr (exit code " + code + "):",
            stderr.substring(0, 2000));
        }
        try {
          let json = stdout.trim();
          const jsonMatch = json.match(/\{[\s\S]*\}/);
          if (jsonMatch) json = jsonMatch[0];
          const parsed = JSON.parse(json);
          if (parsed.title && parsed.body) {
            resolve(parsed);
          } else {
            console.log("[athena] LLM returned incomplete JSON:", json.substring(0, 200));
            resolve(null);
          }
        } catch (e) {
          console.log("[athena] LLM parse error:", e.message, "raw:", stdout.substring(0, 300));
          resolve(null);
        }
      });
    });
  }

  // ── Browser-based capture (Electron's Chromium) ──────────────────

  abortBrowserCapture() {
    if (this._browserCaptureCleanup) {
      this._browserCaptureCleanup();
      this._browserCaptureCleanup = null;
    }
  }

  _probeElectronApis() {
    if (this._electronProbed) return;
    this._electronProbed = true;
    try {
      const electron = require("electron");
      console.log("[athena] electron module available:", Object.keys(electron).join(", "));
      if (electron.remote) {
        console.log("[athena] electron.remote available:", Object.keys(electron.remote).join(", "));
      }
      if (electron.ipcRenderer) {
        console.log("[athena] ipcRenderer available");
      }
      const BW = (electron.remote && electron.remote.BrowserWindow) ||
                 (electron.BrowserWindow);
      console.log("[athena] BrowserWindow:", BW ? "available" : "not available");
      this._electronBrowserWindow = BW || null;
      this._electron = electron;
    } catch (e) {
      console.log("[athena] electron module not available:", e.message);
      this._electronBrowserWindow = null;
      this._electron = null;
    }
    try {
      const remote = require("@electron/remote");
      console.log("[athena] @electron/remote available:", Object.keys(remote).join(", "));
      if (remote.BrowserWindow) {
        this._electronBrowserWindow = remote.BrowserWindow;
        console.log("[athena] BrowserWindow from @electron/remote: available");
      }
    } catch (e) {
      console.log("[athena] @electron/remote not available:", e.message);
    }
  }

  // Fetch a GitHub repo's README (gh-free) via the bundled Python helper
  // (bin/lib/fetch_github_readme.py). Returns the complete raw .md string,
  // or null on ANY failure (private repo, offline, rate-limited, Python
  // missing) so the caller cleanly falls back to its other capture paths.
  // Repos must never use the generic DOM walker — it mangles README images
  // (forces width=600, drops ![]() thumbnails). Witnessed: roboflow/notebooks
  // (2026-06-08). The helper reuses the same image-rewrite as the CLI's
  // kb-capture, so plugin + CLI repo captures stay consistent.
  _captureGithubReadme(owner, repo, url) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
      try {
        execFile(
          pythonCmd(),
          [
            resolvePythonScript(this, "bin/lib/fetch_github_readme.py"),
            owner,
            repo.replace(/\.git$/, ""),
            url,
          ],
          { encoding: "utf8", timeout: 20000, maxBuffer: 32 * 1024 * 1024 },
          (err, stdout) => {
            if (err) {
              console.warn("[athena] gh-free README fetch failed, falling back:",
                err.message);
              return finish(null);
            }
            const raw = (stdout || "").trim();
            finish(raw.length > 200 ? raw : null);
          },
        );
      } catch (e) {
        console.warn("[athena] gh-free README fetch threw:", e && e.message);
        finish(null);
      }
    });
  }

  // Fetch an X/Twitter status via the bundled Python helper
  // (bin/lib/fetch_tweet.py), which reads the public syndication CDN — no auth,
  // no Playwright. Returns the complete raw .md string, or null on ANY failure
  // (deleted/protected tweet, offline, rate-limited, Python missing) so the
  // caller cleanly falls back to its browser-capture path. Tweets must never
  // use the generic DOM walker: for X Articles the visible body is just a t.co
  // pointer, so the walker captures a truncated preview + shortlink title.
  // Witnessed: FakeMaidenMaker/status/2064900447375085823 (2026-06-12).
  _captureTweet(url) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (v) => { if (!settled) { settled = true; resolve(v); } };
      try {
        execFile(
          pythonCmd(),
          [resolvePythonScript(this, "bin/lib/fetch_tweet.py"), url],
          { encoding: "utf8", timeout: 20000, maxBuffer: 32 * 1024 * 1024 },
          (err, stdout) => {
            if (err) {
              console.warn("[athena] syndication tweet fetch failed, falling back:",
                err.message);
              return finish(null);
            }
            const raw = (stdout || "").trim();
            finish(raw.length > 80 ? raw : null);
          },
        );
      } catch (e) {
        console.warn("[athena] syndication tweet fetch threw:", e && e.message);
        finish(null);
      }
    });
  }

  // updateStatus (optional) surfaces fallback transitions in the chat
  // status line. Without it the user sees "Capturing URL..." for the
  // full duration of all retries (up to 30s) with no feedback. With it
  // they see "Browser capture failed, trying webview..." etc. and can
  // ctrl+C or wait knowingly.
  async browserCapture(url, updateStatus = null) {
    this._probeElectronApis();

    if (this._electronBrowserWindow) {
      console.log("[athena] trying BrowserWindow capture for:", url);
      const text = await this._browserWindowCapture(url);
      if (text) return text;
      if (updateStatus) updateStatus("Browser capture failed, trying webview...");
    }

    console.log("[athena] trying webview capture for:", url);
    return this._webviewCapture(url);
  }

  async _browserWindowCapture(url) {
    const BW = this._electronBrowserWindow;
    if (!BW) return null;
    let win = null;
    try {
      win = new BW({
        width: 1280,
        height: 900,
        show: false,
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true,
          // Sandbox stays on — Athena's CLAUDE.md requires it for browser
          // capture. On Linux this can cause the renderer to hang if the
          // chrome-sandbox helper is misconfigured (AppArmor + Snap
          // Obsidian is the common case); the outer 15s timeout below is
          // the fail-safe so the user falls through to webview/shell
          // instead of staring at "Capturing URL..." forever.
          sandbox: true,
        },
      });
      // Wrap the full load+extract chain in a 15s race. A hung loadURL
      // (the Linux-sandbox failure mode) leaves the entire await chain
      // unfired — including the 6s setTimeout that's nested inside it —
      // so without this race the browserCapture path never falls
      // through to _webviewCapture. Matches _webviewCapture's existing
      // 15s budget for symmetry.
      // 1.0.16: extract title + text + images in one JS round-trip.
      // Returns { title, text, images: [{src, alt}] } so the calling
      // code can build a real frontmatter title (was hardcoded "Page"
      // for generic webpages) and append captured images to the raw
      // body (was text-only — image-heavy pages like Cisco blog posts
      // landed with no images at all). All within the same 15s outer
      // timeout that protects against Linux sandbox hangs.
      const data = await Promise.race([
        (async () => {
          await win.loadURL(url);
          await new Promise(r => setTimeout(r, 6000));
          return await win.webContents.executeJavaScript(_BROWSER_EXTRACT_JS);
        })(),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("BrowserWindow timeout after 15s")), 15000)
        ),
      ]);
      try { win.close(); } catch {}
      const text = (data && data.text) ? data.text.trim() : "";
      const images = (data && data.images) || [];
      console.log("[athena] BrowserWindow extracted:",
        text.length + " chars,",
        images.length + " image(s),",
        "title=" + JSON.stringify((data && data.title) || ""));
      if (text.length <= 100) return null;
      return { title: (data && data.title) || "", text: text, images: images };
    } catch (e) {
      console.log("[athena] BrowserWindow capture failed:", e.message);
      try { if (win && !win.isDestroyed()) win.close(); } catch {}
      return null;
    }
  }

  async _webviewCapture(url) {
    return new Promise((resolve) => {
      let resolved = false;

      try {
        const testWv = document.createElement("webview");
        if (!testWv.executeJavaScript) {
          console.log("[athena] webview not supported \u2014 no executeJavaScript");
          resolve(null);
          return;
        }
        console.log("[athena] webview tag supported");
      } catch (e) {
        console.log("[athena] webview not available:", e.message);
        resolve(null);
        return;
      }

      const webview = document.createElement("webview");
      webview.setAttribute("src", url);
      webview.setAttribute("useragent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36");
      webview.style.width = "1280px";
      webview.style.height = "900px";
      webview.style.position = "absolute";
      webview.style.left = "-9999px";
      document.body.appendChild(webview);

      const cleanup = () => {
        if (!resolved) resolved = true;
        try { document.body.removeChild(webview); } catch {}
      };

      this._browserCaptureCleanup = () => { cleanup(); resolve(null); };

      const timeout = setTimeout(() => {
        console.log("[athena] webview timeout \u2014 giving up after 15s");
        if (!resolved) { resolved = true; cleanup(); resolve(null); }
      }, 15000);

      webview.addEventListener("dom-ready", () => {
        console.log("[athena] webview dom-ready, waiting 6s for JS rendering...");
        setTimeout(async () => {
          if (resolved) return;
          try {
            // 1.0.16: same extraction as _browserWindowCapture — title +
            // text + images, returned as {title, text, images}.
            const data = await webview.executeJavaScript(_BROWSER_EXTRACT_JS);
            clearTimeout(timeout);
            cleanup();
            resolved = true;
            const text = (data && data.text) ? data.text.trim() : "";
            const images = (data && data.images) || [];
            console.log("[athena] webview extracted:",
              text.length + " chars,",
              images.length + " image(s),",
              "title=" + JSON.stringify((data && data.title) || ""));
            if (text.length <= 100) { resolve(null); return; }
            resolve({ title: (data && data.title) || "", text: text, images: images });
          } catch (e) {
            console.log("[athena] webview extract failed:", e.message);
            clearTimeout(timeout);
            cleanup();
            resolved = true;
            resolve(null);
          }
        }, 6000);
      });

      webview.addEventListener("did-fail-load", (event) => {
        console.log("[athena] webview did-fail-load:", event.errorCode, event.errorDescription);
        if (!resolved) { resolved = true; clearTimeout(timeout); cleanup(); resolve(null); }
      });

      webview.addEventListener("console-message", (event) => {
        console.log("[athena] webview console:", event.message);
      });
    });
  }

  // ── File watchers (clip dirs + url-new.txt) ──────────────────────

  /**
   * Parse settings.clippingsFolder into absolute directory paths.
   * Comma-separated, deduped, leading-slash-stripped, empty-item-filtered.
   */
  _resolveClipDirs() {
    const vaultPath = this.app.vault.adapter.basePath;
    const raw = this.settings.clippingsFolder || "clippings, inbox/Clippings";
    const paths = raw.split(",")
      .map((p) => p.trim().replace(/^\/+/, ""))
      .filter(Boolean);
    const seen = new Set();
    const out = [];
    for (const p of paths) {
      const abs = path.join(vaultPath, p);
      if (!seen.has(abs)) { seen.add(abs); out.push(abs); }
    }
    return out;
  }

  _setupClipWatcher(clipDir) {
    try {
      const watcher = fs.watch(clipDir, (eventType, filename) => {
        const skipFiles = ["URL Tracker.md", "Add URLs.md", "url-new.txt"];
        if (eventType === "rename" && filename && filename.endsWith(".md") && !filename.startsWith(".")
            && !skipFiles.includes(filename)) {
          const filePath = path.join(clipDir, filename);
          if (!this._processedClips) this._processedClips = new Set();
          // Key by absolute path — same filename can exist in multiple
          // watched dirs and we don't want one to block the other.
          if (this._processedClips.has(filePath)) return;
          this._processedClips.add(filePath);
          setTimeout(async () => {
            try {
              if (!fs.existsSync(filePath)) return;
              console.log("[athena] New clip detected:", filePath);
              await this._handleClipFile(clipDir, filename, filePath);
            } catch (e) {
              console.error("[athena] _setupClipWatcher clip handler error:", e);
              this._processedClips.delete(filePath);  // allow watchdog retry
            }
          }, 2000);
        }
      });
      this._clipWatchers.push({ dir: clipDir, watcher });
    } catch (e) {
      console.log("[athena] Could not watch", clipDir, ":", e.message);
    }
  }

  /** Shared clip processing for both initial watcher and watchdog retry. */
  async _handleClipFile(clipDir, filename, filePath) {
    try {
      let view = this._getActiveView();
      if (!view) {
        // No Athena panel open \u2014 open one so the clip ingests now instead of
        // waiting for the user to notice. Bounded auto-open (see helper).
        new Notice(`Athena: Clip saved \u2014 processing\u2026`);
        view = await this._tryOpenViewForClip(filePath);
      }
      if (!view || view.isStreaming) {
        // Couldn't ingest right now \u2014 un-mark so the 60s watchdog retries
        // when a view exists, instead of stranding the clip until a reload.
        if (this._processedClips) this._processedClips.delete(filePath);
        return;
      }

      let clipUrl = "", clipContent = "";
      try {
        clipContent = fs.readFileSync(filePath, "utf8");
        const urlMatch = clipContent.match(/source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                         clipContent.match(/url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
        if (urlMatch) clipUrl = urlMatch[1];
      } catch {}
      const cleanFilename = filename.replace(/^\(\d+\)\s*/, "");
      const clipName = cleanFilename.replace(".md", "");
      const clipTitleMatch = clipContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m) || clipContent.match(/^#\s+(.+)/m);
      const clipTitle = clipTitleMatch ? clipTitleMatch[1].trim() : clipName;

      // Route through bin/lib/process_clip.py — the canonical writer that
      // applies URL-derived slug (slug.derive_slug), per-host URL canonicalization
      // (url_canonical.canonicalize), and schema validation (raw_writer).
      // Replaces the title-derived slug bypass that produced collision-bait
      // raws like raw/webpages/artifacts/post-linkedin.md whenever LinkedIn
      // served the generic "Post | LinkedIn" title (every LinkedIn post does).
      // Subprocess adds ~150ms latency but eliminates drift between JS and
      // Python slug derivation forever.
      const vaultBase = this.app.vault.adapter.basePath;
      let rawPath, fullRawPath;
      try {
        const out = execFileSync(pythonCmd(), [
          // Plugin-bundled (1.0.9+) → vault-side fallback.
          resolvePythonScript(this, "bin/lib/process_clip.py"),
          vaultBase,
          filePath,
        ], { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] }).trim();
        fullRawPath = out;
        rawPath = path.relative(vaultBase, fullRawPath);
      } catch (e) {
        const stderr = (e.stderr && e.stderr.toString()) || e.message || String(e);
        console.error("[athena] process_clip failed for clip:", filename, stderr);
        new Notice(`Athena: Clip processing failed — ${stderr.split("\n")[0]}`);
        if (this._processedClips) this._processedClips.delete(filePath);
        return;
      }
      const processedDir = path.join(clipDir, ".processed");
      try { fs.mkdirSync(processedDir, { recursive: true }); } catch {}
      try { fs.renameSync(filePath, path.join(processedDir, filename)); } catch {}

      view.addSystemMessage(`New clip received: ${clipName}`);
      view.startStreamingMessage();
      view.isStreaming = true;
      // Per Gryphon issue #3: input stays enabled while clip ingest runs.
      try {
        const result = await this.ingestContent({
          url: clipUrl || null,
          content: clipContent,
          title: clipTitle,
          rawPath: rawPath,
          source: "web-clipper",
          view,
        });
        if (result.status === "duplicate") {
          view.finalizeStreamingMessage(
            `**Already in knowledge base:** [[${result.dupPage}]]\n\nMatched by ${result.dupMethod}.`,
            `Already captured: ${result.dupPage}`
          );
        } else if (result.status === "updated") {
          view.finalizeStreamingMessage(
            `**Updated:** [[${result.pageName}]]\n\nRefreshed with better content from clip.` +
            (result.summary ? `\n\n${result.summary}` : ""),
            `Updated: ${result.pageName}`
          );
        } else if (result.status === "created") {
          const lines = [];
          if (result.pageName) {
            lines.push(`**Captured:** ${clipUrl || clipName}`);
            lines.push(`**Page created:** [[${result.pageName}]]`);
            if (result.summary) lines.push(`\n${result.summary}`);
          } else {
            lines.push(`**Clip saved:** ${clipUrl || clipName}`);
          }
          view.finalizeStreamingMessage(lines.join("\n"), result.pageName ? `Page created: ${result.pageName}` : "Clip saved");
        } else {
          view.finalizeStreamingMessage(result.summary || "Clip processing failed.", "Failed");
        }
      } finally {
        view.isStreaming = false;
        // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
        // prompts that arrived during the clip ingest.
        if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
      }
    } catch (e) {
      console.error("[athena] _handleClipFile error:", e);
      if (this._processedClips) this._processedClips.delete(filePath);  // allow watchdog retry
    }
  }

  _setupUrlNewWatcher() {
    const urlNewPath = path.join(this.app.vault.adapter.basePath, "inbox", "url-new.txt");
    // fs.watch throws ENOENT if the file doesn't exist — on a fresh
    // vault install (no Python backend) it never has. Create as empty
    // so the watcher attaches and future URL drops trigger ingest.
    // Parent inbox/ is ensured in onload() before this runs.
    try {
      if (!fs.existsSync(urlNewPath)) fs.writeFileSync(urlNewPath, "");
    } catch (e) {
      console.log("[athena] Could not create url-new.txt:", e.message);
      return;
    }
    try {
      this._urlNewWatcher = fs.watch(urlNewPath, (eventType) => {
        if (eventType !== "change") return;
        this._processUrlNewDebounced();
      });
    } catch (e) {
      console.log("[athena] Could not watch url-new.txt:", e.message);
    }
  }

  _processUrlNewDebounced() {
    if (this._urlNewTimer) clearTimeout(this._urlNewTimer);
    this._urlNewTimer = setTimeout(() => this._processUrlNew(), 3000);
  }

  async _processUrlNew() {
    const vaultPath = this.app.vault.adapter.basePath;
    const urlNewPath = path.join(vaultPath, "inbox", "url-new.txt");
    try {
      const content = fs.readFileSync(urlNewPath, "utf8").trim();
      if (!content) return;
      const urls = content.split("\n").map(l => l.trim()).filter(l => l.startsWith("http"));
      if (urls.length === 0) return;
      console.log("[athena] processing url-new.txt:", urls.length, "URLs");

      const view = this._getActiveView();
      if (view) {
        if (view.isStreaming) return;

        view.addSystemMessage(`New URL${urls.length > 1 ? "s" : ""} detected in inbox (${urls.length})`);
        view.startStreamingMessage();
        view.isStreaming = true;
        // Per Gryphon issue #3: input stays enabled while URLs ingest.
        let created = 0;
        const skippedDups = [];

        try {
          for (const url of urls) {
            view.updateStatus(`Processing ${url.substring(0, 50)}...`);
            const result = await this.ingestContent({ url, source: "url-new", view });
            if (result.status === "created" || result.status === "updated") {
              created++;
            } else if (result.status === "duplicate") {
              skippedDups.push({ url, page: result.dupPage });
            }
          }

          const lines = [];
          if (created > 0) lines.push(`**${created} page${created > 1 ? "s" : ""} created**`);
          if (skippedDups.length > 0) {
            lines.push("");
            lines.push(`**${skippedDups.length} already in knowledge base:**`);
            for (const d of skippedDups) lines.push(`- [[${d.page}]]`);
          }
          view.finalizeStreamingMessage(
            lines.join("\n") || "Nothing to process.",
            created > 0 ? `${created} page${created > 1 ? "s" : ""} created` : "Nothing to process"
          );
        } finally {
          view.isStreaming = false;
          // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
          // prompts the user typed during the URL ingest.
          if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
        }
        try { fs.writeFileSync(urlNewPath, ""); } catch {}
      } else {
        new Notice(`Athena: ${urls.length} URL(s) queued \u2014 open Athena to process.`);
      }
    } catch (e) {
      console.log("[athena] url-new.txt processing error:", e.message);
    }
  }

  /** Watchdog: health check on startup + every 60s. */
  async _watchdogCheck() {
    const vaultPath = this.app.vault.adapter.basePath;
    // Console-spam reduction (2026-05-19): only log when the watchdog
    // actually finds something to do. The previous "[athena] watchdog
    // check" line per 60s tick accumulated to hundreds of log lines
    // during a long Obsidian session with nothing meaningful between
    // them. Restart-watcher + queue-found events still log loudly.

    // 1. Restart any missing clip watchers. We walk the configured dir
    //    list; any dir that's not already covered in this._clipWatchers
    //    gets a fresh watcher attached (via _setupClipWatcher so the
    //    dedup + handoff logic matches the initial watcher exactly).
    if (!this._clipWatchers) this._clipWatchers = [];
    const covered = new Set(this._clipWatchers.map((w) => w.dir));
    for (const dir of this._resolveClipDirs()) {
      if (!covered.has(dir) && fs.existsSync(dir)) {
        console.log("[athena] watchdog: restarting clip watcher for", dir);
        this._setupClipWatcher(dir);
      }
    }

    const urlNewPath = path.join(vaultPath, "inbox", "url-new.txt");
    if (!this._urlNewWatcher) {
      try {
        if (fs.existsSync(urlNewPath)) {
          console.log("[athena] watchdog: restarting url-new watcher");
          this._urlNewWatcher = fs.watch(urlNewPath, () => this._processUrlNewDebounced());
        }
      } catch (e) { console.log("[athena] watchdog: url-new watcher restart failed:", e.message); }
    }

    // 2. Process pending items
    try {
      if (fs.existsSync(urlNewPath)) {
        const content = fs.readFileSync(urlNewPath, "utf8").trim();
        const urls = content.split("\n").map(l => l.trim()).filter(l => l.startsWith("http"));
        if (urls.length > 0) {
          console.log("[athena] watchdog: found", urls.length, "pending URLs in url-new.txt");
          this._processUrlNewDebounced();
        }
      }
    } catch {}

    try {
      const skipFiles = new Set(["URL Tracker.md", "Add URLs.md"]);
      for (const dir of this._resolveClipDirs()) {
        if (!fs.existsSync(dir)) continue;
        const clips = fs.readdirSync(dir).filter(f => f.endsWith(".md") && !f.startsWith(".") && !skipFiles.has(f));
        if (clips.length > 0) {
          console.log("[athena] watchdog: found", clips.length, "unprocessed clip(s) in", dir);
          for (const clip of clips) {
            this._processClip(dir, clip);
          }
        }
      }
    } catch {}
  }

  /** Watchdog clip processor — simpler than _handleClipFile, used for clips
   *  discovered after the file watcher already fired (or never fired). */
  _processClip(clipDir, filename) {
    const filePath = path.join(clipDir, filename);
    if (!this._processedClips) this._processedClips = new Set();
    if (this._processedClips.has(filePath)) return;
    this._processedClips.add(filePath);
    setTimeout(async () => {
      try {
        if (!fs.existsSync(filePath)) return;
        let view = this._getActiveView();
        if (!view) {
          // No Athena panel open — clips arrive while the user is in their
          // browser, not focused on Athena. Try to open the view, but bound the
          // auto-open attempts so a vault where activateView can't place a leaf
          // doesn't churn forever (see _tryOpenViewForClip).
          view = await this._tryOpenViewForClip(filePath);
        }
        if (!view || view.isStreaming) {
          // Couldn't get a usable view (open failed/capped, or a turn is
          // mid-stream). Un-mark so a later tick retries when a view exists —
          // otherwise the clip is stranded in _processedClips until reload.
          this._processedClips.delete(filePath);
          return;
        }
        console.log("[athena] watchdog: processing clip", filename);

        // Watchdog uses a simpler finalization (shorter messages)
        let clipContent = "", clipUrl = "";
        try {
          clipContent = fs.readFileSync(filePath, "utf8");
          const urlMatch = clipContent.match(/source:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m) ||
                           clipContent.match(/url:\s*['"]?(https?:\/\/\S+?)['"]?\s*$/m);
          if (urlMatch) clipUrl = urlMatch[1];
        } catch {}
        const cleanFilename = filename.replace(/^\(\d+\)\s*/, "");
        const clipName = cleanFilename.replace(".md", "");
        const clipTitleMatch = clipContent.match(/^title:\s*['"]?(.+?)['"]?\s*$/m) || clipContent.match(/^#\s+(.+)/m);
        const clipTitle = clipTitleMatch ? clipTitleMatch[1].trim() : clipName;

        // Same as _handleClipFile — route through process_clip.py to use
        // the canonical URL-derived slug. Watchdog retry path; second clip
        // arriving for the same title would collide on the title-derived
        // bypass. See _handleClipFile for full rationale.
        const vaultBase = this.app.vault.adapter.basePath;
        let rawPath, fullRawPath;
        try {
          const out = execFileSync(pythonCmd(), [
            // Plugin-bundled (1.0.9+) → vault-side fallback.
            resolvePythonScript(this, "bin/lib/process_clip.py"),
            vaultBase,
            filePath,
          ], { encoding: "utf8", timeout: 30000, stdio: ["ignore", "pipe", "pipe"] }).trim();
          fullRawPath = out;
          rawPath = path.relative(vaultBase, fullRawPath);
        } catch (e) {
          const stderr = (e.stderr && e.stderr.toString()) || e.message || String(e);
          console.error("[athena] process_clip failed for retry clip:", filename, stderr);
          new Notice(`Athena: Clip processing failed — ${stderr.split("\n")[0]}`);
          if (this._processedClips) this._processedClips.delete(filePath);
          return;
        }
        const processedDir = path.join(clipDir, ".processed");
        try { fs.mkdirSync(processedDir, { recursive: true }); } catch {}
        try { fs.renameSync(filePath, path.join(processedDir, filename)); } catch {}

        view.addSystemMessage(`New clip received: ${clipName}`);
        view.startStreamingMessage();
        view.isStreaming = true;
        // Per Gryphon issue #3: input stays enabled while clip ingest runs.
        try {
          const result = await this.ingestContent({
            url: clipUrl || null, content: clipContent, title: clipTitle,
            rawPath: rawPath, source: "web-clipper", view,
          });
          if (result.status === "created" && result.pageName) {
            view.finalizeStreamingMessage(`**Page created:** [[${result.pageName}]]`, `Page created: ${result.pageName}`);
          } else if (result.status === "duplicate") {
            view.finalizeStreamingMessage(`**Already in KB:** [[${result.dupPage}]]`, `Already captured`);
          } else {
            view.finalizeStreamingMessage("Clip processed.", "Clip processed");
          }
        } finally {
          view.isStreaming = false;
          // Per Gryphon issue #3: don't re-toggle disabled. Drain queued
          // prompts the user typed during the watchdog clip processing.
          if (typeof view._drainQueuedPrompts === "function") view._drainQueuedPrompts();
        }
      } catch (e) {
        console.error("[athena] _processClip error:", e);
        this._processedClips.delete(filePath);  // allow watchdog retry
      }
    }, 2000);
  }
}

// ── Settings Tab ───────────────────────────────────────────────────

class AthenaSettingTab extends PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
  }

  display(containerEl) {
    // Obsidian passes containerEl, but also sets this.containerEl — accept either
    const el = containerEl || this.containerEl;
    el.empty();
    el.createEl("h2", { text: "Athena Settings" });

    const vaultName = this.app.vault.getName();
    new Setting(el)
      .setName("Web Clipper")
      .setDesc(`Install from obsidian.md/clipper. Vault name: "${vaultName}", Folder: "inbox/clippings"`)
      .addButton((btn) => {
        btn.setButtonText("Copy vault name").onClick(() => {
          navigator.clipboard.writeText(vaultName);
          new Notice(`Copied: "${vaultName}"`);
        });
      });

    new Setting(el)
      .setName("Claude Code CLI path")
      .setDesc("Leave empty to auto-detect.")
      .addText((text) => {
        text.setPlaceholder("Auto-detect")
          .setValue(this.plugin.settings.claudePath)
          .onChange(async (v) => { this.plugin.settings.claudePath = v; await this.plugin.saveSettings(); });
      });

    // Helper for #122: GryphonChatView caches settings at construction, so
    // changing provider/model/effort/permissionMode doesn't refresh the
    // running view's badges or spawn options. Until Gryphon implements
    // reactive settings (polleoai/gryphon#40), we surface a Notice telling
    // the user how to apply the change without losing chat state.
    const showRefreshNotice = (settingName) => {
      new Notice(
        `${settingName} updated. Reopen the Athena chat tab to apply, or toggle the plugin off/on.`,
        7000
      );
    };

    new Setting(el)
      .setName("LLM provider")
      .setDesc("Which backend Gryphon uses to reach the model. \"Auto\" prefers Claude Code if installed, else the first available API key (Anthropic \u2192 OpenAI \u2192 Google).")
      .addDropdown((d) => {
        for (const p of PROVIDER_PREFS) d.addOption(p.value, p.label + " \u2014 " + p.desc);
        d.setValue(this.plugin.settings.providerPreference || "auto")
          .onChange(async (v) => {
            this.plugin.settings.providerPreference = v;
            await this.plugin.saveSettings();
            showRefreshNotice("LLM provider");
            // Re-render the settings tab so downstream provider-dependent
            // sections (model dropdown labels, API-key fields, CLI-path
            // fields, health-check button) reflect the new provider.
            // Without this, the user sees the provider value change but
            // nothing else updates until they reopen the settings tab.
            this.display();
          });
      });

    // Provider-specific Default model dropdown. Mirrors Gryphon's
    // three-branch pattern in vendor/gryphon/src/plugin.js \u2014 without it,
    // OpenAI / Gemini users only see the Anthropic abstract tiers
    // (Haiku / Sonnet / Opus) and the dropdown looks broken because none
    // of those are real OpenAI or Gemini model ids. Auto resolves to the
    // currently-active provider so users with only one key see that
    // provider's native list.
    const { getActiveProviderKind } = require("../../vendor/gryphon/src/providers/factory");
    const _activeKind = getActiveProviderKind(this.plugin) ||
                        this.plugin.settings.providerPreference || "auto";

    if (_activeKind === "google-api" || _activeKind === "gemini-cli") {
      const {
        getModelDropdownOptions: getGeminiOptions,
        resolveModel: resolveGeminiModel,
        DEFAULT_MODEL: GEMINI_DEFAULT_MODEL,
      } = require("../../vendor/gryphon/src/providers/google-api/pricing");
      const geminiModels = getGeminiOptions();
      // Auto-correct stale cross-vendor ids (e.g. "sonnet" carried over
      // from prior Anthropic use) so the displayed dropdown value, the
      // chat toolbar, and runtime model resolution all agree.
      const isKnown = geminiModels.some((o) => o.id === this.plugin.settings.model);
      if (!isKnown) {
        const resolved = resolveGeminiModel(this.plugin.settings.model);
        const fitsDropdown = geminiModels.some((o) => o.id === resolved);
        const persistTarget = fitsDropdown ? resolved : GEMINI_DEFAULT_MODEL;
        if (this.plugin.settings.model !== persistTarget) {
          this.plugin.settings.model = persistTarget;
          this.plugin.saveSettings();
        }
      }
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar.")
        .addDropdown((d) => {
          for (const m of geminiModels) d.addOption(m.id, m.label);
          d.setValue(this.plugin.settings.model)
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    } else if (_activeKind === "openai-api" || _activeKind === "codex-cli") {
      const {
        getModelDropdownOptions: getOpenAIOptions,
        resolveModel: resolveOpenAIModel,
        DEFAULT_MODEL: OPENAI_DEFAULT_MODEL,
      } = require("../../vendor/gryphon/src/providers/openai-api/pricing");
      const openaiModels = getOpenAIOptions();
      const isKnown = openaiModels.some((o) => o.id === this.plugin.settings.model);
      if (!isKnown) {
        const resolved = resolveOpenAIModel(this.plugin.settings.model);
        const fitsDropdown = openaiModels.some((o) => o.id === resolved);
        const persistTarget = fitsDropdown ? resolved : OPENAI_DEFAULT_MODEL;
        if (this.plugin.settings.model !== persistTarget) {
          this.plugin.settings.model = persistTarget;
          this.plugin.saveSettings();
        }
      }
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar.")
        .addDropdown((d) => {
          for (const m of openaiModels) d.addOption(m.id, m.label);
          d.setValue(this.plugin.settings.model)
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    } else {
      // Anthropic family (claude-code / anthropic-api / auto-resolves-to-Anthropic)
      // uses the abstract MODELS list \u2014 Gryphon maps these to concrete
      // versions at chat time, so haiku/sonnet/opus/opus[1m] are the
      // right surface here.
      new Setting(el)
        .setName("Default model")
        .setDesc("Also changeable from the chat toolbar. Gryphon resolves these tiers to the latest concrete versions at chat time.")
        .addDropdown((d) => {
          for (const m of MODELS) d.addOption(m.value, m.label + " \u2014 " + m.desc);
          // If the current model is a non-Anthropic id (e.g. user just
          // switched from OpenAI to Anthropic), fall back to "sonnet".
          const _currentValid = MODELS.some((m) => m.value === this.plugin.settings.model);
          d.setValue(_currentValid ? this.plugin.settings.model : "sonnet")
            .onChange(async (v) => {
              this.plugin.settings.model = v;
              await this.plugin.saveSettings();
              showRefreshNotice("Default model");
            });
        });
    }

    new Setting(el)
      .setName("Default effort")
      .addDropdown((d) => {
        for (const e of EFFORTS) d.addOption(e.value, e.label + " \u2014 " + e.desc);
        d.setValue(this.plugin.settings.effort)
          .onChange(async (v) => {
            this.plugin.settings.effort = v;
            await this.plugin.saveSettings();
            showRefreshNotice("Default effort");
          });
      });

    new Setting(el)
      .setName("Default permission mode")
      .setDesc("Safe = auto-accept edits. YOLO = skip all checks. Plan = propose only.")
      .addDropdown((d) => {
        for (const p of PERMS) d.addOption(p.value, p.label + " \u2014 " + p.desc);
        d.setValue(this.plugin.settings.permissionMode)
          .onChange(async (v) => {
            this.plugin.settings.permissionMode = v;
            await this.plugin.saveSettings();
            showRefreshNotice("Default permission mode");
          });
      });

    // Issue #133: connection-timeout override, mirroring Gryphon's tab
    // (Gryphon #38 in v1.4.0). Empty input = use the model-adaptive
    // default \u2014 Haiku 30s, Sonnet 60s, Opus 120s, Opus 1M 180s; non-
    // Anthropic providers 60s. 5\u2013600 second range; out-of-range silently
    // ignored to avoid noisy mid-typing errors. Status line below shows
    // the effective timeout so users see what was accepted.
    let timeoutStatusEl = null;
    const updateTimeoutStatus = (rawInput) => {
      if (!timeoutStatusEl) return;
      const trimmed = (rawInput || "").trim();
      const effectiveMs = resolveConnectionTimeoutMs({
        override: this.plugin.settings.connectionTimeoutMs,
        model: this.plugin.settings.model,
      });
      const effectiveSec = Math.round(effectiveMs / 1000);
      let prefix;
      let color = "";
      if (!trimmed) {
        prefix = `Using model-adaptive default: ${effectiveSec}s`;
      } else {
        const sec = Number(trimmed);
        if (Number.isFinite(sec) && sec >= 5 && sec <= 600) {
          prefix = `\u2713 Override active: ${effectiveSec}s`;
          color = "var(--color-green)";
        } else {
          prefix = `\u2717 Invalid: must be 5\u2013600 seconds. Currently using: ${effectiveSec}s`;
          color = "var(--color-red)";
        }
      }
      timeoutStatusEl.setText(prefix);
      timeoutStatusEl.style.color = color;
    };

    new Setting(el)
      .setName("Connection timeout (seconds)")
      .setDesc(
        "How long to wait for the model's first token before treating " +
        "the request as stuck. Leave empty for the model-adaptive " +
        "default (Haiku 30s, Sonnet 60s, Opus 120s, Opus 1M 180s; " +
        "non-Anthropic providers 60s). Set 5\u2013600 to override for " +
        "slow networks or unusually large prompts."
      )
      .addText((text) => {
        const stored = this.plugin.settings.connectionTimeoutMs;
        const display = (typeof stored === "number" && Number.isFinite(stored) && stored > 0)
          ? String(Math.round(stored / 1000))
          : "";
        text
          .setPlaceholder("default")
          .setValue(display)
          .onChange(async (value) => {
            const trimmed = (value || "").trim();
            if (!trimmed) {
              this.plugin.settings.connectionTimeoutMs = null;
              await this.plugin.saveSettings();
              updateTimeoutStatus(value);
              return;
            }
            const sec = Number(trimmed);
            if (Number.isFinite(sec) && sec >= 5 && sec <= 600) {
              this.plugin.settings.connectionTimeoutMs = Math.round(sec) * 1000;
              await this.plugin.saveSettings();
            }
            // Out-of-range or non-numeric: don't persist. Status line
            // below shows the validation error AND the effective fallback
            // so the user sees their input was rejected.
            updateTimeoutStatus(value);
          });
      })
      .then((setting) => {
        timeoutStatusEl = setting.descEl.createDiv({ cls: "setting-item-description" });
        timeoutStatusEl.style.marginTop = "4px";
        timeoutStatusEl.style.fontStyle = "italic";
        const stored = this.plugin.settings.connectionTimeoutMs;
        const initialDisplay = (typeof stored === "number" && Number.isFinite(stored) && stored > 0)
          ? String(Math.round(stored / 1000))
          : "";
        updateTimeoutStatus(initialDisplay);
      });

    new Setting(el)
      .setName("Open in main tab")
      .setDesc("Open chat in a main tab instead of the right sidebar.")
      .addToggle((t) => {
        t.setValue(this.plugin.settings.openInMainTab)
          .onChange(async (v) => { this.plugin.settings.openInMainTab = v; await this.plugin.saveSettings(); });
      });

    new Setting(el)
      .setName("Web Clipper folders")
      .setDesc(
        "Comma-separated list of folders (relative to vault root) " +
        "Athena watches for new Web Clipper files. Defaults cover the " +
        "Obsidian Web Clipper extension's factory default (clippings/) " +
        "and the legacy path (inbox/Clippings). Change takes effect " +
        "after restarting Obsidian."
      )
      .addText((t) => {
        t.setPlaceholder("clippings, inbox/Clippings")
          .setValue(this.plugin.settings.clippingsFolder || "clippings, inbox/Clippings")
          .onChange(async (v) => {
            this.plugin.settings.clippingsFolder = v;
            await this.plugin.saveSettings();
          });
      });

    // Provider-aware test button. Routes the health check based on the
    // selected LLM provider (#117). For CLI providers, spawn `<cli> --version`.
    // For API providers, surface an info message \u2014 Athena doesn't have the
    // credentials needed to run a real API health check (Gryphon owns those).
    el.createEl("h3", { text: "Test" });
    const testBtn = el.createEl("button", { text: "Test Connection" });
    testBtn.addEventListener("click", async () => {
      const provider = this.plugin.settings.providerPreference || "auto";

      // Map provider \u2192 { label, binaryFinder, defaultName, args }.
      // For "auto", try claude first (matches Gryphon's auto-fallback chain
      // which prefers Claude Code if installed).
      const cliMap = {
        "claude-code": { label: "Claude Code", bin: this.plugin.settings.claudePath || findClaudeBinary(), name: "claude" },
        "codex-cli":   { label: "Codex CLI",   bin: "codex", name: "codex" },
        "gemini-cli":  { label: "Gemini CLI",  bin: "gemini", name: "gemini" },
        "auto":        { label: "Claude Code (auto)", bin: this.plugin.settings.claudePath || findClaudeBinary(), name: "claude" },
      };
      const apiSet = new Set(["anthropic-api", "openai-api", "google-api"]);

      if (apiSet.has(provider)) {
        new Notice(`Provider '${provider}' uses an HTTP API. Health check requires sending a chat message \u2014 try one to verify the key works.`, 8000);
        testBtn.textContent = `${provider}: send a chat to verify`;
        return;
      }

      const cli = cliMap[provider];
      if (!cli || !cli.bin) {
        new Notice(`${cli?.label || provider} CLI not found on PATH. Install it or change provider in Settings.`);
        testBtn.textContent = `\u2717 ${cli?.label || provider} not found`;
        return;
      }

      testBtn.disabled = true;
      testBtn.textContent = "Testing...";
      const proc = spawn(cli.bin, ["--version"]);
      const timer = setTimeout(() => { try { proc.kill(); } catch {} }, 5000);
      let out = "";
      proc.stdout.on("data", (d) => { out += d.toString(); });
      proc.on("close", (code) => {
        clearTimeout(timer);
        testBtn.disabled = false;
        testBtn.textContent = code === 0 ? `\u2713 ${cli.label} ${out.trim()}` : `\u2717 ${cli.label} failed`;
      });
      proc.on("error", () => { clearTimeout(timer); testBtn.disabled = false; testBtn.textContent = `\u2717 ${cli.label} not found`; });
    });
  }
}

// ── Search Modal ───────────────────────────────────────────────────

class SearchModal extends Modal {
  constructor(app, vaultPath) {
    super(app);
    this.vaultPath = vaultPath;
    this.query = "";
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.createEl("h2", { text: "Athena Search" });

    new Setting(contentEl)
      .setName("Query")
      .addText((text) => {
        text.setPlaceholder("Search your knowledge base...");
        text.inputEl.style.width = "100%";
        text.onChange((v) => (this.query = v.trim()));
        text.inputEl.addEventListener("keydown", (e) => {
          if (e.key === "Enter") this.runSearch();
        });
        setTimeout(() => text.inputEl.focus(), 50);
      });

    new Setting(contentEl)
      .addButton((btn) => {
        btn.setButtonText("Search").setCta().onClick(() => this.runSearch());
      })
      .addButton((btn) => {
        btn.setButtonText("Close").onClick(() => this.close());
      });
  }

  runSearch() {
    if (!this.query) { new Notice("Enter a search query"); return; }

    new Notice("Searching...");
    const kbPath = path.join(this.vaultPath, "bin", "kb");
    execFile(kbPath, ["search", this.query], {
      cwd: this.vaultPath,
      timeout: 30000,
      env: { ...process.env, PATH: buildEnhancedPath() },
    }, (error, stdout, stderr) => {
      if (error) {
        new Notice("Search failed: " + (stderr || error.message).substring(0, 80));
        return;
      }

      const resultsPath = "wiki/dashboards/Search Results.md";
      const file = this.app.vault.getAbstractFileByPath(resultsPath);
      if (file) {
        this.app.workspace.getLeaf().openFile(file);
      } else {
        new Notice("Results written \u2014 open Search Results.md");
      }
      this.close();
    });
  }

  onClose() {
    this.contentEl.empty();
  }
}

// ── Setup Wizard ───────────────────────────────────────────────────

class AthenaSetupWizard extends Modal {
  constructor(app, vaultName) {
    super(app);
    this.vaultName = vaultName;
  }

  onOpen() {
    const { contentEl } = this;
    contentEl.addClass("athena-setup-wizard");

    contentEl.createEl("h1", { text: "Welcome to Athena" });
    contentEl.createEl("p", { text: "Your Second Brain is ready. Let's set up web clipping so you can capture pages from your browser." });

    contentEl.createEl("h2", { text: "Step 1: Install Web Clipper" });
    const installP = contentEl.createEl("p");
    installP.createEl("span", { text: "Install the " });
    installP.createEl("strong", { text: "Obsidian Web Clipper" });
    installP.createEl("span", { text: " (not the Notion one) for your browser:" });
    const linkP = contentEl.createEl("p");
    linkP.createEl("a", { text: "https://obsidian.md/clipper", href: "https://obsidian.md/clipper" });
    contentEl.createEl("p", { text: "After installing, open the Web Clipper settings in your browser." });

    contentEl.createEl("h2", { text: "Step 2: General settings" });
    contentEl.createEl("p", { text: "In the Web Clipper General settings, find the Vaults section. Add this vault name:" });

    const vaultRow = contentEl.createDiv("athena-setup-row");
    vaultRow.createEl("code", { text: this.vaultName, cls: "athena-setup-value" });
    const copyVaultBtn = vaultRow.createEl("button", { text: "Copy", cls: "athena-setup-copy" });
    copyVaultBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(this.vaultName);
      copyVaultBtn.textContent = "Copied!";
      setTimeout(() => { copyVaultBtn.textContent = "Copy"; }, 1500);
    });

    contentEl.createEl("h2", { text: "Step 3: Set note folder" });
    contentEl.createEl("p", { text: "In the Web Clipper template settings, set the folder to:" });

    const folderRow = contentEl.createDiv("athena-setup-row");
    folderRow.createEl("code", { text: "inbox/Clippings", cls: "athena-setup-value" });
    const copyFolderBtn = folderRow.createEl("button", { text: "Copy", cls: "athena-setup-copy" });
    copyFolderBtn.addEventListener("click", () => {
      navigator.clipboard.writeText("inbox/Clippings");
      copyFolderBtn.textContent = "Copied!";
      setTimeout(() => { copyFolderBtn.textContent = "Copy"; }, 1500);
    });

    contentEl.createEl("h2", { text: "Step 4: Behavior" });
    contentEl.createEl("p", { text: "In the Behavior section, look for these settings and set them as shown. If an option listed here doesn't match what you see, just skip it \u2014 defaults are fine." });

    const table = contentEl.createEl("table", { cls: "athena-setup-table" });
    const header = table.createEl("tr");
    header.createEl("th", { text: "Setting" });
    header.createEl("th", { text: "Recommended" });

    const settings = [
      ["Save clipped note without opening it", "ON"],
      ["Legacy mode", "OFF"],
    ];
    for (const [name, value] of settings) {
      const row = table.createEl("tr");
      row.createEl("td", { text: name });
      row.createEl("td", { text: value, cls: "athena-setup-value" });
    }

    contentEl.createEl("h2", { text: "Step 5: Start clipping" });
    contentEl.createEl("p", { text: "Open any page in your browser and click the Web Clipper icon to save it. Then in Athena, type:" });
    contentEl.createEl("code", { text: "kb add", cls: "athena-setup-value" });
    contentEl.createEl("p", { text: "This processes all pending pages (clippings + queued URLs) into your knowledge base." });

    const doneBtn = contentEl.createEl("button", { text: "Done \u2014 Start using Athena", cls: "athena-setup-done" });
    doneBtn.addEventListener("click", () => this.close());
  }

  onClose() {
    this.contentEl.empty();
  }
}

module.exports = AthenaPlugin;
