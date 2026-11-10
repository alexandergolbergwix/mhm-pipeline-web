import pytest

from app.middleware.csrf import _is_exempt_path

RUN = "47e03437-09f2-4d6b-aea7-89fe79d76198"
JOB = "23a407e2-4fca-4e87-a790-3d97b879798c"


def test_modal_completion_webhook_is_exempt() -> None:
    assert _is_exempt_path(f"/api/runs/{RUN}/jobs/{JOB}/modal-event")


@pytest.mark.parametrize(
    "path",
    [
        f"/api/runs/{RUN}/jobs/{JOB}/cancel",
        f"/api/runs/{RUN}/jobs",
        f"/api/runs/{RUN}/jobs/{JOB}/modal-event/extra",
        f"/api/runs/{RUN}/jobs/not-a-uuid/modal-event",
    ],
)
def test_other_run_paths_stay_csrf_protected(path: str) -> None:
    assert not _is_exempt_path(path)
