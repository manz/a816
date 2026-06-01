# Design: unify scopes, modules, and alloc bodies

**Status:** draft / RFC
**Author:** manz (+ assistant)
**Date:** 2026-06-01

## Why

a816 has three overlapping concepts that each carve a namespace and/or a
boundary, and they blur into each other:

1. **`.scope NAME { ... }`** - a lexical namespace in one translation unit.
   Members export as `NAME.member`; non-underscore = public.
2. **`.alloc ... { ... }` body** - a *placement* unit (allocator picks the
   address) that *also* silently opened a namespace (`AllocBodyScope`).
3. **module** (`.import "name"`) - a namespace **plus** separate compilation
   (`.o`) **plus** link-time placement.

A module is, essentially, a `.scope` that also got a compilation boundary and a
`.o`. But the three are implemented as separate mechanisms with subtly
different rules, and every recent symbol-resolution bug lived in the seams:

- **`.extern` inside an `.alloc`/`.scope` body doesn't resolve at the use
  site** - externs register only in their declaring scope, and an alloc body
  opens its own. (Forced ff4 to hoist every extern to root.)
- **Underscore labels mangled per-alloc** (#89) broke labels that were
  *shared across allocs in one module* - they were never really private; the
  mangle exposed mislabeled `_names`.
- **`.import` does not surface the imported module's public labels by name** -
  you still need a manual `.extern` per symbol.
- **Constant export is inconsistent** - a standalone `.scope` constant exports
  GLOBAL DATA, but an import-seeded constant gets skipped, so an
  `.extern`-of-a-constant has nothing to resolve against.

Net effect: neither `.scope` nor `.import` cleanly gives "use another unit's
public symbols by name," so projects (ff4) leaned on **wild `.extern`** - a
manual, per-symbol, scope-fragile workaround. The fix isn't three more
patches; it's untangling the model.

## The core idea

**A module is a compiled scope.** Unify the *namespace* half; keep the
*compile/place* half as the only real difference.

```
.scope NAME    = named scope (namespace)
.alloc NAME    = named scope + placement     -> members are NAME.member
.alloc (anon)  = placement only              -> members bubble to enclosing scope
module         = named scope + placement + compilation (.o)
```

A **named** alloc is a named scope that's also placed: its members live under
`NAME.member`, exactly like `.scope NAME`. An **anonymous** alloc
(`.alloc in pool { ... }`) is placement only - no namespace - and its public
members bubble to the enclosing scope. They are all built on **the same scope
kind**, differing only by what else they do (place bytes, produce a `.o`),
never by how the namespace behaves. No special `AllocBodyScope` with opt-out
rules.

(Two separate concerns live below and should not be conflated: **(1)** the
scope-mechanics *bug* - a scope must resolve declarations made inside it; **(2)**
the *model* - named allocs namespace under their name. (1) is a fix; (2) is a
design choice the project wants.)

Concretely:

- **One scope kind, one set of rules.** Member naming (`NAME.member`),
  public/private (underscore), export classification, *and extern resolution*
  are identical whether you wrote `.scope`, `.alloc`, or are inside a module.
  One `_export_name`, one `is_external_symbol` walk, one privacy rule.
- **A scope resolves externs declared inside it.** Today an `.extern` declared
  in ANY scope body (`.scope` *or* `.alloc`) fails to resolve even at a use
  site in the same scope - only root externs reach inward. That's the core
  bug, and it's uniform: fix the scope's extern visibility once and `.scope`,
  `.alloc`, and module bodies are all fixed together.
- **`.import "mod"` brings `mod`'s public scope into the namespace.** After
  import, `mod.foo` (and `mod.CONST`) are usable directly - labels *and*
  constants - with no per-symbol `.extern`. Import = "open this compiled
  scope," not "link an opaque blob and re-declare everything by hand."
- **`.extern` shrinks to its real job:** symbols a816 can't see at all -
  build-script-injected constants, third-party `.o` drops, hand-placed
  vanilla-ROM addresses. Not "I imported this module but still have to name
  each symbol," and not a workaround for scopes that won't resolve their own
  declarations.

## Concern 1: the scope-mechanics bug (a fix)

A scope must resolve declarations made inside it. Today it doesn't:

- **A scope doesn't see its own `.extern`.** An `.extern` declared inside a
  `.scope` *or* an `.alloc` body fails to resolve even at a use site in the
  same body - only root externs reach inward. Verified for both. This is the
  single bug that forced ff4 to hoist every extern to root.
- **Constant export is inconsistent.** A standalone-scope constant exports
  GLOBAL DATA, but an import-seeded one is skipped on re-export, so an
  `.extern`-of-a-constant has nothing to resolve against.

Fix: one scope kind, one resolution rule. `is_external_symbol` / `value_for`
resolve a scope's own declarations and walk to the enclosing scope uniformly,
whether the scope came from `.scope`, `.alloc`, or a module body. Collapse
`AllocBodyScope` into the ordinary scope so there is nothing to special-case.

This is purely a correctness fix - independent of the model below.

## Concern 2: the namespace model (a design choice)

### Named allocs are namespaces
`.alloc render in pool { foo: ... CONST = 1 }` publishes `render.foo`,
`render.CONST` - a named alloc *is* a named scope that also places. Anonymous
`.alloc in pool { ... }` stays placement-only; its public members bubble to the
enclosing scope (asset-blob style, no useful name to hang them on). This is the
project's preference: explicit `render.foo` over bare bubbling means fewer
bare-name collisions and a clear public surface per alloc.

### Import surfaces the scope
`.import "render"` makes `render.<public>` resolvable in the importer with no
`.extern` - labels *and* constants. Private (`_`-prefixed) members stay
invisible across the boundary. This is the headline ergonomic win and what
kills wild externs.

### Constants are first-class exports
A module's public constants (`NAME = expr`, `NAME := expr`) export as GLOBAL
DATA and are surfaced by import like labels. `.extern`-of-a-constant becomes
unnecessary (import already brought it); a compile-time value need not become a
link symbol.

### Privacy, consistent
`_`-prefixed = private to **its scope**, resolved per-*object* at link (#88
already does the cross-module half). Drop #89's per-*alloc* mangle - it
over-fragmented privacy and broke labels shared between sibling allocs in one
module (which simply weren't private). Privacy is scope-private; an alloc is one
scope.

### Extern, narrowed
With the above, `.extern` is only for symbols with no owning a816 unit -
build-script-injected constants, third-party `.o` drops, hand-placed
vanilla-ROM addresses. `fluff` warns on a redundant `.extern` for an imported
module's public symbol, and on `.extern` declared inside a scope body when it
was meant for root.

## Prior art: Python modules (and where we diverge)

The target feel is Python's module/namespace model - it's a proven cure for
exactly the "manually re-declare every cross-unit symbol" problem.

Copy wholesale (the namespace half):

| Python | a816 |
|---|---|
| `import mod` → `mod.foo` | `.import "mod"` → `mod.foo` (no per-symbol `.extern`) |
| `from mod import foo` | selective bring-bare (`foo = mod.foo`, or a `.from`) |
| `from mod import *` | bubble publics (a `.use` / anon-alloc style) |
| `_name` private convention | `_name` private (already) |
| `__all__` explicit public list | explicit export vs the underscore convention |
| `sys.modules` run-once cache | the `.o` build cache (already) |

**The one divergence - do NOT copy it:** Python *fuses* "import a name" with
"run the module's code" (import executes the module, once). a816 cannot fuse
them, because **N importers share ONE placed copy** of a module's code. So two
things Python treats as a single event stay separate here:

- **visibility** - many importers see `mod.foo`; namespace-only, cheap.
- **placement / emission** - `mod`'s bytes land in the ROM *once*, at link (the
  paired-import dedup already in the linker).

Python has no placement dimension at all (no addresses). An a816 module is a
Python module **plus a linker-placed code section**. That's why this doc keeps
namespace separate from compile/place: it's *more* separated than Python, and
correctly so. Steal Python's visibility model; keep emission decoupled from it.

## Migration / back-compat

- **Import-surfacing is additive** - existing `.extern` declarations keep
  working; they just become redundant once import surfaces the symbol. Migrate
  opportunistically; add a `fluff` rule that flags "redundant `.extern` for an
  imported module's public symbol."
- **Named-alloc namespacing is the breaking one.** If `.alloc render { foo: }`
  starts publishing `render.foo` instead of bare `foo`, every cross-reference
  to a named alloc's members changes. Gate behind a flag or format epoch;
  migrate ff4 (our test bed) first. (Anonymous allocs are unaffected - still
  bubble bare.)
- **Object format:** export the public/private classification + constants
  uniformly so import can replay a module's scope. May need a version bump.

## Open questions

- **Nesting + transitivity.** Scopes nest lexically; modules chain via
  `.import`. Does `.import` of a module that itself imports another surface the
  transitive publics, or only the direct module's? (Lean: direct only;
  re-export is explicit.)
