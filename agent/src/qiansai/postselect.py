"""Change final selection while preserving the original generation trajectory."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
import time

from chipmate.inference import (
    DEFAULT_MAX_TURNS, DEFAULT_N, DEFAULT_NUM_VERIFY_TESTS, DEFAULT_SEED, DEFAULT_TEMPERATURE,
    _INVALID_P, _INVALID_V, _verify_pair, run_problem,
)
from chipmate.prompts import build_v_prompt, extract_python, extract_verilog

from .candidate_policy import cached_simulators, verification_viability
from .verification_policy import verification_policy


class RecordingBackend:
    def __init__(self, backend):
        self.backend, self.pools = backend, []

    def generate(self, *args, **kwargs):
        outputs = self.backend.generate(*args, **kwargs)
        self.pools.append(outputs)
        return outputs


def select_recorded(ref_sv, v_pools, p_pools, *, n, num_verify_tests, seed, checkpoint=None, v_checker=None):
    """No prompting or generation; evaluate every recorded round's full matrix."""
    best, best_rank, matrices = None, None, []
    for turn, (v_pool, p_pool) in enumerate(zip(v_pools, p_pools)):
        vs = [extract_verilog(x or "") or _INVALID_V for x in v_pool]
        ps = [extract_python(x or "") or _INVALID_P for x in p_pool]
        if len(vs) != n or len(ps) != n:
            raise ValueError("Recorded pool size differs from configured candidate count")
        with ThreadPoolExecutor(max_workers=n * n) as pool:
            futures = {(i, j): pool.submit(_verify_pair, ref_sv, vs[i], ps[j], num_verify_tests, seed + turn)
                       for i in range(n) for j in range(n)}
            results = {key: future.result() for key, future in futures.items()}
        rates = [[float(results[i, j].get("match_rate", 0)) for j in range(n)] for i in range(n)]
        v_support = [sum(x >= 1 for x in row) for row in rates]
        p_support = [sum(rates[i][j] >= 1 for i in range(n)) for j in range(n)]
        evidence = [v_checker(code) for code in vs] if v_checker else [0.0] * n
        viability = {(i, j): verification_viability(check, vs[i], ps[j])
                     for (i, j), check in results.items()}
        i, j = max(results, key=lambda pair: (evidence[pair[0]], rates[pair[0]][pair[1]],
                                             *viability[pair], v_support[pair[0]],
                                             p_support[pair[1]], -pair[0], -pair[1]))
        rank = (evidence[i], rates[i][j], *viability[i, j], v_support[i], p_support[j], -turn, -i, -j)
        matrices.append({"turn": turn, "rates": rates, "selected": [i, j],
                         "v_full_match_support": v_support, "p_full_match_support": p_support,
                         "selected_viability": viability[i, j],
                         "public_example_scores": evidence if v_checker else None})
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best = {"verilog": vs[i], "python": ps[j], "best_match_rate": rates[i][j],
                    "best_turn": turn, "matched": rates[i][j] >= 1,
                    "selected_indices": [i, j],
                    "selected_viability": viability[i, j],
                    "public_example_match_rate": evidence[i] if v_checker else None}
            if checkpoint:
                checkpoint({**best, "phase": "postselection"})
    if best is None:
        raise ValueError("No recorded candidate pools")
    return best, matrices


@dataclass
class PostselectionResult:
    control: object
    selected: dict
    matrices: list
    cache: dict
    repairs: list = field(default_factory=list)
    acceptance: dict | None = None

    def to_dict(self):
        control = self.control.to_dict()
        return {**control, **self.selected, "candidate_policy": "postselect_v1",
                "control_result": control, "postselection_matrices": self.matrices,
                "simulation_cache": self.cache, "public_example_repairs": self.repairs,
                "selection_acceptance": self.acceptance,
                "turns_used": control["turns_used"] + len(self.repairs)}


