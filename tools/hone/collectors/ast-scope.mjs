#!/usr/bin/env node
/**
 * ast-scope.mjs — the AST/scope layer: real function models via the TypeScript compiler API.
 *
 * PORTED from pdpp-cq-sweep/reference-implementation/scripts/code-quality/ast-scope.mjs (validated
 * against the falsification experiment + ~45 gated refactors — treat the analysis as correct).
 * It replaced a regex body-scan router that (a) resolved an anonymous callback's enclosing fn by
 * scanning upward for the first `function`/`const =` token (grabbing keywords like `if`), (b) counted
 * method names and locals as closure captures, (c) couldn't tell a true free variable from a param.
 *
 * For every function-like node it computes: kind, name (real declared name or the CALLBACK ANCHOR —
 * the method the arrow is an argument to, e.g. `.map`, `.on('close')`), callbackKind
 * (iterator/api/transaction/other), start/end lines (1-based, joins with biome's diagnostic anchor),
 * enclosingFn (nearest function-like ancestor — never a keyword), freeVars (identifiers USED in the
 * subtree but DECLARED in an enclosing FUNCTION scope — the true closure-capture signal),
 * freeMutableVars (captures bound let/var AND reassigned — shared mutable cells), moduleRefs
 * (module-scope names referenced — imports/top-level helpers), awaitCount, hasBranch.
 *
 * v1.1 AMENDMENT (schema reality-validation, packet srv-devexp-ingest-normalize-0001 lesson): the
 * reference implementation counted MODULE-SCOPE bindings as captures, misclassifying T1a as T1b.
 * Module-scope helpers stay in scope after a hoist — they don't block extraction. freeVars now
 * counts FUNCTION-scope captures only (params/locals of enclosing functions); module-scope
 * references are reported separately as moduleRefs and never key capture-based classification.
 *
 * Goal is DIRECTIONAL correctness for routing, not a safety proof: we only flag names we can PROVE
 * are enclosing-scope locals, never a method name or local declaration. Read-only — parse + walk.
 *
 * Repo-independence: the only change from the reference is loadTS(repoRoot) — typescript resolves
 * from the TARGET repo's own node_modules first (hone ships no deps), falling back to any
 * typescript resolvable from hone's tree.
 */
import { createRequire } from 'node:module';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

/**
 * Dynamically import the TS compiler API, resolved from the target repo (its own tsc).
 * Throws a clear message if unavailable — a target repo without typescript can't be analyzed.
 */
export async function loadTS(repoRoot) {
  const tried = [];
  if (repoRoot) {
    try {
      const req = createRequire(join(repoRoot, '__hone_resolve__.js'));
      const mod = await import(pathToFileURL(req.resolve('typescript')).href);
      return mod.default || mod;
    } catch (e) { tried.push(`target repo (${repoRoot}): ${e.message.split('\n')[0]}`); }
  }
  try {
    const mod = await import('typescript');
    return mod.default || mod;
  } catch (e) { tried.push(`hone tree: ${e.message.split('\n')[0]}`); }
  throw new Error(
    `typescript compiler API not resolvable — hone needs the TARGET repo's own typescript ` +
    `(install it there, or make it resolvable from hone). Tried: ${tried.join(' | ')}`,
  );
}

// JS/TS globals + ambient names we must never count as a "capture". This is a HEURISTIC allowlist for the
// fallback path; the primary path uses real scope resolution and only falls back to this for names it can't bind.
const GLOBAL_NAMES = new Set([
  'undefined', 'NaN', 'Infinity', 'globalThis', 'console', 'process', 'Buffer', 'require', 'module', 'exports',
  '__dirname', '__filename', 'setTimeout', 'setInterval', 'clearTimeout', 'clearInterval', 'queueMicrotask',
  'Object', 'Array', 'String', 'Number', 'Boolean', 'Symbol', 'BigInt', 'Function', 'Math', 'JSON', 'Date',
  'RegExp', 'Error', 'TypeError', 'RangeError', 'SyntaxError', 'Promise', 'Map', 'Set', 'WeakMap', 'WeakSet',
  'Proxy', 'Reflect', 'ArrayBuffer', 'DataView', 'Uint8Array', 'Int8Array', 'Uint16Array', 'Uint32Array',
  'Float32Array', 'Float64Array', 'TextEncoder', 'TextDecoder', 'URL', 'URLSearchParams', 'AbortController',
  'structuredClone', 'crypto', 'fetch', 'Headers', 'Request', 'Response', 'FormData', 'Blob', 'atob', 'btoa',
  'parseInt', 'parseFloat', 'isNaN', 'isFinite', 'encodeURIComponent', 'decodeURIComponent', 'encodeURI',
  'decodeURI', 'this', 'super', 'arguments', 'null', 'true', 'false', 'void',
]);

