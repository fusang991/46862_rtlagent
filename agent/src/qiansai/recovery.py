"""Spend extra generation only when internal verification remains unresolved."""

from dataclasses import dataclass
import time

from chipmate.inference import DEFAULT_MAX_TURNS, DEFAULT_N, DEFAULT_NUM_VERIFY_TESTS, DEFAULT_SEED, DEFAULT_TEMPERATURE

from .candidate_policy import run_all_pairs
from .postselect import run_postselect
from .skill_backend import apply_skill
from .verification_policy import verification_policy


@dataclass
class RecoveryResult:
    initial: dict
    recovery: dict | None
    chosen: dict
    skipped_deadline: bool

    def to_dict(self):
        total_turns = self.initial["turns_used"] + (self.recovery["turns_used"] if self.recovery else 0)
        chosen_phase = "recovery" if self.chosen is self.recovery else "initial"
        return {**self.chosen, "candidate_policy": "postselect_recover_v1",
                "turns_used": total_turns, "selected_phase": chosen_phase,
                "selected_phase_turn": self.chosen["best_turn"],
                "best_turn": self.chosen["best_turn"] + (self.initial["turns_used"] if chosen_phase == "recovery" else 0),
                "initial_result": self.initial, "recovery_result": self.recovery,
                "recovery_triggered": self.recovery is not None,
                "recovery_skipped_deadline": self.skipped_deadline,
                "total_generation_turns": total_turns}


def run_recovery(*, task_id, question, ref_sv, v_backend, p_backend=None,
                 n=DEFAULT_N, max_turns=DEFAULT_MAX_TURNS,
                 num_verify_tests=DEFAULT_NUM_VERIFY_TESTS,
                 temperature=DEFAULT_TEMPERATURE, seed=DEFAULT_SEED,
                 checkpoint=None, deadline=None, recovery_turns=2,
                 min_recovery_s=60, post_verification_policy="rtl_tools_v1"):
    if recovery_turns < 1:
        raise ValueError("Recovery turn count must be positive")
    initial = run_postselect(task_id=task_id, question=question, ref_sv=ref_sv,
                 v_backend=v_backend, p_backend=p_backend, n=n, max_turns=max_turns,
                 num_verify_tests=num_verify_tests, temperature=temperature, seed=seed,
                 checkpoint=checkpoint, post_verification_policy=post_verification_policy).to_dict()
    if initial["matched"]:
        return RecoveryResult(initial, None, initial, False)
    if deadline is not None and deadline - time.monotonic() < min_recovery_s:
        return RecoveryResult(initial, None, initial, True)
    chosen = initial
    def quality(candidate):
        return candidate["best_match_rate"], tuple(candidate.get("selected_viability", ()))
    def improved(candidate):
        nonlocal chosen
        if quality(candidate) > quality(chosen):
            chosen = {**initial, **candidate}
            if checkpoint:
                checkpoint({**candidate, "phase": "conditional_recovery"})
    p_backend = p_backend if p_backend is not None else v_backend
    with verification_policy("rtl_tools_v1"):
        recovery = run_all_pairs(task_id=task_id, question=question, ref_sv=ref_sv,
                    v_backend=apply_skill(v_backend, "v", "rtl_semantics_v1"),
                    p_backend=apply_skill(p_backend, "p", "rtl_semantics_v1"),
                    n=n, max_turns=recovery_turns, num_verify_tests=num_verify_tests,
                    temperature=temperature, seed=seed + 1000, checkpoint=improved).to_dict()
    if quality(recovery) > quality(initial):
        chosen = recovery
    return RecoveryResult(initial, recovery, chosen, False)
