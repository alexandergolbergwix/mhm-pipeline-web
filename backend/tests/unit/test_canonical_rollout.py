from uuid import UUID

from app.settings import Settings


def test_explicit_run_cohort_is_enabled_without_global_flag() -> None:
    run_id = UUID("48ba6c13-115c-4763-bff1-c08b9031b518")
    settings = Settings(
        hmo_canonical_first=False,
        hmo_canonical_first_run_ids=str(run_id),
        hmo_canonical_first_percentage=0,
    )
    assert settings.canonical_first_for_run(run_id)
    assert not settings.canonical_first_for_run(UUID("00000000-0000-0000-0000-000000000000"))


def test_percentage_cohort_is_deterministic() -> None:
    run_id = UUID("48ba6c13-115c-4763-bff1-c08b9031b518")
    settings = Settings(hmo_canonical_first_percentage=100)
    assert settings.canonical_first_for_run(run_id)


def test_global_flag_overrides_cohort() -> None:
    settings = Settings(hmo_canonical_first=True)
    assert settings.canonical_first_for_run("any-run")


def test_legacy_authority_mutations_are_retired_by_default() -> None:
    assert Settings().legacy_authority_mutations_enabled is False