/** classify what kind of callback an arrow/function-expression is, from the call it is an argument to. */
const ITERATOR_METHODS = new Set([
  'map', 'forEach', 'filter', 'reduce', 'reduceRight', 'flatMap', 'find', 'findIndex', 'findLast',
  'some', 'every', 'sort', 'flat', 'group',
]);
const TXN_METHODS = new Set(['transaction', 'tx', 'inTransaction', 'withTransaction']);
const API_HINTS = new Set(['on', 'once', 'addEventListener', 'then', 'catch', 'finally', 'subscribe']);

/**
 * Given a function-like node that is being passed as a call argument, return {anchor, callbackKind}.
 * anchor is a human name for the callback site (`.map`, `.on('close')`, `transaction`, or the callee name).
 */
function callbackAnchor(ts, node) {
  const parent = node.parent;
  if (!parent || !ts.isCallExpression(parent)) return null;
  const callee = parent.expression;
  // property access: obj.method(cb)  → method name drives the kind
  if (ts.isPropertyAccessExpression(callee)) {
    const method = callee.name.getText();
    let kind = 'other';
    if (ITERATOR_METHODS.has(method)) kind = 'iterator';
    else if (TXN_METHODS.has(method)) kind = 'transaction';
    else if (API_HINTS.has(method)) kind = 'api';
    // event-emitter style: .on('close', cb) — include the event literal in the anchor for readability
    let anchor = `.${method}`;
    const firstArg = parent.arguments[0];
    if ((method === 'on' || method === 'once' || method === 'addEventListener') && firstArg && ts.isStringLiteralLike(firstArg)) {
      anchor = `.${method}('${firstArg.text}')`;
    }
    // any transaction-ish or event method is api unless iterator/txn already set
    if (kind === 'other' && /^(on|add|register|handle|hook|listen|use|wrap|guard|middleware)/i.test(method)) kind = 'api';
    return { anchor, callbackKind: kind };
  }
  // bare call: fnName(cb) or fnName(x, cb)
  if (ts.isIdentifier(callee)) {
    const name = callee.getText();
    let kind = 'other';
    if (TXN_METHODS.has(name)) kind = 'transaction';
    else if (API_HINTS.has(name) || /^(on|set|register|handle|hook|listen|with|wrap|define|create)/i.test(name)) kind = 'api';
    return { anchor: `${name}()`, callbackKind: kind };
  }
  return { anchor: '(call)', callbackKind: 'other' };
}

/** true if node is a function-like we care about. */
function isFunctionLike(ts, n) {
  return ts.isFunctionDeclaration(n) || ts.isFunctionExpression(n) || ts.isArrowFunction(n) ||
    ts.isMethodDeclaration(n) || ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n) ||
    ts.isConstructorDeclaration(n);
}

/** the declared name of a function-like, or null (anonymous). Resolves `const NAME = () =>` / `NAME: () =>`. */
function declaredName(ts, node) {
  if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isFunctionExpression(node)) && node.name) {
    return node.name.getText();
  }
  if (ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node)) return node.name?.getText() || null;
  if (ts.isConstructorDeclaration(node)) return 'constructor';
  const p = node.parent;
  if (!p) return null;
  // const NAME = () => ... | let NAME = function () {}
  if (ts.isVariableDeclaration(p) && p.name && ts.isIdentifier(p.name)) return p.name.getText();
  // NAME: () => ...  (object/class property or property assignment)
  if ((ts.isPropertyAssignment(p) || ts.isPropertyDeclaration(p)) && p.name) return p.name.getText();
  // export default function () {} → "default"
  if (ts.isExportAssignment(p)) return 'default';
  return null;
}

