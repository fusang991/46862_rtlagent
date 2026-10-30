# RTL semantics checklist, revision 2

Extends revision 1 with ownership of externally supplied state and the distinction
between continuous and procedural assignments. Derived from observed development
compiler failures. No task identifiers, reference implementations or expected
answers are included in injected notes. Select with `skill_policy: rtl_semantics_v2`.

This revision has not yet been evaluated with fresh model inference. The frozen
revision-1 ablations stay unchanged; revision 2 requires a separate experiment.
Evidence: [autonomous study](../../docs/journal/20261017_234742_autonomous_iteration.md).

V3 keeps the V2 semantic checklist and adds general Python expression clarity, conditional-expression syntax, keyword access, and ordinary integer output guidance. These are prompt instructions, not code rewriting or a design emitter. Fresh model quality must be measured separately; loading this file does not prove a syntax or benchmark improvement.
