"""Accept candidate changes from observed verification evidence, never grades."""

from chipmate.cross_verify import cross_verify

from .candidate_policy import verification_viability


def accept_evidence(*, policy, control_sv, proposal_sv, control_rate, proposal_rate,
                    control_public=None, proposal_public=None, proposal_unknown=False):
    if policy not in {'strong_evidence_v1', 'strong_evidence_v2', 'strong_evidence_v3'}:
        raise ValueError(f'Unknown acceptance policy: {policy}')
    if proposal_public is not None and proposal_public < control_public:
        return False, 'public_constraint_regression'
    if proposal_sv != 3:
        if (policy == 'strong_evidence_v3' and proposal_sv == 2 and proposal_unknown
                and proposal_public is not None and proposal_public >= 1
                and proposal_public > control_public):
            return True, 'complete_public_constraints_with_unconstrained_unknowns'
        return False, 'partial_or_equal_evidence_preserve_control'
    if proposal_public is not None and proposal_public > control_public:
        return True, 'public_constraint_improvement'
    recoverable = control_sv == 0 if policy == 'strong_evidence_v2' else control_sv < 3
    if recoverable:
        return True, 'verilog_compile_recovery' if policy.endswith('v2') else 'verilog_execution_improvement'
    if proposal_rate >= 1 and control_rate < 1:
        return True, 'complete_cross_agreement'
    return False, 'partial_or_equal_evidence_preserve_control'


class EvidenceGate:
    """Partial RTL/Python agreement alone is insufficient for replacement.

    Public constraints, successful simulation after a tool failure, or complete
    agreement can justify a change. This is still not independent proof.
    """
    def __init__(self, control, ref_sv, *, num_verify_tests, seed,
                 checkpoint=None, v_checker=None, policy='strong_evidence_v1'):
        if policy not in {'strong_evidence_v1', 'strong_evidence_v2', 'strong_evidence_v3'}:
            raise ValueError(f'Unknown acceptance policy: {policy}')
        self.policy = policy
        self.control = dict(control)
        self.chosen = dict(control)
        self.ref_sv, self.num_tests, self.seed = ref_sv, num_verify_tests, seed
        self.checkpoint, self.v_checker = checkpoint, v_checker
        self.control_check = self.verify(control)
        self.control_public = v_checker(control['verilog']) if v_checker else None
        if v_checker:
            self.chosen['public_example_match_rate'] = self.control_public
        self.decisions = []

    def verify(self, candidate):
        # Check SV even when P extraction failed. The upstream _verify_pair
        # shortcut would confuse "not run" with an observed compile failure.
        return cross_verify(self.ref_sv, candidate['verilog'], candidate.get('python', ''),
                            num_tests=self.num_tests,
                            seed=self.seed + max(0, candidate.get('best_turn', 0)))

    def observe(self, candidate):
        check = self.verify(candidate)
        old_sv = verification_viability(self.control_check, self.control['verilog'],
                                        self.control.get('python', ''))[0]
        new_sv = verification_viability(check, candidate['verilog'], candidate.get('python', ''))[0]
        public = self.v_checker(candidate['verilog']) if self.v_checker else None
        old_rate, new_rate = self.control_check.get('match_rate', 0), check.get('match_rate', 0)
        accepted, reason = accept_evidence(policy=self.policy, control_sv=old_sv, proposal_sv=new_sv,
                                          control_rate=old_rate, proposal_rate=new_rate,
                                          control_public=self.control_public, proposal_public=public,
                                          proposal_unknown='unknown_output' in (check.get('sv_error') or '')
                                              or 'incomplete_or_unknown' in (check.get('sv_error') or ''))
        decision = {'accepted': accepted, 'reason': reason,
                    'control_post_match_rate': old_rate, 'proposal_post_match_rate': new_rate,
                    'control_sv_viability': old_sv, 'proposal_sv_viability': new_sv,
                    'control_sv_error': self.control_check.get('sv_error'), 'proposal_sv_error': check.get('sv_error'),
                    'control_public_match_rate': self.control_public, 'proposal_public_match_rate': public,
                    'proposal_turn': candidate.get('best_turn'),
                    'proposal_indices': candidate.get('selected_indices')}
        self.decisions.append(decision)
        if accepted:
            self.chosen = dict(candidate)
            if self.checkpoint:
                self.checkpoint({**candidate, 'selection_acceptance': decision})

    def summary(self):
        return {'policy': self.policy, 'control_post_check': self.control_check,
                'decisions': self.decisions,
                'preserved_control_rtl': self.chosen['verilog'] == self.control['verilog']}
