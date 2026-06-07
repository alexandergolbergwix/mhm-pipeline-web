"""Regression tests for the Markdown report writer.

Pins that ``write_markdown`` tolerates ``overall`` values outside the
displayed full/partial/fail set — ``abstain`` (tier-1 uncertain) and
``verification_failed`` (tier-2 exhaustion) are legitimate verdict
values in gated mode (escalate_on=("abstain","partial")) and must not
crash report generation (the production KeyError fixed in this commit).
"""
from __future__ import annotations

from pathlib import Path

from eval_agent.evaluators._base import Verdict


def _verdict(overall: str, *, evaluator_id: str = "person_ner", sub_type: str = "AUTHOR") -> Verdict:
    return Verdict(
        record_id="r1",
        evaluator_id=evaluator_id,
        sub_type=sub_type,
        candidate_payload={"text": "אברהם"},
        confidence=0.9,
        overall=overall,
        reasoning="x",
    )


def test_abstain_overall_does_not_crash(tmp_path: Path) -> None:
    from eval_agent.report.markdown_report import write_markdown

    out = tmp_path / "report.md"
    write_markdown(out, [_verdict("abstain"), _verdict("full"), _verdict("fail")])
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    # full/partial/fail columns still render; the abstain is counted in total.
    assert "person_ner" in body
    assert "| 3 |" in body  # N=total includes the abstain


def test_verification_failed_and_unknown_overall_do_not_crash(tmp_path: Path) -> None:
    from eval_agent.report.markdown_report import write_markdown

    out = tmp_path / "report.md"
    write_markdown(
        out,
        [_verdict("verification_failed"), _verdict("something_new"), _verdict("partial")],
    )
    assert out.is_file()
