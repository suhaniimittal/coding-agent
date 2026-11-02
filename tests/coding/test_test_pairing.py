import pytest

from src.coding.models import FilePatch
from src.coding.tdd_models import FileChange
from src.coding.test_pairing import enforce_test_pairs, is_test_path


@pytest.mark.parametrize(
    "path, expected",
    [
        ("src/apps/x/hooks.test.ts", True),
        ("src/apps/x/view.spec.tsx", True),
        ("cypress/e2e/flow.cy.js", True),
        ("src/test/kotlin/com/a/FooTest.kt", True),
        ("src/main/kotlin/com/a/FooTests.java", True),
        ("app/__tests__/a.ts", True),
        ("src/apps/x/hooks.ts", False),
        ("src/main/kotlin/com/a/Contest.kt", False),  # "test" inside a word is not a test file
        ("src/main/kotlin/com/a/TestUtils.kt", False),
        (None, False),
    ],
)
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


def _c(path, symbol="SummaryInformation", change_type="modify"):
    return FileChange(
        file_path=path,
        function_or_symbol=symbol,
        change_type=change_type,
        change_description="d",
        implementation_notes="n",
        pseudocode_sketch="p",
        reasoning="r",
    )


def _p(path):
    return FilePatch("ui", path, "modify", "new", [])


CODE, TEST = "src/x/hooks.ts", "src/x/hooks.test.ts"


def test_case1_code_not_applied_drops_its_test():
    changes = [_c(CODE), _c(TEST)]
    patches = [_p(TEST)]  # only the test was produced

    kept, commit, skipped, warnings = enforce_test_pairs("ui", changes, patches, [_p(TEST)])

    assert kept == [] and commit == []
    assert skipped[0].change.file_path == TEST and "was not applied" in skipped[0].reason
    assert warnings == []


def test_case2_code_applied_test_missing_keeps_code_and_warns():
    changes = [_c(CODE), _c(TEST)]

    kept, commit, skipped, warnings = enforce_test_pairs("ui", changes, [_p(CODE)], [_p(CODE)])

    assert [p.file_path for p in commit] == [CODE]
    assert skipped == []
    assert len(warnings) == 1 and TEST in warnings[0] and "will likely fail" in warnings[0]


def test_both_applied_is_silent():
    changes = [_c(CODE), _c(TEST)]
    both = [_p(CODE), _p(TEST)]

    kept, commit, skipped, warnings = enforce_test_pairs("ui", changes, both, both)

    assert len(commit) == 2 and skipped == [] and warnings == []


def test_unpaired_test_and_reference_are_left_alone():
    changes = [_c(TEST, symbol="Other"), _c(CODE), _c("src/x/helper.ts", change_type="reference")]
    patches = [_p(TEST), _p(CODE)]

    kept, commit, skipped, warnings = enforce_test_pairs("ui", changes, patches, patches)

    assert len(commit) == 2 and skipped == [] and warnings == []


def test_code_without_a_path_counts_as_not_applied():
    changes = [_c(None), _c(TEST)]

    kept, commit, skipped, warnings = enforce_test_pairs("ui", changes, [_p(TEST)], [_p(TEST)])

    assert commit == [] and "<no path>" in skipped[0].reason
