# pip install textual

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

BULK_FETCH_CMD = (
    "sys_pkgs=$(pm list packages -s 2>/dev/null | sed 's/package://'); "
    "user_pkgs=$(pm list packages -3 2>/dev/null | sed 's/package://'); "
    "for p in $sys_pkgs; do "
    'l=$(dumpsys package "$p" 2>/dev/null | grep -m1 -oE "applicationLabel=[^ ]+" | sed \'s/applicationLabel=//\'); '
    'if [ -z "$l" ]; then l="$p"; fi; '
    'echo "SYS::$p::$l"; '
    "done; "
    "for p in $user_pkgs; do "
    'l=$(dumpsys package "$p" 2>/dev/null | grep -m1 -oE "applicationLabel=[^ ]+" | sed \'s/applicationLabel=//\'); '
    'if [ -z "$l" ]; then l="$p"; fi; '
    'echo "USR::$p::$l"; '
    "done"
)

KEYBINDINGS = [
    ("?", "Show this help screen"),
    ("i  or  /", "Enter INSERT mode (focus search bar)"),
    ("Escape", "Exit INSERT mode, return to NORMAL mode"),
    ("h", "Focus the filter panel (left)"),
    ("l", "Focus the app list (right)"),
    ("j", "Move cursor down"),
    ("k", "Move cursor up"),
    ("g", "Jump to top of list"),
    ("G", "Jump to bottom of list"),
    ("Enter", "Launch selected app / apply selected filter"),
    ("Ctrl+R", "Refresh app list from device"),
    ("q", "Quit (NORMAL mode)"),
    ("Ctrl+Q", "Quit"),
]


@dataclass
class AndroidApp:
    package: str
    label: str
    app_type: str  # "user" or "system"


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
        if event.key in ("escape", "question_mark", "enter", "?"):
            self.dismiss()


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


