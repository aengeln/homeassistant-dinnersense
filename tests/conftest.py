import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(request):
    # Tests marked real_recorder must set up the recorder before `hass` exists,
    # so they request enable_custom_integrations themselves, after recorder_mock.
    if not request.node.get_closest_marker("real_recorder"):
        request.getfixturevalue("enable_custom_integrations")
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    # The debounced tips refresh leaves a timer when a test ends without unloading.
    return True
