from textual import events
from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen, Screen
from textual.widgets import LoadingIndicator, Static
from bindings import KEYBINDINGS

class LoadingScreen(ModalScreen):
    """Blocking modal shown while apps are being fetched from the device."""
    CSS = """
    LoadingScreen {
        align: center middle;
        background: $surface 60%;
    }
    #loading-box {
        width: 44;
        height: auto;
        border: heavy $accent;
        background: $panel;
        padding: 1 2;
        align: center middle;
    }
    #loading-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    LoadingIndicator {
        height: 3;
    }
    #loading-msg {
        content-align: center middle;
        width: 100%;
        color: $text-muted;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="loading-box"):
            yield Static("⏳  Fetching Installed Apps", id="loading-title")
            yield LoadingIndicator()
            yield Static("Please wait, this may take a moment...", id="loading-msg")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()


class HelpScreen(ModalScreen):
    """Modal screen listing all keybindings."""
    CSS = """
    HelpScreen {
        align: center middle;
    }
    #help-box {
        width: 64;
        height: auto;
        max-height: 80%;
        border: heavy $accent;
        background: $panel;
        padding: 1 2;
    }
    #help-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    .help-row {
        width: 100%;
    }
    #help-footer {
        margin-top: 1;
        content-align: center middle;
        width: 100%;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        with Container(id="help-box"):
            yield Static("⌨  Keybindings", id="help-title")
            for key, desc in KEYBINDINGS:
                yield Static(f"[bold $accent]{key:<10}[/] {desc}", classes="help-row")
            yield Static("\nPress Esc, ?, or Enter to close", id="help-footer")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "question_mark", "enter"):
            self.dismiss()
        event.stop()


class NoDeviceScreen(Screen):
    """Error screen shown when no adb device is connected."""
    CSS = """
    NoDeviceScreen {
        align: center middle;
        background: $surface;
    }
    #error-box {
        width: 64;
        height: auto;
        border: heavy $error;
        padding: 2 4;
        background: $panel;
        color: $text;
    }
    #error-title {
        text-style: bold;
        color: $error;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    #error-msg, #error-msg2 {
        content-align: center middle;
        width: 100%;
    }
    """

    def __init__(self, message: str):
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Container(id="error-box"):
            yield Static("⚠  No ADB Device Found", id="error-title")
            yield Static(self.message, id="error-msg")
            yield Static("\nPress 'q' to quit.", id="error-msg2")

    def on_key(self, event: events.Key) -> None:
        if event.key == "q":
            self.app.exit()
        event.stop()


class UnauthorizedScreen(Screen):
    """Screen shown when an ADB device is connected but not authorized."""
    CSS = """
    UnauthorizedScreen {
        align: center middle;
        background: $surface;
    }
    #error-box {
        width: 64;
        height: auto;
        border: heavy $warning;
        padding: 2 4;
        background: $panel;
        color: $text;
    }
    #error-title {
        text-style: bold;
        color: $warning;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    #error-msg {
        content-align: center middle;
        width: 100%;
    }
    """
    def compose(self) -> ComposeResult:
        with Container(id="error-box"):
            yield Static("⚠  Device Unauthorized", id="error-title")
            yield Static("Please accept the fingerprint prompt on your Android device.", id="error-msg")

    def on_mount(self) -> None:
        """Start polling for device status when the screen is mounted."""
        self.set_interval(1, self.check_status)

    async def check_status(self) -> None:
        """Check the device status and proceed if authorized."""
        if self.app.is_running:
            await self.app.check_device_and_proceed()

    def on_key(self, event: events.Key) -> None:
        if event.key == "q":
            self.app.exit()
        event.stop()


class ErrorScreen(ModalScreen):
    """Modal screen to display a generic error message."""

    CSS = """
    ErrorScreen {
        align: center middle;
    }
    #error-popup {
        width: 64;
        height: auto;
        max-height: 80%;
        border: heavy $error;
        background: $panel;
        padding: 1 2;
    }
    #error-popup-title {
        text-style: bold;
        color: $error;
        content-align: center middle;
        width: 100%;
        margin-bottom: 1;
    }
    #error-popup-message {
        width: 100%;
        content-align: center middle;
    }
    #error-popup-footer {
        margin-top: 1;
        content-align: center middle;
        width: 100%;
        color: $text-muted;
    }
    """

    def __init__(self, title: str, message: str):
        super().__init__()
        self.error_title = title
        self.error_message = message

    def compose(self) -> ComposeResult:
        with Container(id="error-popup"):
            yield Static(f"⚠  {self.error_title}", id="error-popup-title")
            yield Static(self.error_message, id="error-popup-message")
            yield Static("\nPress Esc or Enter to close", id="error-popup-footer")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "enter"):
            self.dismiss()
        event.stop()
