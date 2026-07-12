# pip install textual

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from typing import List

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

BULK_FETCH_CMD = (
    "for p in $(pm list packages -3 2>/dev/null | sed 's/package://'); do "
    'l=$(dumpsys package "$p" 2>/dev/null | grep -m1 -oE "applicationLabel=[^ ]+" | sed \'s/applicationLabel=//\'); '
    'if [ -z "$l" ]; then l="$p"; fi; '
    'echo "$p::$l"; '
    "done"
)


@dataclass
class AndroidApp:
    package: str
    label: str


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

    def on_key(self, event) -> None:
        if event.key == "q":
            self.app.exit()


class ScrcpyLauncher(App):
    """A Textual TUI launcher for scrcpy apps."""

    TITLE = "scrcpy App Launcher"

    CSS = """
    Screen {
        background: $background;
    }

    #search-input {
        dock: top;
        margin: 1 2 0 2;
        border: round $accent;
        background: $surface;
    }

    #apps-table {
        margin: 1 2 1 2;
        background: $surface;
        border: round $primary;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text-muted;
        content-align: center middle;
    }

    DataTable > .datatable--cursor {
        background: $accent;
        color: $text;
    }
    """

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+r", "refresh_apps", "Refresh"),
    ]

    def __init__(self):
        super().__init__()
        self.all_apps: List[AndroidApp] = []
        self.filtered_apps: List[AndroidApp] = []
        self.status_text: str = "Loading installed apps..."

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(
            placeholder="🔍 Search apps by name or package...", id="search-input"
        )
        yield DataTable(id="apps-table", cursor_type="row", zebra_stripes=True)
        yield Static(self.status_text, id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.add_columns("App Name", "Package ID")
        self.query_one("#search-input", Input).focus()

        has_device, dev_msg = await self.check_adb_device()
        if not has_device:
            await self.push_screen(NoDeviceScreen(dev_msg))
            return

        self.load_apps()

    async def check_adb_device(self):
        if shutil.which("adb") is None:
            return (
                False,
                "The 'adb' binary was not found in PATH.\nPlease install Android platform-tools.",
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb",
                "devices",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError:
            return (
                False,
                "The 'adb' binary was not found in PATH.\nPlease install Android platform-tools.",
            )

        lines = stdout.decode(errors="ignore").strip().splitlines()
        devices = []
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])

        if not devices:
            return (
                False,
                "No connected Android devices/emulators were detected.\nConnect a device and enable USB debugging.",
            )
        return True, ""

    @work(exclusive=True)
    async def load_apps(self) -> None:
        self.status_text = "Fetching installed apps from device..."
        self.update_status()
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb",
                "shell",
                BULK_FETCH_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except Exception as e:
            self.status_text = f"Error running adb: {e}"
            self.update_status()
            return

        output = stdout.decode(errors="ignore")
        apps: List[AndroidApp] = []
        seen = set()
        for line in output.splitlines():
            line = line.strip()
            if not line or "::" not in line:
                continue
            pkg, _, label = line.partition("::")
            pkg = pkg.strip()
            label = label.strip() or pkg
            if not pkg or pkg in seen:
                continue
            seen.add(pkg)
            apps.append(AndroidApp(package=pkg, label=label))

        apps.sort(key=lambda a: a.label.lower())
        self.all_apps = apps
        self.filtered_apps = apps
        self.populate_table(apps)

        if apps:
            self.status_text = (
                f"{len(apps)} apps loaded. Type to search, Enter to launch."
            )
        else:
            self.status_text = "No third-party apps found on device."
        self.update_status()

    def update_status(self) -> None:
        try:
            status = self.query_one("#status-bar", Static)
            status.update(self.status_text)
        except Exception:
            pass

    def populate_table(self, apps: List[AndroidApp]) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.clear()
        for app in apps:
            table.add_row(app.label, app.package, key=app.package)
        if apps:
            table.move_cursor(row=0)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        query = event.value.strip().lower()
        if not query:
            self.filtered_apps = self.all_apps
        else:
            self.filtered_apps = [
                a
                for a in self.all_apps
                if query in a.label.lower() or query in a.package.lower()
            ]
        self.populate_table(self.filtered_apps)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input":
            return
        if self.filtered_apps:
            self.launch_scrcpy(self.filtered_apps[0].package)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pkg = event.row_key.value
        if pkg:
            self.launch_scrcpy(pkg)

    def launch_scrcpy(self, package_id: str) -> None:
        if not package_id:
            return
        self.status_text = f"Launching {package_id} via scrcpy..."
        self.update_status()
        try:
            subprocess.Popen(
                [
                    "scrcpy",
                    "--new-display=1920x1080/240",
                    f"--start-app={package_id}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.status_text = f"Launched: {package_id}"
        except FileNotFoundError:
            self.status_text = "Error: 'scrcpy' binary not found in PATH."
        except Exception as e:
            self.status_text = f"Error launching scrcpy: {e}"
        self.update_status()

    def action_refresh_apps(self) -> None:
        self.load_apps()


if __name__ == "__main__":
    ScrcpyLauncher().run()
