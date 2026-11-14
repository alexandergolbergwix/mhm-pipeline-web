# Validation contracts

> Up: [Eval Agent](README.md) · Rule W-287

The action registry exposes four actions without a scope filter.
The Publication review action applies only to `selection`.
The `all` scope retains the three authority actions.
A test must check both action identity and scope.
An action count alone cannot prove the scope contract.

The judge endpoint uses `moonshotai/Kimi-K2.5` as the default.
Gemini remains an explicit choice.
The Gemini credential test selects `gemini-3.5-flash` and removes the server fallback key for that test only.
The test also supplies no curator key.
It must check the missing-Gemini-key error rather than the default judge's credentials.
Tests must not change the product default to match an obsolete assertion.

Tests:

- `backend/tests/unit/test_agent_actions.py`
- `backend/tests/integration/test_ai_verify_endpoints.py`
- `backend/tests/test_judge_models_router.py`
- `backend/tests/test_run_job_params_tier_model.py`
