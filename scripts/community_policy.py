"""Central policy for automatic community verification."""

MIN_DISTINCT_VOTES = 5
MIN_WORKING_VOTES = 5
MIN_WORKING_RATIO = 0.80


def should_promote(*, working: int, partial: int, failed: int) -> bool:
    total = working + partial + failed
    if total < MIN_DISTINCT_VOTES or working < MIN_WORKING_VOTES:
        return False
    return (working / total) >= MIN_WORKING_RATIO
