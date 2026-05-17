#!/usr/bin/env node
// html2md.mjs — Convert HTML to readable markdown. Zero dependencies.
//
// Usage:
//   echo "<html>..." | node html2md.mjs
//   node html2md.mjs < file.html
//   node html2md.mjs --url https://example.com
//   node html2md.mjs --url https://example.com --title-only

import { stdin, stdout, argv } from 'node:process';

const args = argv.slice(2);
const urlFlag = args.indexOf('--url');
const titleOnly = args.includes('--title-only');
const url = urlFlag !== -1 ? args[urlFlag + 1] : null;

async function getHtml() {
  if (url) {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; KBCapture/1.0)' },
      redirect: 'follow',
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    return await res.text();
  }
  // Read from stdin
  const chunks = [];
  for await (const chunk of stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf-8');
}

function extractTitle(html) {
  const m = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return m ? m[1].replace(/\s+/g, ' ').trim() : '';
}

// Resolve a possibly-relative URL against the page's base URL. Falls back to
// returning the src unchanged if baseUrl is absent or resolution fails (e.g.
// data: URIs, fragment-only links). Absolute URLs pass through untouched.
function resolveUrl(src, baseUrl) {
  if (!src) return src;
  if (/^(https?:|data:|mailto:|tel:|#)/i.test(src)) return src;
  if (src.startsWith('//')) return 'https:' + src;
  if (!baseUrl) return src;
  try {
    return new URL(src, baseUrl).href;
  } catch {
    return src;
  }
}

function htmlToMarkdown(html, baseUrl) {
  let text = html;

  // Remove everything before <body> or <article> or <main>
  const bodyMatch = text.match(/<(?:article|main)[^>]*>([\s\S]*?)<\/(?:article|main)>/i);
  if (bodyMatch) {
    text = bodyMatch[1];
  } else {
    const bodyTag = text.match(/<body[^>]*>([\s\S]*?)<\/body>/i);
    if (bodyTag) text = bodyTag[1];
  }

  // Remove script, style, nav, header, footer, aside tags and content
  text = text.replace(/<(script|style|nav|header|footer|aside|noscript|svg|iframe)[^>]*>[\s\S]*?<\/\1>/gi, '');

  // Remove HTML comments
  text = text.replace(/<!--[\s\S]*?-->/g, '');

  // Convert headings
  text = text.replace(/<h1[^>]*>([\s\S]*?)<\/h1>/gi, (_, c) => `\n# ${stripTags(c).trim()}\n`);
  text = text.replace(/<h2[^>]*>([\s\S]*?)<\/h2>/gi, (_, c) => `\n## ${stripTags(c).trim()}\n`);
  text = text.replace(/<h3[^>]*>([\s\S]*?)<\/h3>/gi, (_, c) => `\n### ${stripTags(c).trim()}\n`);
  text = text.replace(/<h4[^>]*>([\s\S]*?)<\/h4>/gi, (_, c) => `\n#### ${stripTags(c).trim()}\n`);
  text = text.replace(/<h5[^>]*>([\s\S]*?)<\/h5>/gi, (_, c) => `\n##### ${stripTags(c).trim()}\n`);
  text = text.replace(/<h6[^>]*>([\s\S]*?)<\/h6>/gi, (_, c) => `\n###### ${stripTags(c).trim()}\n`);

  // Convert links — resolve relative href against the page's base URL so
  // links remain navigable once the markdown is viewed in Obsidian or diff'd
  // from a different location.
  text = text.replace(/<a[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, (_, href, c) => {
    const label = stripTags(c).trim();
    if (!label) return '';
    if (href.startsWith('#') || href.startsWith('javascript:')) return label;
    return `[${label}](${resolveUrl(href, baseUrl)})`;
  });

  // Lazy-load handling: many sites use `<img src="data:...">` as an LQIP
  // placeholder and put the real URL in `data-src`, `data-lazy-src`, or
  // `data-original`. Some sites also wrap a `<noscript><img src="real.png">`
  // fallback. Without this preprocessing, html2md would capture the 24x15
  // placeholder and lose the real image (the wiz.io article hit this).
  //
  // Strategy:
  //   1. Promote noscript-fallback <img> to replace the preceding LQIP.
  //   2. For any <img> still pointing at a data: URI, prefer data-src /
  //      data-lazy-src / data-original / data-srcset attributes if present.
  text = text.replace(
    /<img[^>]*src="data:[^"]*"[^>]*>\s*<noscript[^>]*>\s*(<img[^>]*>)\s*<\/noscript>/gi,
    (_, fallback) => fallback
  );
  text = text.replace(
    /<noscript[^>]*>\s*(<img[^>]*src="https?:[^"]+"[^>]*>)\s*<\/noscript>/gi,
    (_, fallback) => fallback
  );
  text = text.replace(/<img[^>]*\bsrc="data:[^"]*"[^>]*>/gi, (m) => {
    const dataAttr = m.match(/\b(?:data-src|data-lazy-src|data-original)="([^"]+)"/i);
    if (dataAttr && /^https?:/i.test(dataAttr[1])) {
      return m.replace(/\bsrc="data:[^"]*"/i, `src="${dataAttr[1]}"`);
    }
    const srcset = m.match(/\bdata-srcset="([^"]+)"/i);
    if (srcset) {
      const first = srcset[1].split(',')[0].trim().split(/\s+/)[0];
      if (/^https?:/i.test(first)) {
        return m.replace(/\bsrc="data:[^"]*"/i, `src="${first}"`);
      }
    }
    return m;
  });

  // Convert images — same resolution rule. Relative srcs like "Cover.png"
  // would otherwise break when viewed outside the original page's directory.
  text = text.replace(/<img[^>]*alt="([^"]*)"[^>]*src="([^"]*)"[^>]*\/?>/gi,
    (_, alt, src) => `![${alt}](${resolveUrl(src, baseUrl)})`);
  text = text.replace(/<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*\/?>/gi,
    (_, src, alt) => `![${alt}](${resolveUrl(src, baseUrl)})`);
  text = text.replace(/<img[^>]*src="([^"]*)"[^>]*\/?>/gi,
    (_, src) => `![](${resolveUrl(src, baseUrl)})`);

  // Convert code blocks
  text = text.replace(/<pre[^>]*><code[^>]*>([\s\S]*?)<\/code><\/pre>/gi, (_, c) => `\n\`\`\`\n${decodeEntities(c).trim()}\n\`\`\`\n`);
  text = text.replace(/<pre[^>]*>([\s\S]*?)<\/pre>/gi, (_, c) => `\n\`\`\`\n${decodeEntities(stripTags(c)).trim()}\n\`\`\`\n`);

  // Convert inline code
  text = text.replace(/<code[^>]*>([\s\S]*?)<\/code>/gi, (_, c) => `\`${decodeEntities(stripTags(c))}\``);

  // Convert bold and italic
  text = text.replace(/<(strong|b)[^>]*>([\s\S]*?)<\/\1>/gi, (_, _t, c) => `**${stripTags(c).trim()}**`);
  text = text.replace(/<(em|i)[^>]*>([\s\S]*?)<\/\1>/gi, (_, _t, c) => `*${stripTags(c).trim()}*`);

  // Convert blockquotes
  text = text.replace(/<blockquote[^>]*>([\s\S]*?)<\/blockquote>/gi, (_, c) => {
    return stripTags(c).trim().split('\n').map(l => `> ${l.trim()}`).join('\n') + '\n';
  });

  // Convert list items
  text = text.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, c) => `- ${stripTags(c).trim()}\n`);

  // Convert paragraphs and divs to double newlines
  text = text.replace(/<\/p>/gi, '\n\n');
  text = text.replace(/<br\s*\/?>/gi, '\n');
  text = text.replace(/<\/div>/gi, '\n');

  // Convert horizontal rules
  text = text.replace(/<hr[^>]*\/?>/gi, '\n---\n');

  // Strip all remaining HTML tags
  text = stripTags(text);

  // Decode HTML entities
  text = decodeEntities(text);

  // Clean up whitespace
  text = text.replace(/\n{3,}/g, '\n\n');          // max 2 consecutive newlines
  text = text.replace(/[ \t]+$/gm, '');             // trailing whitespace per line
  text = text.replace(/^[ \t]+/gm, (m) => m.replace(/\t/g, '  ')); // tabs to spaces
  text = text.trim();

  return text;
}

function stripTags(html) {
  return html.replace(/<[^>]+>/g, '');
}

function decodeEntities(text) {
  return text
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCharCode(parseInt(n, 16)));
}

try {
  const html = await getHtml();
  if (titleOnly) {
    stdout.write(extractTitle(html) + '\n');
  } else {
    const title = extractTitle(html);
    const md = htmlToMarkdown(html, url);
    if (title) stdout.write(`# ${title}\n\n`);
    stdout.write(md + '\n');
  }
} catch (err) {
  process.stderr.write(`error: ${err.message}\n`);
  process.exit(1);
}
