"""全量 fixture 回归：9 条样本的路由结论必须与期望一致。"""

import pytest

from listing_guard.runner import load_fixture, run_audit, verdict_bucket
from listing_guard.settings import DATA_DIR

FIXTURES = sorted(p.stem for p in (DATA_DIR / "fixtures").glob("*.json"))


@pytest.mark.parametrize("case", FIXTURES)
def test_fixture_expected_bucket(case, tmp_path):
    fixture = load_fixture(case)
    result = run_audit(fixture, checkpoint_path=tmp_path / f"{case}.sqlite")
    bucket = verdict_bucket(result["state"].get("verdict", "unknown"))
    assert bucket == fixture["expected"], (
        f"{case}: 期望 {fixture['expected']}，实际 {bucket}（verdict={result['state'].get('verdict')}）"
    )


def test_no_misjudge_on_pass_fixtures(tmp_path):
    """核心红线：优质样本不允许被判 auto_reject（误杀成本高于漏放）。"""
    for case in FIXTURES:
        fixture = load_fixture(case)
        if fixture["expected"] != "pass":
            continue
        result = run_audit(fixture, checkpoint_path=tmp_path / f"{case}.sqlite")
        assert result["state"].get("verdict") != "auto_reject"