/** kind label for a function-like node. */
function fnKind(ts, node) {
  if (ts.isFunctionDeclaration(node)) return 'function-decl';
  if (ts.isMethodDeclaration(node)) return 'method';
  if (ts.isGetAccessorDeclaration(node)) return 'getter';
  if (ts.isSetAccessorDeclaration(node)) return 'setter';
  if (ts.isConstructorDeclaration(node)) return 'constructor';
  if (ts.isArrowFunction(node)) return 'arrow';
  if (ts.isFunctionExpression(node)) return 'function-expr';
  return 'unknown';
}

/**
 * Collect the set of names DECLARED directly inside `fnNode`'s own scope (params + `var`/`let`/`const`/`function`/
 * `class` + catch bindings + nested-fn names + import-like). Used to subtract from used-names → free vars.
 * Also returns declaredMutable: names bound with let/var (candidate mutable captures if reassigned in a parent).
 */
function collectLocalDecls(ts, fnNode) {
  const local = new Set();
  const localMutable = new Set();
  // params (incl. destructured)
  const addBindingName = (name, mutable) => {
    if (!name) return;
    if (ts.isIdentifier(name)) { local.add(name.getText()); if (mutable) localMutable.add(name.getText()); return; }
    if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
      for (const el of name.elements) {
        if (ts.isBindingElement(el)) { addBindingName(el.name, mutable); }
      }
    }
  };
  for (const p of fnNode.parameters || []) addBindingName(p.name, true);
  if (ts.isFunctionExpression(fnNode) && fnNode.name) local.add(fnNode.name.getText()); // named fn-expr binds its own name

  const visit = (n) => {
    // do NOT descend into nested function-likes' bodies (their locals are their own scope) — but DO record
    // their declared names (they're declared in THIS scope).
    if (n !== fnNode && isFunctionLike(ts, n)) {
      const nm = declaredName(ts, n);
      // only names declared via decl/var here belong to this scope; arrow-as-arg has no name to add
      if (nm && (ts.isFunctionDeclaration(n))) local.add(nm);
      return; // stop — nested scope
    }
    if (ts.isVariableStatement(n) || ts.isVariableDeclarationList(n)) { ts.forEachChild(n, visit); return; }
    if (ts.isVariableDeclaration(n)) {
      const mutable = !!(n.parent && ts.isVariableDeclarationList(n.parent) && (n.parent.flags & ts.NodeFlags.Const) === 0);
      addBindingName(n.name, mutable);
      if (n.initializer) visit(n.initializer);
      return;
    }
    if (ts.isFunctionDeclaration(n) && n.name) { local.add(n.name.getText()); return; }
    if (ts.isClassDeclaration(n) && n.name) { local.add(n.name.getText()); }
    if (ts.isCatchClause(n) && n.variableDeclaration) addBindingName(n.variableDeclaration.name, true);
    if (ts.isImportClause(n) && n.name) local.add(n.name.getText());
    ts.forEachChild(n, visit);
  };
  if (fnNode.body) ts.forEachChild(fnNode.body, visit);
  return { local, localMutable };
}

/**
 * Collect identifier READS in fnNode's subtree that are candidate free variables:
 * bare identifiers used as values (NOT property names `x.FOO`, NOT object-literal keys, NOT declaration names,
 * NOT type positions). This is the "used names" set we then subtract locals+globals from.
 */
