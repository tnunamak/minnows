// yaml.mjs — a minimal YAML subset codec, stdlib-only (hone ships dep-free by policy).
//
// SUPPORTED SUBSET (everything hone reads/writes — profiles + candidate packets):
//   - block maps (2-space indent), block lists of scalars, `- key: value` list-of-maps
//     (continuation keys must sit exactly 2 spaces past the dash),
//   - inline arrays [a, b, "c"], empty {} / [],
//   - scalars: null/~, true/false, int/float, 'single'/"double"-quoted (JSON escapes), bare strings,
//   - comments (# at start or after whitespace, quote-aware).
// NOT supported (crash loudly, by design): anchors/aliases, multi-line block scalars (| >),
// inline maps, multi-doc streams. If you need those in quality/hone.yaml, simplify the file.
//
// stringifyYaml() only ever emits constructs parseYaml() reads; hone round-trip-verifies every
// packet it writes (emit → parse → deep-equal) so a codec bug crashes the plan instead of
// corrupting the packet stream.

export function parseYaml(text) {
  const lines = [];
  const raws = String(text).split(/\r?\n/);
  for (let i = 0; i < raws.length; i++) {
    let noComment = stripComment(raws[i]);
    if (!noComment.trim() || noComment.trim() === '---') continue;
    // inline (flow) arrays may span lines: join until brackets balance outside quotes.
    while (bracketBalance(noComment) > 0 && i + 1 < raws.length) {
      noComment += ' ' + stripComment(raws[++i]).trim();
    }
    lines.push({ indent: noComment.match(/^ */)[0].length, text: noComment.trim() });
  }
  if (!lines.length) return null;
  let pos = 0;

  function parseNode(indent) {
    const l = lines[pos];
    if (!l || l.indent < indent) return null;
    if (l.text === '-' || l.text.startsWith('- ')) return parseList(l.indent);
    return parseMap(l.indent);
  }

  function parseMap(indent) {
    const obj = {};
    while (pos < lines.length) {
      const l = lines[pos];
      if (l.indent !== indent || l.text === '-' || l.text.startsWith('- ')) break;
      const kv = splitKey(l.text);
      if (!kv) throw new Error(`yaml: expected 'key: value' at line: ${JSON.stringify(l.text)}`);
      pos++;
      const key = unquote(kv[0]);
      if (kv[1] != null) obj[key] = scalarOrInline(kv[1]);
      else {
        const next = lines[pos];
        if (next && next.indent > indent) obj[key] = parseNode(next.indent);
        else if (next && next.indent === indent && (next.text === '-' || next.text.startsWith('- '))) obj[key] = parseList(indent);
        else obj[key] = null;
      }
    }
    return obj;
  }

  function parseList(indent) {
    const arr = [];
    while (pos < lines.length) {
      const l = lines[pos];
      if (l.indent !== indent || !(l.text === '-' || l.text.startsWith('- '))) break;
      const item = l.text === '-' ? '' : l.text.slice(2).trim();
      pos++;
      if (!item) {
        const next = lines[pos];
        arr.push(next && next.indent > indent ? parseNode(next.indent) : null);
      } else if (splitKey(item)) {
        // `- key: value` — a map whose first entry rides the dash; continuation keys at indent+2.
        lines.splice(pos, 0, { indent: indent + 2, text: item });
        arr.push(parseMap(indent + 2));
      } else arr.push(scalarOrInline(item));
    }
    return arr;
  }

  const root = parseNode(lines[0].indent);
  if (pos < lines.length) {
    throw new Error(`yaml: unparsed trailing content from line: ${JSON.stringify(lines[pos].text)} (inconsistent indentation?)`);
  }
  return root;
}

/** split `key: rest` at the first unquoted colon followed by space/EOL → [key, rest|null], or null. */
function splitKey(t) {
  let inS = false, inD = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (inD && c === '\\') { i++; continue; }
    if (c === "'" && !inD) inS = !inS;
    else if (c === '"' && !inS) inD = !inD;
    else if (c === ':' && !inS && !inD && (i === t.length - 1 || t[i + 1] === ' ')) {
      const rest = t.slice(i + 1).trim();
      return [t.slice(0, i).trim(), rest || null];
    }
  }
  return null;
}

function unquote(s) {
  if (s.startsWith('"')) return JSON.parse(s);
  if (s.startsWith("'") && s.endsWith("'") && s.length >= 2) return s.slice(1, -1).replace(/''/g, "'");
  return s;
}

function scalarOrInline(s) {
  if (s.startsWith('[')) return parseInlineArray(s);
  if (s === '{}') return {};
  if (s.startsWith('{')) throw new Error(`yaml subset: inline maps not supported: ${JSON.stringify(s)}`);
  return scalar(s);
}