- **Named-alloc member references.** Today named-alloc body labels are used
  bare across the module. Moving to `render.foo` is the cleaner model but a
  wide source change. Is there an interim where named allocs *also* bubble bare
  (back-compat) while the dotted form becomes canonical? Or a fluff autofix?
- **Privacy granularity.** Is `_`-private *module*-private or *scope*-private?
  Today it's tangled (per-alloc). Pick one: module-private is simpler and
  matches "module = compiled scope."
- **Constant linking.** Do cross-module constants resolve at link (value
  substitution) or only via compile-time import-surfacing? Import-surfacing is
  cleaner (no link symbol for a compile-time value); extern-of-constant becomes
  unnecessary rather than fixed.

## Out of scope

- The freespace/pool allocator itself (placement mechanics are unchanged; only
  the *namespace* coupling of `.alloc` is in question).
- The bss/`.reserve` work (orthogonal; reservations are placement + symbols,
  and ride whatever namespace model lands here).

## Evidence (this session, ff4-mig2)

Fixing ff4 against current a816 required: hoisting `.extern` out of every
`.alloc`/`.scope` body (6 files), de-underscoring ~15 cross-alloc "private"
labels, and extracting shared scope constants to a `.include` because
`.extern`-of-a-constant didn't resolve. Every one of those is a symptom of the
three-concept tangle this doc proposes to remove.
