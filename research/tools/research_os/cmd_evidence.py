"""Commands for evidence and belief: observation, claim, decision."""

from .common import authority_args, authority_for, emit, parse_scope, resolve_list, split, writer
from .rules import decision_needs_authority
from .state import EVIDENCE_KEYS

CLAIM_STATUSES = ("candidate", "contested", "supported", "refuted", "superseded")
VERIFY_RESULTS = ("consistent_with_evidence", "inconsistent_with_evidence", "insufficient_evidence")


# ---------------------------------------------------------------- observation

def cmd_observation_create(args):
    w = writer(args, "interpreter")

    def build(state):
        payload = {"alias": state.next_alias("O"), "experiments": resolve_list(state, args.experiments, "experiment"),
                   "statement": args.statement, "evidence": resolve_list(state, args.evidence, "run")}
        return [w.event(state, "observation_created", payload)]

    return emit(w.transact(build))


def cmd_observation_retract(args):
    w = writer(args, "interpreter")

    def build(state):
        subject = state.resolve(args.ref, "observation")
        return [w.event(state, "observation_retracted", {"reason": args.reason}, subject=subject)]

    return emit(w.transact(build))


# ---------------------------------------------------------------- claim

def _evidence(state, args, current):
    evidence = {}
    for key in EVIDENCE_KEYS:
        given = getattr(args, key)
        evidence[key] = resolve_list(state, given) if given is not None else list(current.get(key, []))
    return evidence


def cmd_claim_propose(args):
    w = writer(args, "interpreter")

    def build(state):
        payload = {"alias": state.next_alias("C"), "statement": args.statement, "scope": parse_scope(args.scope),
                   "hypotheses": resolve_list(state, args.hypotheses, "hypothesis"),
                   "evidence": _evidence(state, args, {})}
        return [w.event(state, "claim_proposed", payload)]

    return emit(w.transact(build))


def cmd_claim_revise(args):
    w = writer(args, "interpreter")

    def build(state):
        subject = state.resolve(args.ref, "claim")
        payload = {"reason": args.reason}
        if args.statement is not None:
            payload["statement"] = args.statement
        if args.scope is not None:
            payload["scope"] = parse_scope(args.scope)
        if args.hypotheses is not None:
            payload["hypotheses"] = resolve_list(state, args.hypotheses, "hypothesis")
        if any(getattr(args, key) is not None for key in EVIDENCE_KEYS):
            payload["evidence"] = _evidence(state, args, state.records[subject]["evidence"])
        return [w.event(state, "claim_revised", payload, subject=subject)]

    return emit(w.transact(build))


def cmd_claim_transition(args):
    w = writer(args, "interpreter")

    def build(state):
        subject = state.resolve(args.ref, "claim")
        required = args.to in ("supported", "refuted")
        payload = {"from": state.records[subject]["status"], "to": args.to, "reason": args.reason,
                   "authority": authority_for(state, args, "claim_transition", subject, {"to": args.to}, required)}
        if args.superseded_by:
            payload["superseded_by"] = state.resolve(args.superseded_by, "claim")
        return [w.event(state, "claim_status_changed", payload, subject=subject)]

    return emit(w.transact(build))


def cmd_claim_verify(args):
    w = writer(args)

    def build(state):
        subject = state.resolve(args.ref, "claim")
        payload = {"claim_version": state.version(subject), "result": args.result,
                   "findings": args.finding or [], "context_excluded": split(args.excluded)}
        return [w.event(state, "claim_verification_recorded", payload, subject=subject, role="verifier")]

    return emit(w.transact(build))


# ---------------------------------------------------------------- decision

def cmd_decision_propose(args):
    w = writer(args, "proposer")

    def build(state):
        payload = {"alias": state.next_alias("D"), "decision": args.decision, "reason": args.reason,
                   "based_on": resolve_list(state, args.based_on)}
        if args.revisit_if:
            payload["revisit_if"] = args.revisit_if
        return [w.event(state, "decision_proposed", payload)]

    return emit(w.transact(build))


def _decision_act(args, etype, to):
    w = writer(args, "proposer")

    def build(state):
        subject = state.resolve(args.ref, "decision")
        required = decision_needs_authority(state, subject)
        payload = {"authority": authority_for(state, args, "decision", subject, {"to": to}, required)}
        if etype == "decision_revoked":
            payload["reason"] = args.reason
        return [w.event(state, etype, payload, subject=subject)]

    return emit(w.transact(build))


# ---------------------------------------------------------------- parser

def _claim_fields(parser, required):
    parser.add_argument("--statement", required=required)
    parser.add_argument("--scope", action="append", help="KEY=V1,V2 (repeatable)")
    parser.add_argument("--hypotheses", help="Hypothesis refs, comma-separated")
    for key in EVIDENCE_KEYS:
        parser.add_argument(f"--{key}", help=f"{key} evidence refs (Experiment/Run/Observation), comma-separated")


def _register_observation(sub, common):
    obs = sub.add_parser("observation").add_subparsers(dest="action", required=True)
    p = obs.add_parser("create", parents=[common])
    p.add_argument("--experiments", required=True)
    p.add_argument("--statement", required=True)
    p.add_argument("--evidence", required=True, help="Run refs, comma-separated")
    p.set_defaults(func=cmd_observation_create)
    p = obs.add_parser("retract", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_observation_retract)


def _register_claim(sub, common):
    claim = sub.add_parser("claim").add_subparsers(dest="action", required=True)
    p = claim.add_parser("propose", parents=[common])
    _claim_fields(p, required=True)
    p.set_defaults(func=cmd_claim_propose)
    p = claim.add_parser("revise", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    _claim_fields(p, required=False)
    p.set_defaults(func=cmd_claim_revise)
    p = claim.add_parser("transition", parents=[common])
    p.add_argument("ref")
    p.add_argument("--to", choices=CLAIM_STATUSES, required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--superseded-by", dest="superseded_by")
    authority_args(p)
    p.set_defaults(func=cmd_claim_transition)
    p = claim.add_parser("verify", parents=[common], help="record a verifier result (role forced to verifier)")
    p.add_argument("ref")
    p.add_argument("--result", choices=VERIFY_RESULTS, required=True)
    p.add_argument("--finding", action="append", help="finding (repeatable, max 10)")
    p.add_argument("--excluded", default="producer_handoff,producer_reasoning")
    p.set_defaults(func=cmd_claim_verify)


def _register_decision(sub, common):
    dec = sub.add_parser("decision").add_subparsers(dest="action", required=True)
    p = dec.add_parser("propose", parents=[common])
    p.add_argument("--decision", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--based-on", dest="based_on", default="")
    p.add_argument("--revisit-if", dest="revisit_if")
    p.set_defaults(func=cmd_decision_propose)
    p = dec.add_parser("make", parents=[common])
    p.add_argument("ref")
    authority_args(p)
    p.set_defaults(func=lambda args: _decision_act(args, "decision_made", "made"))
    p = dec.add_parser("revoke", parents=[common])
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    authority_args(p)
    p.set_defaults(func=lambda args: _decision_act(args, "decision_revoked", "revoked"))


def register(sub, common):
    _register_observation(sub, common)
    _register_claim(sub, common)
    _register_decision(sub, common)