function parseInlineArray(s) {
  if (!s.endsWith(']')) throw new Error(`yaml: unterminated inline array: ${JSON.stringify(s)}`);
  const inner = s.slice(1, -1).trim();
  if (!inner) return [];
  const parts = [];
  let cur = '', inS = false, inD = false, depth = 0;
  for (let i = 0; i < inner.length; i++) {
    const c = inner[i];
    if (inD && c === '\\') { cur += c + (inner[i + 1] || ''); i++; continue; }
    if (c === "'" && !inD) inS = !inS;
    else if (c === '"' && !inS) inD = !inD;
    if (!inS && !inD) {
      if (c === ',' && depth === 0) { parts.push(cur); cur = ''; continue; }
      if (c === '[') depth++;
      if (c === ']') depth--;
    }
    cur += c;
  }
  parts.push(cur);
  return parts.map((p) => scalarOrInline(p.trim()));
}

function scalar(s) {
  if (s === '' || s === '~' || s === 'null') return null;
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+$/.test(s)) return Number(s);
  if (/^-?(\d+\.\d*|\.\d+)([eE][+-]?\d+)?$/.test(s)) return Number(s);
  if (s.startsWith('"')) return JSON.parse(s);
  if (s.startsWith("'") && s.endsWith("'") && s.length >= 2) return s.slice(1, -1).replace(/''/g, "'");
  return s;
}

/** net [ ] depth of a line, ignoring brackets inside quotes. */
function bracketBalance(line) {
  let inS = false, inD = false, depth = 0;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inD && c === '\\') { i++; continue; }
    if (c === "'" && !inD) inS = !inS;
    else if (c === '"' && !inS) inD = !inD;
    else if (!inS && !inD) { if (c === '[') depth++; else if (c === ']') depth--; }
  }
  return depth;
}

function stripComment(line) {
  let inS = false, inD = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inD && c === '\\') { i++; continue; }
    if (c === "'" && !inD) inS = !inS;
    else if (c === '"' && !inS) inD = !inD;
    else if (c === '#' && !inS && !inD && (i === 0 || line[i - 1] === ' ' || line[i - 1] === '\t')) return line.slice(0, i);
  }
  return line;
}

// ---------------------------------------------------------------- emitter

export function stringifyYaml(value) {
  if (!isPlainObject(value)) throw new Error('stringifyYaml: root must be a map');
  return emitMap(value, 0).join('\n') + '\n';
}

function isPlainObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }

function emitMap(obj, indent) {
  const pad = ' '.repeat(indent);
  const out = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined) continue;
    const key = /^[A-Za-z0-9_][A-Za-z0-9_.\/-]*$/.test(k) ? k : JSON.stringify(k);
    if (isPlainObject(v)) {
      if (!Object.keys(v).length) out.push(`${pad}${key}: {}`);
      else { out.push(`${pad}${key}:`); out.push(...emitMap(v, indent + 2)); }
    } else if (Array.isArray(v)) {
      if (!v.length) out.push(`${pad}${key}: []`);
      else if (v.every((x) => !isPlainObject(x) && !Array.isArray(x))) {
        const inline = `[${v.map(fmtScalar).join(', ')}]`;
        if (indent + key.length + 2 + inline.length <= 110) out.push(`${pad}${key}: ${inline}`);
        else { out.push(`${pad}${key}:`); for (const x of v) out.push(`${pad}  - ${fmtScalar(x)}`); }
      } else {
        out.push(`${pad}${key}:`);
        for (const x of v) {
          if (isPlainObject(x)) {
            const sub = emitMap(x, indent + 4);
            out.push(`${pad}  - ${sub[0].trim()}`, ...sub.slice(1));
          } else if (Array.isArray(x)) throw new Error('stringifyYaml: nested arrays in block lists not supported');
          else out.push(`${pad}  - ${fmtScalar(x)}`);
        }
      }
    } else out.push(`${pad}${key}: ${fmtScalar(v)}`);
  }
  return out;
}

function fmtScalar(v) {
  if (v === null) return 'null';
  if (typeof v === 'boolean') return String(v);
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) throw new Error(`stringifyYaml: non-finite number ${v}`);
    return String(v);
  }
  const s = String(v);
  if (s === '' || /^\s|\s$/.test(s) || /[:#\[\]{},&*!|>%@`"'\\]/.test(s) || /^[-?]/.test(s) ||
    /^(true|false|null|~|yes|no|on|off)$/i.test(s) || /^-?[\d.]/.test(s) || s.includes('\n')) {
    return JSON.stringify(s);
  }
  return s;
}