def run_postselect(*, task_id, question, ref_sv, v_backend, p_backend=None,
                   n=DEFAULT_N, max_turns=DEFAULT_MAX_TURNS,
                   num_verify_tests=DEFAULT_NUM_VERIFY_TESTS,
                   temperature=DEFAULT_TEMPERATURE, seed=DEFAULT_SEED, checkpoint=None,
                   post_verification_policy="upstream", v_checker=None, public_repair_turns=0, deadline=None,
                   post_acceptance_policy="unconditional"):
    if post_verification_policy not in {"upstream", "reset_names_v1", "rtl_tools_v1", "rtl_timing_v1"}:
        raise ValueError(f"Unknown post-verification policy: {post_verification_policy}")
    if public_repair_turns < 0:
        raise ValueError("Public example repair count must be nonnegative")
    if post_acceptance_policy not in {"unconditional", "strong_evidence_v1", "strong_evidence_v2", "strong_evidence_v3"}:
        raise ValueError(f"Unknown post-acceptance policy: {post_acceptance_policy}")
    gate = None
    repairs = []
    v, p = RecordingBackend(v_backend), RecordingBackend(p_backend if p_backend is not None else v_backend)
    with ExitStack() as generation_context:
        v_cache, p_cache = generation_context.enter_context(cached_simulators())
        control = run_problem(task_id=task_id, question=question, ref_sv=ref_sv,
                    v_backend=v, p_backend=p, n=n, max_turns=max_turns,
                    num_verify_tests=num_verify_tests, temperature=temperature, seed=seed)
        before = {"v": v_cache.stats(), "p": p_cache.stats()}
        if checkpoint:
            checkpoint({**control.to_dict(), "phase": "generation_complete"})
        # Timing changes the testbench bytes. Close the generation cache before
        # binding that policy, then use a separate cache for final selection.
        # Original requests and feedback above remain exactly unchanged.
        separate_cache = post_verification_policy == "rtl_timing_v1"
        if separate_cache:
            generation_context.close()
        with ExitStack() as selection_context:
            selection_context.enter_context(verification_policy(post_verification_policy, question=question))
            if separate_cache:
                v_cache, p_cache = selection_context.enter_context(cached_simulators())
            if post_acceptance_policy != "unconditional":
                from .selection_acceptance import EvidenceGate
                gate = EvidenceGate(control.to_dict(), ref_sv, num_verify_tests=num_verify_tests,
                                    seed=seed, checkpoint=checkpoint, v_checker=v_checker,
                                    policy=post_acceptance_policy)
            selection_checkpoint = gate.observe if gate else checkpoint
            selected, matrices = select_recorded(ref_sv, v.pools, p.pools, n=n,
                                num_verify_tests=num_verify_tests, seed=seed, checkpoint=selection_checkpoint, v_checker=v_checker)
            for attempt in range(public_repair_turns):
                active = gate.chosen if gate else selected
                if v_checker is None or active["public_example_match_rate"] >= 1:
                    break
                if deadline is not None and deadline - time.monotonic() < 60:
                    break
                feedback = v_checker.feedback(active["verilog"])
                prompt = build_v_prompt(question, len(v.pools), [active["verilog"]], feedback)
                v.generate(prompt, n, temperature)
                # This is one extra V call, not an unrecorded P generation.
                p.pools.append(p.pools[-1])
                repairs.append({"attempt": attempt, "feedback": feedback,
                                "p_candidates": "reuse last recorded model-generated P pool"})
                selected, matrices = select_recorded(ref_sv, v.pools, p.pools, n=n,
                                num_verify_tests=num_verify_tests, seed=seed, checkpoint=selection_checkpoint,
                                v_checker=v_checker)
            if gate:
                selected = gate.chosen
    return PostselectionResult(control, selected, matrices,
                               {"after_generation": before, "final": {"v": v_cache.stats(), "p": p_cache.stats()},
                                "separate_postselection_cache": separate_cache}, repairs,
                               gate.summary() if gate else None)
