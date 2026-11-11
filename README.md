# plan-refine

The other half of the approve / request-changes loop: after a human
sends an InspectablePlan back for changes (see
[plan-approve](https://github.com/anubhav9369/plan-approve)), apply
exactly those changes and produce a new version of the same plan —
same id, version bumped by one — so the human can decide again on
the revised plan. Nothing is applied silently and nothing is applied
halfway.

Part of Week 2 (human-in-the-loop approve / reject / refine) of the
trustworthy-agentic-ai prototype for **BRJP26100123 — Building
Trustworthy AI Systems through Explainability and Human-in-the-Loop
Control**. Companion repos:
[trustworthy-agentic-ai](https://github.com/anubhav9369/trustworthy-agentic-ai)
(the InspectablePlan schema),
[plan-explainer](https://github.com/anubhav9369/plan-explainer)
(plain-language rendering + review checklist),
[plan-diff](https://github.com/anubhav9369/plan-diff)
(what changed between two plan versions),
[plan-risk-scan](https://github.com/anubhav9369/plan-risk-scan)
(what needs a human's attention before approval), and
[plan-approve](https://github.com/anubhav9369/plan-approve)
(the human decision point).

## The rules

| Rule | What it stops |
| --- | --- |
| A refinement needs a reviewer and a reason | Anonymous edits nobody can defend later |
| Every change must apply or none does | A half-refined plan where step 1 changed and step 3 silently didn't |
| The plan id never changes; only the version bumps | A revised plan masquerading as a different plan |
| With a decisions log supplied, the latest decision must be `request-changes` | Refining a plan that was approved, rejected, or never sent back |
| Each refinement appends one record to a JSONL log, never edited in place | Losing the trail from v1 to v2 |

## Change operations

`reword-step`, `add-step`, `remove-step`, `gate-step`,
`add-failure-point`, `add-assumption`, `add-constraint`,
`add-risk`, `set-why` — see the module docstring for the exact shape
of each change.

## Usage

```bash
python3 plan_refine.py examples/plan-v1.json examples/changes.json \
  --reviewer Anubhav \
  --reason "Reviewer sent v1 back: the send step was ungated with no failure point." \
  --out examples/plan-v2.json \
  --log examples/refinements.jsonl
```

The example turns v1 into v2: step 3 is reworded to confirm the
recipient first, gated behind human permission, given its failure
point ("Wrong recipient; message sent before the draft is approved"),
and the plan gains the constraint that nothing leaves the system
before human approval. A refusal exits with code 2 and prints why,
leaving the plan untouched.

Python API:

```python
from plan_refine import refine, append_record, read_log

refined, record = refine(plan, changes, "Anubhav",
                         "Reviewer sent v1 back for changes.",
                         prior_decisions=read_log("decisions.jsonl"))
append_record("refinements.jsonl", record)
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

9 tests, all passing.
