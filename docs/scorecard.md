# Judge scorecard v1

The judge does not collapse unlike concerns into a single reassuring number. A run first passes hard
gates: it has cases, every case passes, no fatal audit finding exists, a reproducibility manifest is
present (including the commit, evaluator-source digest captured before the first request, and an exact
model-artifact digest computed by the evaluator from `--model-file`), and every case contains the declared
set of repetitions. The evaluator also requires the router's loaded model record to expose the same absolute
path; this relies on the local router as part of the trusted runner boundary. With `--baseline`, the scenario groups
must match, the baseline must carry the same trustworthy identity evidence, and no group may regress.
Any failed gate yields `double_check`.

For comparison, it reports an unweighted vector:

- correctness: passing cases divided by total cases;
- fatal findings: malformed calls, empty turns, or token-budget exhaustion;
- repetitions: a reliability prerequisite chosen explicitly with `--minimum-repetitions`;
- completion tokens and turns: efficiency signals, never correctness substitutes;
- duration: operational performance, also never a correctness substitute.

Recommended adoption gate: three repetitions per case, correctness `1.0`, zero fatal findings, and no
regression in any scenario group (`harness judge CANDIDATE --baseline BASELINE --minimum-repetitions 3`).
This is deliberately conservative. Any future threshold or weighting
change requires a new policy version, regression fixtures, a decision-log entry, and human approval.
Scenario files are semantically validated before the first request; unknown expectation fields, tools,
arguments, malformed bounds, and invalid expected argument types fail closed instead of being ignored.
