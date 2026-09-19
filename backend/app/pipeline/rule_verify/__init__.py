"""Rule-based verification — deterministic, non-AI checks with per-rule results.

The engine runs one :class:`Rule` per entity per rule and stores a
:class:`RuleResult` with an explicit state: ``pass`` | ``fail`` |
``not_relevant`` | ``error``. A rule that cannot run (external API down,
missing evidence) reports ``error`` — it never folds into ``fail`` and
never reads as pass (the abstain contract, Rule W-158's reasoning).

All rules are advisory (eval-agent R13 parity): nothing here blocks an
upload or approves an item by itself. A curator chooses which rules are
"blocking" for bulk approval (stored per user); only SHACL keeps its
existing pre-existing blocking gate.
"""
