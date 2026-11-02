import logging

from src.coding.models import SkippedChange
from src.coding.tdd_models import FileChange, ServicePlan, TechnicalDesignDoc
from src.tools import coding_tools


def _change(path):
    return FileChange(
        file_path=path,
        function_or_symbol="f",
        change_description="d",
        implementation_notes="n",
        pseudocode_sketch="p",
        reasoning="r",
    )


def test_log_tdd_lists_each_service_with_its_file_paths(caplog):
    tdd = TechnicalDesignDoc(
        title="Fix phone trim",
        issue_summary="s",
        services=[
            ServicePlan(service="ui", changes=[_change("src/a.ts"), _change(None)]),
            ServicePlan(service="candidate-service", changes=[_change("B.kt")]),
        ],
    )
    with caplog.at_level(logging.INFO, logger=coding_tools.logger.name):
        coding_tools._log_tdd(tdd)

    text = caplog.text
    assert "2 service(s)" in text
    assert "ui: 2 change(s) -> src/a.ts, <no path>" in text
    assert "candidate-service: 1 change(s) -> B.kt" in text


def test_log_outcome_warns_for_every_skipped_change_with_reason(caplog):
    result = {
        "branches": [
            {"service": "candidate-service", "branch_url": "https://h/tree/sm/KS-1", "pr_url": None}
        ],
        "skipped": [SkippedChange("ui", _change("src/a.ts"), "service not found in the graph")],
    }
    with caplog.at_level(logging.INFO, logger=coding_tools.logger.name):
        coding_tools._log_outcome(result)

    assert "1 branch(es) pushed, 1 change(s) skipped" in caplog.text
    assert (
        "Branch pushed for candidate-service" in caplog.text
        and "https://h/tree/sm/KS-1" in caplog.text
    )
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "SKIPPED ui src/a.ts — service not found in the graph" in warnings[0].getMessage()