function collectUsedNames(ts, fnNode) {
  const used = new Map(); // name -> count
  const bump = (nm) => used.set(nm, (used.get(nm) || 0) + 1);
  const visit = (n) => {
    // descend into everything INCLUDING nested fns (a capture through a nested fn is still a capture of fnNode's
    // free var relative to fnNode's parents — but we only care about names not local to fnNode; nested locals get
    // subtracted because they aren't in fnNode.local. Simplest correct-enough: count all identifier reads.)
    if (ts.isIdentifier(n)) {
      const p = n.parent;
      // skip property-access member name: obj.FOO  (FOO is not a variable)
      if (p && ts.isPropertyAccessExpression(p) && p.name === n) return;
      // skip qualified-name right side
      if (p && ts.isQualifiedName && ts.isQualifiedName(p) && p.right === n) return;
      // skip object-literal property KEY: { FOO: ... }
      if (p && ts.isPropertyAssignment(p) && p.name === n) return;
      if (p && ts.isShorthandPropertyAssignment(p) && p.name === n) { bump(n.getText()); return; } // {foo} DOES read foo
      // skip binding/declaration NAMES
      if (p && (ts.isParameter(p) || ts.isVariableDeclaration(p) || ts.isBindingElement(p) ||
        ts.isFunctionDeclaration(p) || ts.isClassDeclaration(p) || ts.isPropertyDeclaration(p) ||
        ts.isMethodDeclaration(p) || ts.isImportSpecifier(p) || ts.isImportClause(p)) && p.name === n) return;
      // skip label / type refs
      if (p && (ts.isTypeReferenceNode?.(p) || (ts.isTypeQueryNode && ts.isTypeQueryNode(p)))) return;
      bump(n.getText());
      return;
    }
    ts.forEachChild(n, visit);
  };
  if (fnNode.body) visit(fnNode.body);
  return used;
}

/**
 * Walk up the ancestor chain collecting names declared in ENCLOSING scopes, SPLIT by scope kind
 * (the v1.1 amendment): function scopes (params/locals — hoisting BREAKS these references, so they
 * are true captures) vs the module scope (imports/top-level decls — still in scope after a hoist).
 * Also records which mutable (let/var) bindings are reassigned somewhere in the file. Returns:
 *   { fnScopeDecls: Set<name>, moduleScopeDecls: Set<name>, mutableReassigned: Set<name> }
 */
function collectEnclosingScope(ts, sourceFile, fnNode) {
  const fnScopeDecls = new Set();
  const moduleScopeDecls = new Set();
  const mutableBindings = new Set();     // names bound let/var in some enclosing scope
  // 1) find all ancestor scopes (function bodies + blocks + the source file)
  const scopes = [];
  let cur = fnNode.parent;
  while (cur) {
    if (isFunctionLike(ts, cur) || ts.isSourceFile(cur) || ts.isBlock(cur) || ts.isModuleBlock(cur)) scopes.push(cur);
    cur = cur.parent;
  }
  // a Block is module-scope iff it has no function-like ancestor (top-level { }).
  const isModuleScope = (scope) => {
    if (ts.isSourceFile(scope) || ts.isModuleBlock(scope)) return true;
    if (isFunctionLike(ts, scope)) return false;
    let p = scope.parent;
    while (p) { if (isFunctionLike(ts, p)) return false; p = p.parent; }
    return true;
  };
  // 2) declarations visible in those scopes (params of enclosing fns + top-level decls)
  const addName = (target, name, mutable) => {
    if (!name) return;
    if (ts.isIdentifier(name)) { target.add(name.getText()); if (mutable) mutableBindings.add(name.getText()); return; }
    if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name)) {
      for (const el of name.elements) if (ts.isBindingElement(el)) addName(target, el.name, mutable);
    }
  };
  for (const scope of scopes) {
    const target = isModuleScope(scope) ? moduleScopeDecls : fnScopeDecls;
    if (isFunctionLike(ts, scope)) for (const p of scope.parameters || []) addName(target, p.name, true);
    // scan direct statements of the scope body for declarations (shallow — don't recurse into nested fns)
    const body = ts.isSourceFile(scope) ? scope : (scope.body || scope);
    const scanStmts = (container) => {
      const stmts = container.statements || [];
      for (const st of stmts) {
        if (ts.isVariableStatement(st)) {
          const mutable = (st.declarationList.flags & ts.NodeFlags.Const) === 0;
          for (const d of st.declarationList.declarations) addName(target, d.name, mutable);
        } else if (ts.isFunctionDeclaration(st) && st.name) target.add(st.name.getText());
        else if (ts.isClassDeclaration(st) && st.name) target.add(st.name.getText());
        else if (ts.isImportDeclaration(st) && st.importClause) {
          const ic = st.importClause;
          if (ic.name) target.add(ic.name.getText());
          if (ic.namedBindings) {
            if (ts.isNamespaceImport(ic.namedBindings)) target.add(ic.namedBindings.name.getText());
            else for (const e of ic.namedBindings.elements) target.add(e.name.getText());
          }
        }
      }
    };
    if (body && body.statements) scanStmts(body);
  }
  // 3) which mutable bindings are REASSIGNED anywhere in the file (dangerous shared-cell captures).
  const reassigned = new Set();
  const findReassign = (n) => {
    if (ts.isBinaryExpression(n) && n.operatorToken.kind >= ts.SyntaxKind.FirstAssignment &&
      n.operatorToken.kind <= ts.SyntaxKind.LastAssignment && ts.isIdentifier(n.left)) {
      reassigned.add(n.left.getText());
    }
    if ((ts.isPostfixUnaryExpression(n) || ts.isPrefixUnaryExpression(n)) && ts.isIdentifier(n.operand) &&
      (n.operator === ts.SyntaxKind.PlusPlusToken || n.operator === ts.SyntaxKind.MinusMinusToken)) {
      reassigned.add(n.operand.getText());
    }
    ts.forEachChild(n, findReassign);
  };
  findReassign(sourceFile);
  const mutableReassigned = new Set([...mutableBindings].filter((m) => reassigned.has(m)));
  return { fnScopeDecls, moduleScopeDecls, mutableReassigned };
}

