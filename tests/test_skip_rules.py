"""Read-only action classifier tests."""

from semarun.kernel.skip_rules import ActionClass, classify_action, skips_full_checkpoint


def test_grep_is_read_only():
    assert classify_action("grep", {"pattern": "foo"}) == ActionClass.READ_ONLY
    assert skips_full_checkpoint("grep", {"pattern": "foo"})


def test_git_diff_is_read_only():
    assert classify_action("git", "diff HEAD~1") == ActionClass.READ_ONLY


def test_explicit_side_effect_forces_recovery():
    assert (
        classify_action("grep", {}, explicit_side_effect="filesystem")
        == ActionClass.RECOVERY_RELEVANT
    )


def test_write_file_is_recovery_relevant():
    assert classify_action("write_file", {"path": "a.txt"}) == ActionClass.RECOVERY_RELEVANT
