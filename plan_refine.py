"""Apply a human's requested changes to an InspectablePlan.

This is the other half of the approve / request-changes loop: after a
human sends a plan back for changes (see plan-approve), the agent
applies exactly those changes and produces a new version of the same
plan — same id, version bumped by one — so the human can decide again
on the revised plan. Nothing is applied silently and nothing is
applied halfway.

Rules (Week 2, BRJP26100123 — human-in-the-loop control):

- A refinement needs a reviewer and a reason: who asked for the
  changes, and why they are being applied now.
- Every change must apply or none does. An unknown operation, a
  missing step, a duplicate step id, or removing the last step
  refuses the whole refinement — the plan is left untouched.
- The plan id never changes; only the version bumps. A revised plan
  is a new version of the same plan, not a different plan.
- When a decisions log is supplied, the latest decision on the plan
  must be ``request-changes``. You cannot refine a plan that was
  approved, rejected, or never sent back — there is nothing to refine.
- Each refinement appends one record to a JSONL log, never edited
  in place, so the path from v1 to v2 is traceable.

Supported change operations (each change is a dict with an ``op``):

- ``reword-step`` — ``step``, ``action`` (and optional ``why``).
- ``add-step`` — ``step`` (a full step dict); optional ``after``
  (a step id to insert after, otherwise appended).
- ``remove-step`` — ``step`` (a step id).
- ``gate-step`` — ``step`` (a step id); sets ``needs_permission``.
- ``add-failure-point`` — ``step``, ``failure_point``.
- ``add-assumption`` / ``add-constraint`` / ``add-risk`` — ``text``.
- ``set-why`` — ``step``, ``why``.

Stdlib only, no third-party dependencies.
"""

import argparse
import copy
import json
import sys
from datetime import datetime, timezone

OPS = (
    "reword-step",
    "add-step",
    "remove-step",
    "gate-step",
    "add-failure-point",
    "add-assumption",
    "add-constraint",
    "add-risk",
    "set-why",
)


class RefinementRefused(ValueError):
    """The refinement was not applied because it breaks a rule above."""


def _find_step(plan, step_id):
    for step in plan.get("steps", []) or []:
        if isinstance(step, dict) and step.get("id") == step_id:
            return step
    return None


def _require_text(value, what):
    text = str(value or "").strip()
    if not text:
        raise RefinementRefused(f"A change needs a non-empty {what}.")
    return text


def _apply_one(plan, change):
    """Apply one change dict to the plan copy in place; raise if invalid."""
    if not isinstance(change, dict):
        raise RefinementRefused(f"Each change must be an object, got {change!r}.")
    op = change.get("op")
    if op not in OPS:
        raise RefinementRefused(
            f"Unknown change operation {op!r}; use one of {', '.join(OPS)}.")

    if op in ("add-assumption", "add-constraint", "add-risk"):
        key = {"add-assumption": "assumptions",
               "add-constraint": "constraints",
               "add-risk": "risks"}[op]
        text = _require_text(change.get("text"), "text")
        items = plan.setdefault(key, [])
        if text in items:
            raise RefinementRefused(f"That {key[:-1]} is already in the plan.")
        items.append(text)
        return

    if op == "add-step":
        step = change.get("step")
        if not isinstance(step, dict) or not str(step.get("id", "")).strip():
            raise RefinementRefused("add-step needs a step object with an id.")
        if _find_step(plan, step["id"]) is not None:
            raise RefinementRefused(
                f"Step {step['id']!r} already exists; cannot add it again.")
        if not str(step.get("action", "")).strip():
            raise RefinementRefused("add-step needs a step with an action.")
        new_step = {
            "id": step["id"],
            "action": step["action"].strip(),
            "why": str(step.get("why", "")).strip(),
            "needs_permission": bool(step.get("needs_permission", False)),
            "failure_points": list(step.get("failure_points") or []),
            "alternatives_considered": list(step.get("alternatives_considered") or []),
        }
        after = change.get("after")
        if after:
            anchor = _find_step(plan, after)
            if anchor is None:
                raise RefinementRefused(
                    f"Cannot add a step after {after!r}: no such step.")
            plan["steps"].insert(plan["steps"].index(anchor) + 1, new_step)
        else:
            plan["steps"].append(new_step)
        return

    step_id = change.get("step")
    step = _find_step(plan, step_id) if step_id else None
    if step is None:
        raise RefinementRefused(f"No step {step_id!r} in the plan.")

    if op == "reword-step":
        step["action"] = _require_text(change.get("action"), "action")
        if change.get("why") is not None:
            step["why"] = _require_text(change.get("why"), "why")
    elif op == "remove-step":
        if len(plan["steps"]) <= 1:
            raise RefinementRefused("Cannot remove the last step of a plan.")
        plan["steps"].remove(step)
    elif op == "gate-step":
        step["needs_permission"] = True
    elif op == "add-failure-point":
        point = _require_text(change.get("failure_point"), "failure point")
        points = step.setdefault("failure_points", [])
        if point in points:
            raise RefinementRefused(
                f"Step {step_id!r} already lists that failure point.")
        points.append(point)
    elif op == "set-why":
        step["why"] = _require_text(change.get("why"), "why")