/** nearest function-like ANCESTOR's display name (real parent — never a keyword). */
function enclosingFnName(ts, fnNode) {
  let cur = fnNode.parent;
  while (cur) {
    if (isFunctionLike(ts, cur)) {
      const nm = declaredName(ts, cur);
      if (nm) return nm;
      // anonymous parent → describe it by its own callback anchor if any
      const cb = callbackAnchor(ts, cur);
      return cb ? `<anon ${cb.anchor}>` : '<anon>';
    }
    cur = cur.parent;
  }
  return '<module>';
}

function countAwaits(ts, fnNode) {
  let n = 0;
  const visit = (node) => {
    if (node !== fnNode && isFunctionLike(ts, node)) return; // don't count nested-fn awaits
    if (ts.isAwaitExpression(node)) n++;
    ts.forEachChild(node, visit);
  };
  if (fnNode.body) ts.forEachChild(fnNode.body, visit);
  return n;
}
function hasBranch(ts, fnNode) {
  let found = false;
  const visit = (node) => {
    if (found) return;
    if (node !== fnNode && isFunctionLike(ts, node)) return;
    if (ts.isIfStatement(node) || ts.isSwitchStatement(node) || ts.isConditionalExpression(node) ||
      ts.isForStatement(node) || ts.isForOfStatement(node) || ts.isForInStatement(node) ||
      ts.isWhileStatement(node) || ts.isDoStatement(node) || ts.isCatchClause(node) ||
      (ts.isBinaryExpression(node) && (node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken ||
        node.operatorToken.kind === ts.SyntaxKind.BarBarToken ||
        node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken))) { found = true; return; }
    ts.forEachChild(node, visit);
  };
  if (fnNode.body) ts.forEachChild(fnNode.body, visit);
  return found;
}

/**
 * Parse one file and return a model of every function-like node with real scope-resolved free vars.
 * @returns {{ functions: FnModel[], source: string }}
 */
