import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.app import ScrcpyLauncher
from src.models import AndroidApp

@pytest.mark.asyncio
async def test_scrcpy_launcher_initialization():
    app = ScrcpyLauncher()
    assert app.current_filter == "all"
    assert app.is_loading is True

@pytest.mark.asyncio
async def test_apply_filters():
    app = ScrcpyLauncher()
    app.all_apps = [
        AndroidApp("com.user", "User App", "user"),
        AndroidApp("com.sys", "Sys App", "system"),
    ]
    app.current_filter = "user"
    
    # Mocking necessary methods that interact with the UI/table
    input_mock = AsyncMock()
    input_mock.value = ""
    app.query_one = AsyncMock(return_value=input_mock)
    app.update_status = MagicMock()
    app.populate_table = MagicMock()
    
    await app.apply_filters()
    assert len(app.filtered_apps) == 1
    assert app.filtered_apps[0].package == "com.user"