class ScrcpyLauncher(App):
    """A Textual TUI launcher for scrcpy apps with vim-style navigation."""

    TITLE = "scrcpy App Launcher"

    CSS = """
    Screen {
        background: $background;
    }

    #body {
        layout: horizontal;
        height: 1fr;
    }

    #filter-panel {
        width: 28%;
        min-width: 20;
        margin: 1 0 1 2;
        border: round $primary;
        padding: 0 1;
    }

    #filter-title {
        text-style: bold;
        color: $accent;
        content-align: center middle;
        width: 100%;
        height: 1;
        margin-bottom: 1;
    }

    #filter-list {
        height: 1fr;
        background: $surface;
    }

    #filter-list:focus {
        border: round $accent;
    }

    #main-panel {
        width: 1fr;
        height: 1fr;
    }

    #search-input {
        margin: 1 2 0 2;
        border: round $primary;
        background: $surface;
    }

    #search-input:focus {
        border: round $accent;
    }

    #apps-table {
        margin: 1 2 1 2;
        background: $surface;
        border: round $primary;
    }

    #apps-table:focus {
        border: round $accent;
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

    OptionList > .option-list--option-highlighted {
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
        self.current_filter: str = "all"
        self.mode: str = "insert"
        self.status_text: str = "Loading installed apps..."

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="filter-panel"):
                yield Static("Filter", id="filter-title")
                yield OptionList(
                    Option("All Apps", id="all"),
                    Option("User Apps", id="user"),
                    Option("System Apps", id="system"),
                    id="filter-list",
                )
            with Vertical(id="main-panel"):
                yield Input(
                    placeholder="🔍 Search apps by name or package...",
                    id="search-input",
                )
                yield DataTable(id="apps-table", cursor_type="row", zebra_stripes=True)
        yield Static(self.status_text, id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.add_columns("App Name", "Package ID")

        filter_list = self.query_one("#filter-list", OptionList)
        filter_list.highlighted = 0

        self.query_one("#search-input", Input).focus()
        self.mode = "insert"

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
            parts = line.split("::", 2)
            if len(parts) != 3:
                continue
            tag, pkg, label = parts
            pkg = pkg.strip()
            label = label.strip() or pkg
            if not pkg or pkg in seen:
                continue
            seen.add(pkg)
            app_type = "system" if tag == "SYS" else "user"
            apps.append(AndroidApp(package=pkg, label=label, app_type=app_type))

        apps.sort(key=lambda a: a.label.lower())
        self.all_apps = apps
        self.apply_filters()

        if apps:
            self.status_text = f"{len(apps)} apps loaded."
        else:
            self.status_text = "No apps found on device."
        self.update_status()

    def update_status(self) -> None:
        try:
            status = self.query_one("#status-bar", Static)
            mode_label = "-- INSERT --" if self.mode == "insert" else "-- NORMAL --"
            filter_label = {"all": "All", "user": "User", "system": "System"}.get(
                self.current_filter, "All"
            )
            count = len(self.filtered_apps)
            status.update(
                f"{mode_label}  |  Filter: {filter_label}  |  {count} apps  |  {self.status_text}  |  Press ? for help"
            )
        except Exception:
            pass

    def populate_table(self, apps: List[AndroidApp]) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.clear()
        for app in apps:
            table.add_row(app.label, app.package, key=app.package)
        if apps:
            table.move_cursor(row=0)

    def apply_filters(self) -> None:
        query = self.query_one("#search-input", Input).value.strip().lower()
        apps = self.all_apps

        if self.current_filter == "user":
            apps = [a for a in apps if a.app_type == "user"]
        elif self.current_filter == "system":
            apps = [a for a in apps if a.app_type == "system"]

        if query:
            apps = [
                a
                for a in apps
                if query in a.label.lower() or query in a.package.lower()
            ]

        self.filtered_apps = apps
        self.populate_table(apps)
        self.update_status()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        self.apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input":
            return
        if self.filtered_apps:
            self.launch_scrcpy(self.filtered_apps[0].package)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pkg = event.row_key.value
        if pkg:
            self.launch_scrcpy(pkg)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "filter-list":
            return
        option_id = event.option.id
        if option_id in ("all", "user", "system"):
            self.current_filter = option_id
            self.apply_filters()

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

    def _goto_edge(self, top: bool) -> None:
        focused = self.focused
        if isinstance(focused, DataTable):
            if focused.row_count:
                row = 0 if top else focused.row_count - 1
                focused.move_cursor(row=row)
        elif isinstance(focused, OptionList):
            try:
                if top and hasattr(focused, "action_first"):
                    focused.action_first()
                elif not top and hasattr(focused, "action_last"):
                    focused.action_last()
            except Exception:
                pass

    def _move_cursor(self, down: bool) -> None:
        focused = self.focused
        try:
            if isinstance(focused, DataTable):
                if down and hasattr(focused, "action_cursor_down"):
                    focused.action_cursor_down()
                elif not down and hasattr(focused, "action_cursor_up"):
                    focused.action_cursor_up()
            elif isinstance(focused, OptionList):
                if down and hasattr(focused, "action_cursor_down"):
                    focused.action_cursor_down()
                elif not down and hasattr(focused, "action_cursor_up"):
                    focused.action_cursor_up()
        except Exception:
            pass

    async def on_key(self, event: events.Key) -> None:
        # Modal screens (help / no-device) handle their own keys.
        if isinstance(self.screen, (HelpScreen, NoDeviceScreen)):
            return

        if self.mode == "insert":
            if event.key == "escape":
                self.mode = "normal"
                table = self.query_one("#apps-table", DataTable)
                table.focus()
                self.update_status()
                event.stop()
            return

        # NORMAL mode vim-style bindings
        if event.character == "?":
            await self.push_screen(HelpScreen())
            event.stop()
        elif event.key in ("i", "slash"):
            self.mode = "insert"
            self.query_one("#search-input", Input).focus()
            self.update_status()
            event.stop()
        elif event.key == "h":
            self.query_one("#filter-list", OptionList).focus()
            self.update_status()
            event.stop()
        elif event.key == "l":
            self.query_one("#apps-table", DataTable).focus()
            self.update_status()
            event.stop()
        elif event.key == "j":
            self._move_cursor(down=True)
            event.stop()
        elif event.key == "k":
            self._move_cursor(down=False)
            event.stop()
        elif event.character == "g":
            self._goto_edge(top=True)
            event.stop()
        elif event.character == "G":
            self._goto_edge(top=False)
            event.stop()
        elif event.key == "q":
            self.exit()
            event.stop()


if __name__ == "__main__":
    ScrcpyLauncher().run()