export function buildFileModel(absFile, ts, sourceText) {
  const src = sourceText;
  const scriptKind = absFile.endsWith('.ts') ? ts.ScriptKind.TS : ts.ScriptKind.JS;
  const sf = ts.createSourceFile(absFile, src, ts.ScriptTarget.Latest, /*setParentNodes*/ true, scriptKind);
  const functions = [];
  const visit = (node) => {
    if (isFunctionLike(ts, node)) {
      const pos = sf.getLineAndCharacterOfPosition(node.getStart(sf));
      const endPos = sf.getLineAndCharacterOfPosition(node.getEnd());
      const name = declaredName(ts, node);
      let anchor = name, callbackKind = 'other';
      if (!name) {
        const cb = callbackAnchor(ts, node);
        if (cb) { anchor = cb.anchor; callbackKind = cb.callbackKind; }
        else anchor = '<anon>';
      } else {
        // a NAMED fn can still be a callback (e.g. arr.map(function foo(){})) — detect for completeness
        const cb = callbackAnchor(ts, node);
        if (cb) callbackKind = cb.callbackKind;
      }
      // free vars = used names − local decls − globals, split by where the binding lives (v1.1):
      //   enclosing FUNCTION scope → a true capture (hoisting breaks it) → drives classification;
      //   module scope             → moduleRefs (still in scope after a hoist; never blocks T1a).
      // Note: a module-scope mutable cell read by a callback is unchanged by hoisting — the hoisted
      // fn still closes over module scope — so it deliberately does NOT force T2-capturing-mutable.
      // Names we can't bind at all are treated as NON-captures (directional: we only flag names we
      // can PROVE are enclosing-scope locals, avoiding the regex era's false positives).
      const { local } = collectLocalDecls(ts, node);
      const used = collectUsedNames(ts, node);
      const { fnScopeDecls, moduleScopeDecls, mutableReassigned } = collectEnclosingScope(ts, sf, node);
      const freeVars = [];
      const freeMutable = [];
      const moduleRefs = [];
      for (const [nm] of used) {
        if (local.has(nm) || GLOBAL_NAMES.has(nm)) continue;
        if (fnScopeDecls.has(nm)) {   // inner scope wins on shadowing — counting as capture = up-tier (escalation bias)
          freeVars.push(nm);
          if (mutableReassigned.has(nm)) freeMutable.push(nm);
        } else if (moduleScopeDecls.has(nm)) moduleRefs.push(nm);
      }
      functions.push({
        name: anchor,
        declaredName: name,
        kind: fnKind(ts, node),
        callbackKind: name && callbackKind === 'other' ? null : (name ? callbackKind : callbackKind),
        line: pos.line + 1,
        col: pos.character + 1,
        endLine: endPos.line + 1,
        enclosingFn: enclosingFnName(ts, node),
        freeVars: [...new Set(freeVars)],
        freeMutableVars: [...new Set(freeMutable)],
        moduleRefs: [...new Set(moduleRefs)],
        awaitCount: countAwaits(ts, node),
        hasBranch: hasBranch(ts, node),
        isCallback: !name || (callbackAnchor(ts, node) != null),
      });
    }
    ts.forEachChild(node, visit);
  };
  ts.forEachChild(sf, visit);
  return { functions, source: src };
}

/**
 * Join biome diagnostics (which give file+line+cc for the flagged function anchor) to the AST function models.
 * biome anchors the diagnostic at the function's START token; TS gives the same start line for the node. We match
 * by (file, nearest AST function whose start line === diag line, else the smallest function spanning the line).
 * @returns the matched FnModel, or null if no function spans the line.
 */
export function matchDiag(models, diagLine) {
  // 1) exact start-line match (the common case — biome & TS agree on the def line)
  const exact = models.filter((m) => m.line === diagLine);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) {
    // multiple fns start on the same line (chained .map().filter()) → smallest span (innermost)
    return exact.sort((a, b) => (a.endLine - a.line) - (b.endLine - b.line))[0];
  }
  // 2) no exact: the smallest function that SPANS the diag line (biome sometimes anchors a line into the body)
  const spanning = models.filter((m) => m.line <= diagLine && m.endLine >= diagLine);
  if (!spanning.length) return null;
  return spanning.sort((a, b) => (a.endLine - a.line) - (b.endLine - b.line))[0];
}