def refine(plan, changes, reviewer, reason, prior_decisions=(),
           refined_at=None):
    """Apply changes to a plan copy and return (refined_plan, record).

    ``prior_decisions`` are records from a decisions log (plan-approve
    format). When supplied, the latest decision for the plan must be
    ``request-changes``. The original plan is never mutated.
    """
    if not isinstance(plan, dict) or not str(plan.get("goal", "")).strip():
        raise RefinementRefused("The plan has no goal to refine.")
    if not plan.get("steps"):
        raise RefinementRefused("The plan has no steps to refine.")
    if not str(plan.get("id", "")).strip():
        raise RefinementRefused(
            "The plan needs an id so the new version stays grouped with it.")
    if not str(reviewer or "").strip():
        raise RefinementRefused("A refinement needs a named reviewer.")
    if not str(reason or "").strip():
        raise RefinementRefused("A refinement needs a reason.")
    changes = list(changes or [])
    if not changes:
        raise RefinementRefused(
            "A refinement needs at least one change: what must change.")

    pid = str(plan["id"])
    if prior_decisions:
        latest = None
        for record in prior_decisions:
            if record.get("plan_id") == pid:
                latest = record
        if latest is None:
            raise RefinementRefused(
                f"No decision on plan {pid!r} yet; nothing was sent back "
                "for changes.")
        if latest.get("decision") != "request-changes":
            raise RefinementRefused(
                f"Plan {pid!r} was last decided as {latest['decision']!r}, "
                "not request-changes; there is nothing to refine.")

    refined = copy.deepcopy(plan)
    for change in changes:
        _apply_one(refined, change)

    from_version = int(plan.get("version", 1))
    refined["version"] = from_version + 1
    stamp = refined_at or datetime.now(timezone.utc).isoformat()
    refined["refined_at"] = stamp
    record = {
        "plan_id": pid,
        "from_version": from_version,
        "to_version": from_version + 1,
        "reviewer": reviewer.strip(),
        "reason": reason.strip(),
        "changes": changes,
        "refined_at": stamp,
    }
    return refined, record


def append_record(path, record):
    """Append one refinement record to a JSONL log."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def read_log(path):
    """Read a JSONL log back into a list of records (empty if missing)."""
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def load_changes(path):
    """Load a changes file: a bare list, or {"changes": [...]}."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("changes") or []
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply requested changes to an InspectablePlan.")
    parser.add_argument("plan", help="Path to the plan JSON file (v1).")
    parser.add_argument("changes", help="Path to the changes JSON file.")
    parser.add_argument("--reviewer", required=True,
                        help="Who asked for the changes (a name).")
    parser.add_argument("--reason", required=True,
                        help="Why these changes are being applied.")
    parser.add_argument("--out", default="",
                        help="Where to write the refined plan "
                             "(default: print it).")
    parser.add_argument("--log", default="refinements.jsonl",
                        help="Refinement log to append to.")
    parser.add_argument("--decisions", default="",
                        help="Optional decisions log (plan-approve format) "
                             "to check the plan was sent back for changes.")
    args = parser.parse_args(argv)

    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)
    changes = load_changes(args.changes)
    prior = read_log(args.decisions) if args.decisions else ()
    try:
        refined, record = refine(
            plan, changes, args.reviewer, args.reason,
            prior_decisions=prior)
    except RefinementRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    append_record(args.log, record)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(refined, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"Refined plan v{refined['version']} written to {args.out}")
    else:
        print(json.dumps(refined, indent=2, sort_keys=True))
    print(f"Refinement recorded: v{record['from_version']} -> "
          f"v{record['to_version']} ({len(changes)} change(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
