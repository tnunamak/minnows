// slopgate — markdown-aware stripping so slop rules only see real authored prose.
//
// Order matters: fenced/indented code and inline code must go before anything
// else, since a code span can contain characters (backticks inside a table
// cell, a URL inside a quote) that would otherwise confuse the later strips.

export function stripNonProse(text) {
  let out = text;

  // HTML: script/style blocks are never prose — blank wholesale (their
  // content can contain arbitrary code/JSON that isn't authored copy).
  out = out.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, (m) => blank(m));
  out = out.replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, (m) => blank(m));

  // HTML comments.
  out = out.replace(/<!--[\s\S]*?-->/g, (m) => blank(m));

  // HTML tags: blank the tag markup itself (names + attributes), keep the
  // visible inner text between tags — that text is real authored copy and
  // still needs checking (e.g. a hero heading's actual sentence). Attribute
  // VALUES (href, aria-label, svg path data) are markup payload, not prose,
  // so the whole tag — not just the brackets — is blanked.
  out = out.replace(/<[^>]+>/g, (m) => blank(m));

  // Common HTML entities in the now-tag-free text: decode to their literal
  // character so downstream rules see real words, not markup escapes
  // (&quot;X&quot; would otherwise read as the literal string "quot" and
  // pollute cadence's sentence-start extraction). Same-length replacement
  // isn't possible here without shifting offsets, so pad with a trailing
  // space per entity removed — close enough for line-number reporting.
  out = out.replace(/&quot;/g, ' " ').replace(/&#x27;|&#39;|&apos;/g, " ' ")
    .replace(/&amp;/g, ' & ').replace(/&lt;/g, ' < ').replace(/&gt;/g, ' > ')
    .replace(/&nbsp;/g, '  ');

  // Fenced code blocks (``` or ~~~, any info string).
  out = out.replace(/^([ \t]*)(`{3,}|~{3,}).*$[\s\S]*?^\1\2[ \t]*$/gm, (m) => blank(m));

  // Indented code blocks (4-space/tab indent, CommonMark-style). Only strip
  // runs that aren't inside a list (heuristic: preceded by a blank line).
  out = out.replace(/^(?: {4}|\t).*$/gm, (m) => blank(m));

  // Inline code spans.
  out = out.replace(/`[^`\n]+`/g, (m) => blank(m));

  // Blockquotes (`> ...`), line by line.
  out = out.replace(/^[ \t]*>.*$/gm, (m) => blank(m));

  // Markdown tables: a header separator row (|---|---|) marks a table: blank
  // that row and any contiguous `|`-containing rows around it.
  const lines = out.split('\n');
  const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const isTableRow = (l) => l.includes('|') && l.trim().length > 0;
  for (let i = 0; i < lines.length; i++) {
    if (isTableSep(lines[i]) && i > 0 && isTableRow(lines[i - 1])) {
      lines[i - 1] = blank(lines[i - 1]);
      lines[i] = blank(lines[i]);
      let j = i + 1;
      while (j < lines.length && isTableRow(lines[j])) {
        lines[j] = blank(lines[j]);
        j++;
      }
      i = j - 1;
    }
  }
  out = lines.join('\n');

  // Quoted vocabulary catalogs: 3+ short quoted terms joined by commas
  // ("delve", "tapestry", "underscore", "leverage", ...). This is a citation
  // LISTING words as examples (e.g. research about AI writing tells quoting
  // a published word list), not those words being used earnestly in prose —
  // the same distinction the mandate draws for "quoted material" generally.
  // Narrow by design: requires >=3 repetitions of the quote-comma shape so an
  // ordinary sentence with one quoted term ('the API returns "not found"')
  // is untouched.
  out = out.replace(
    /(?:["'][^"'\n]{1,30}["'](?:,\s*(?:and\s+)?)){2,}["'][^"'\n]{1,30}["']/g,
    (m) => blank(m)
  );

  // Longer double-quoted spans (a whole attributed sentence or fragment,
  // e.g. citing a source's exact wording: "Additionally, boasts, bolstered,
  // crucial, delve, ..."). A quotation is source material, not the file's
  // own authored prose — blank it the same way a blockquote is blanked.
  // Allows the quote to wrap onto continuation lines (soft-wrapped prose,
  // common in markdown), but not across a blank line — a blank line marks a
  // new paragraph, so a quote mark before it is almost certainly unclosed
  // rather than genuinely spanning paragraphs.
  out = out.replace(/"[^"]{15,400}"/g, (m) => (m.includes('\n\n') ? m : blank(m)));

  // URLs (bare or markdown-linked) — keep link TEXT, blank the URL itself.
  out = out.replace(/\bhttps?:\/\/[^\s)>\]]+/g, (m) => blank(m));

  // File-path-shaped tokens: at least one `/` with no spaces, optionally
  // with an extension — conservative, only strips clear path shapes.
  out = out.replace(/(?:[\w.-]+\/)+[\w.-]+(?:\.\w+)?/g, (m) => blank(m));

  // YAML frontmatter block.
  out = out.replace(/^---\n[\s\S]*?\n---\n/, (m) => blank(m));

  return out;
}

// Replace with same-length whitespace (preserving newlines) so line/column
// numbers reported by later checks stay accurate against the ORIGINAL text.
function blank(s) {
  return s.replace(/[^\n]/g, ' ');
}
