"""Experimental all-pairs verification with the original generation budget."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import importlib
import json
import threading

from chipmate.feedback import format_p_feedback, format_v_feedback
from chipmate.inference import (
    DEFAULT_MAX_TURNS, DEFAULT_N, DEFAULT_NUM_VERIFY_TESTS, DEFAULT_SEED, DEFAULT_TEMPERATURE,
    MultiAgentResult, TurnLog, _INVALID_P, _INVALID_V, _verify_pair,
)
from chipmate.prompts import build_p_prompt, build_v_prompt, extract_python, extract_verilog

_LOCK = threading.RLock()


def verification_viability(check, v_code, p_code):
    """Break zero-agreement ties using observed validity, never an official grade."""
    has_v = bool(v_code) and not v_code.startswith("We can not")
    has_p = bool(p_code) and not p_code.startswith("We can not")
    error = check.get("sv_error")
    if not has_v:
        v_status = 0
    elif check.get("meta_error"):
        v_status = 1  # Extracted, but the upstream pair check did not execute it.
    elif error:
        v_status = 0 if any(word in error for word in
            ("compile", "elaborate", "interface", "nonsynthesizable")) else 2
    else:
        v_status = 3
    p_status = int(v_status == 3 and not check.get("py_error") and not check.get("meta_error"))
    return v_status, p_status, int(has_v), int(has_p)


class SimulationCache:
    """One execution per identical input, including concurrent duplicate calls."""
    def __init__(self, function):
        self.function = function
        self.lock = threading.Lock()
        self.futures = {}
        self.requests = self.hits = self.misses = 0

    def __call__(self, *args, **kwargs):
        key = hashlib.sha256(json.dumps([args, kwargs], sort_keys=True).encode()).digest()
        with self.lock:
            self.requests += 1
            owner = key not in self.futures
            if owner:
                self.futures[key] = Future()
                self.misses += 1
            else:
                self.hits += 1
            future = self.futures[key]
        if owner:
            try:
                future.set_result(self.function(*args, **kwargs))
            except BaseException as error:
                future.set_exception(error)
                raise
        return future.result()

    def stats(self):
        return {"requests": self.requests, "hits": self.hits, "misses": self.misses}


@contextmanager
def cached_simulators():
    upstream = importlib.import_module("chipmate.cross_verify")
    with _LOCK:
        old_v, old_p = upstream.simulate_verilog, upstream.simulate_python
        v, p = SimulationCache(old_v), SimulationCache(old_p)
        upstream.simulate_verilog, upstream.simulate_python = v, p
        try:
            yield v, p
        finally:
            upstream.simulate_verilog, upstream.simulate_python = old_v, old_p


@dataclass
class PairResult:
    result: MultiAgentResult
    matrices: list
    cache: dict

    def to_dict(self):
        return {**self.result.to_dict(), "candidate_policy": "all_pairs_v1",
                "pair_matrices": self.matrices, "simulation_cache": self.cache,
                "selected_viability": self.matrices[self.result.best_turn]["selected_viability"]}


def run_all_pairs(*, task_id, question, ref_sv, v_backend, p_backend=None,
                  n=DEFAULT_N, max_turns=DEFAULT_MAX_TURNS,
                  num_verify_tests=DEFAULT_NUM_VERIFY_TESTS,
                  temperature=DEFAULT_TEMPERATURE, seed=DEFAULT_SEED, checkpoint=None):
    if n < 1 or max_turns < 1:
        raise ValueError("Candidate and turn counts must be positive")
    if p_backend is None:
        p_backend = v_backend
    v_carry = p_carry = v_error = p_error = ""
    best_v = best_p = ""
    best_rate, best_turn, matched = -1.0, -1, False
    best_rank = None
    logs, matrices = [], []
    with cached_simulators() as (v_cache, p_cache):
        for turn in range(max_turns):
            v_prompt = build_v_prompt(question, turn, [v_carry] if v_carry else [], v_error)
            p_prompt = build_p_prompt(question, turn, [p_carry] if p_carry else [], p_error)
            with ThreadPoolExecutor(max_workers=2) as pool:
                fv = pool.submit(v_backend.generate, v_prompt, n, temperature)
                fp = pool.submit(p_backend.generate, p_prompt, n, temperature)
                vs = [extract_verilog(x or "") or _INVALID_V for x in fv.result()]
                ps = [extract_python(x or "") or _INVALID_P for x in fp.result()]
            with ThreadPoolExecutor(max_workers=n * n) as pool:
                futures = {(i, j): pool.submit(_verify_pair, ref_sv, vs[i], ps[j],
                                                num_verify_tests, seed + turn)
                           for i in range(n) for j in range(n)}
                verified = {key: future.result() for key, future in futures.items()}
            matrix = [[float(verified[i, j].get("match_rate", 0)) for j in range(n)] for i in range(n)]
            v_support = [sum(value >= 1 for value in row) for row in matrix]
            p_support = [sum(matrix[i][j] >= 1 for i in range(n)) for j in range(n)]
            viability = {(i, j): verification_viability(check, vs[i], ps[j])
                         for (i, j), check in verified.items()}
            i, j = max(verified, key=lambda ij: (matrix[ij[0]][ij[1]],
                       *viability[ij],
                       v_support[ij[0]], p_support[ij[1]], -ij[0], -ij[1]))
            rate, check = matrix[i][j], verified[i, j]
            rank = (rate, *viability[i, j], v_support[i], p_support[j])
            if best_rank is None or rank > best_rank:
                best_rate, best_v, best_p, best_turn = rate, vs[i], ps[j], turn
                best_rank = rank
            if not vs[i].startswith("We can not"):
                v_carry = vs[i]
            if not ps[j].startswith("We can not"):
                p_carry = ps[j]
            v_error = format_v_feedback(check) if rate < 1 else ""
            p_error = format_p_feedback(check) if rate < 1 else ""
            logs.append(TurnLog(turn=turn, n=n, best_pair=i * n + j, best_match_rate=rate,
                        all_match_rates=[value for row in matrix for value in row],
                        v_lens=[len(x) for x in vs], p_lens=[len(x) for x in ps],
                        best_v_so_far=best_v, best_mr_so_far=best_rate))
            matrices.append({"turn": turn, "candidates_per_role": n, "pairs": n * n,
                             "rates": matrix, "selected": [i, j],
                             "selected_viability": viability[i, j],
                             "v_full_match_support": v_support, "p_full_match_support": p_support})
            if checkpoint:
                checkpoint({"verilog": best_v, "python": best_p,
                            "best_match_rate": best_rate, "best_turn": best_turn,
                            "selected_viability": tuple(best_rank[1:5]),
                            "matched": best_rate >= 1, "phase": "candidate_turn_complete"})
            if rate >= 1:
                matched = True
                break
    result = MultiAgentResult(task_id=task_id, verilog=best_v or v_carry,
               python=best_p or p_carry, matched=matched, best_match_rate=best_rate,
               best_turn=best_turn, turns_used=len(logs), turn_logs=logs)
    return PairResult(result, matrices, {"v": v_cache.stats(), "p": p_cache.stats()})
