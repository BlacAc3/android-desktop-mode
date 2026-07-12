# pip install textual

import asyncio
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

# Bulk-fetch package list + raw label text in a single adb shell round-trip.
# Captures the FULL nonLocalizedLabel value (up to the next known dumpsys
# field) instead of truncating at the first space, so multi-word app names
# (e.g. "Google Play Services") survive intact.
BULK_FETCH_CMD = (
    "sys_pkgs=$(pm list packages -s 2>/dev/null | sed 's/package://'); "
    "user_pkgs=$(pm list packages -3 2>/dev/null | sed 's/package://'); "
    "extract() { "
    '  dumpsys package "$1" 2>/dev/null '
    '  | grep -m1 -oE "nonLocalizedLabel=.*" '
    "  | sed -E 's/nonLocalizedLabel=//; s/ (icon|labelRes|banner|logo|theme|flags|dataDir)=.*$//'; "
    "}; "
    'for p in $sys_pkgs; do l=$(extract "$p"); [ -z "$l" ] && l="$p"; echo "SYS::$p::$l"; done; '
    'for p in $user_pkgs; do l=$(extract "$p"); [ -z "$l" ] && l="$p"; echo "USR::$p::$l"; done'
)

# Display resolution/DPI target derived from the host panel's reported
# aspect: 1920x1080 @ 1.32x scale on a 14" display. Standard Android baseline
# density is 160dpi; scaling that by the reported 1.32x factor gives a more
# faithful effective density than an arbitrary flat value.
SCRCPY_WIDTH = 1920
SCRCPY_HEIGHT = 1080
SCRCPY_SCALE_FACTOR = 1.32
SCRCPY_BASE_DPI = 160
SCRCPY_DPI = round(SCRCPY_BASE_DPI * SCRCPY_SCALE_FACTOR)  # ≈ 211

FILTER_PANEL_WIDTH = "28%"

# Segments treated as noise when deriving a friendly name from a bare
# package id (i.e. when the device gave us no usable label at all).
_PACKAGE_JUNK_SEGMENTS = {
    "com",
    "org",
    "net",
    "io",
    "co",
    "app",
    "apps",
    "android",
    "inc",
    "corp",
    "ltd",
    "mobile",
    "client",
    "prod",
    "release",
}

KEYBINDINGS = [
    ("?", "Show this help screen"),
    ("/", "Jump to the search bar (INSERT mode)"),
    ("i", "Enter INSERT mode (jump to search bar)"),
    ("Escape", "Exit search bar, return focus to app list"),
    ("h", "Focus the filter panel (left)"),
    ("l", "Focus the app list (right)"),
    ("j  /  Down", "Move cursor down (filter or app list)"),
    ("k  /  Up", "Move cursor up (filter or app list)"),
    ("g", "Jump to top of list"),
    ("G", "Jump to bottom of list"),
    ("m", "Maximize / restore the focused panel"),
    ("Enter", "Launch selected app / apply selected filter"),
    ("Ctrl+R", "Refresh app list from device"),
    ("q", "Quit (when not typing in search)"),
    ("Ctrl+C", "Quit"),
]


def _friendly_from_package(package: str) -> str:
    """Derive a readable name from a bare package id when no label exists."""
    raw_parts = [p for p in package.split(".") if p]
    parts = [p for p in raw_parts if p.lower() not in _PACKAGE_JUNK_SEGMENTS]
    if not parts:
        parts = raw_parts or [package]
    name_parts = parts[-2:] if len(parts) >= 2 else parts
    name = " ".join(name_parts)
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)  # split camelCase
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() if name else package


def clean_app_label(raw_label: str, package: str) -> str:
    """Strip dumpsys/system jargon from a raw label and format it cleanly."""
    label = (raw_label or "").strip()

    # Strip surrounding quotes/braces artifacts dumpsys sometimes leaves in.
    label = label.strip("\"'")
    label = re.sub(r"\{.*?\}", "", label).strip()
    label = label.strip(" {}();,")

    if not label or label.lower() in ("null", "none") or label == package:
        return _friendly_from_package(package)

    # Collapse stray whitespace and title-case single ALLCAPS/underscored
    # tokens (e.g. "SOME_APP_NAME" -> "Some App Name"), leave normal mixed
    # case labels (e.g. "WhatsApp") untouched.
    label = re.sub(r"\s+", " ", label).strip()
    if label.isupper() or "_" in label:
        label = re.sub(r"[_]+", " ", label).strip()
        label = label.title()

    return label or _friendly_from_package(package)


@dataclass
class AndroidApp:
    package: str
    label: str
    app_type: str  # "user" or "system"


class LoadingScreen(ModalScreen):
    """Blocking modal shown while apps are being fetched from the device.
    Swallows all key input so the user cannot interact until loading ends.
    """

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
        # Absorb all key presses; nothing is actionable while loading.
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

    # Global bindings also drive the visible Footer hints. These only fire
    # when the currently focused widget doesn't itself consume the key —
    # typing "/" or "?" while the search Input is focused is handled by the
    # Input's own character-insertion logic and never reaches these.
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+r", "refresh_apps", "Refresh"),
        ("question_mark", "show_help", "Help"),
        ("slash", "focus_search", "Search"),
        ("m", "toggle_maximize", "Maximize"),
    ]

    def __init__(self):
        super().__init__()
        self.all_apps: List[AndroidApp] = []
        self.filtered_apps: List[AndroidApp] = []
        self.current_filter: str = "all"
        self.status_text: str = "Loading installed apps..."
        self.is_loading: bool = True
        self.maximized_panel: Optional[str] = None  # "filter" | "apps" | None

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

        has_device, dev_msg = await self.check_adb_device()
        if not has_device:
            self.is_loading = False
            await self.push_screen(NoDeviceScreen(dev_msg))
            return

        # Block all interaction with a modal loading overlay until the
        # bulk adb fetch/parse completes.
        self.is_loading = True
        await self.push_screen(LoadingScreen())
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
            self._finish_loading()
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
            tag, pkg, raw_label = parts
            pkg = pkg.strip()
            if not pkg or pkg in seen:
                continue
            seen.add(pkg)
            app_type = "system" if tag == "SYS" else "user"
            label = clean_app_label(raw_label, pkg)
            apps.append(AndroidApp(package=pkg, label=label, app_type=app_type))

        apps.sort(key=lambda a: a.label.lower())
        self.all_apps = apps
        self.apply_filters()

        if apps:
            self.status_text = f"{len(apps)} apps loaded."
        else:
            self.status_text = "No apps found on device."

        self._finish_loading()

    def _finish_loading(self) -> None:
        """Dismiss the loading overlay and restore normal interaction."""
        self.is_loading = False
        if isinstance(self.screen, LoadingScreen):
            self.pop_screen()
        try:
            self.query_one("#search-input", Input).focus()
        except Exception:
            pass
        self.update_status()

    def _current_mode_label(self) -> str:
        try:
            focused = self.screen.focused
        except Exception:
            focused = None
        return "-- INSERT --" if isinstance(focused, Input) else "-- NORMAL --"

    def update_status(self) -> None:
        try:
            status = self.query_one("#status-bar", Static)
            mode_label = self._current_mode_label()
            filter_label = {"all": "All", "user": "User", "system": "System"}.get(
                self.current_filter, "All"
            )
            count = len(self.filtered_apps)
            max_label = (
                f"  |  Maximized: {self.maximized_panel}"
                if self.maximized_panel
                else ""
            )
            status.update(
                f"{mode_label}  |  Filter: {filter_label}  |  {count} apps  |  {self.status_text}{max_label}  |  Press ? for help"
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
        if not package_id or self.is_loading:
            return
        self.status_text = f"Launching {package_id} via scrcpy..."
        self.update_status()
        try:
            subprocess.Popen(
                [
                    "scrcpy",
                    f"--new-display={SCRCPY_WIDTH}x{SCRCPY_HEIGHT}/{SCRCPY_DPI}",
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
        if self.is_loading:
            return
        self.is_loading = True
        self.status_text = "Refreshing app list..."
        self.push_screen(LoadingScreen())
        self.load_apps()

    async def action_show_help(self) -> None:
        if self.is_loading:
            return
        await self.push_screen(HelpScreen())

    def action_focus_search(self) -> None:
        if self.is_loading:
            return
        self.query_one("#search-input", Input).focus()
        self.update_status()

    def action_toggle_maximize(self) -> None:
        if self.is_loading or isinstance(
            self.screen, (HelpScreen, NoDeviceScreen, LoadingScreen)
        ):
            return

        filter_panel = self.query_one("#filter-panel")
        main_panel = self.query_one("#main-panel")

        if self.maximized_panel is None:
            focused = self.screen.focused
            if isinstance(focused, OptionList) and focused.id == "filter-list":
                target = "filter"
            elif isinstance(focused, DataTable) and focused.id == "apps-table":
                target = "apps"
            else:
                # Nothing sensible to maximize (e.g. search input focused).
                return

            self.maximized_panel = target
            if target == "filter":
                main_panel.styles.display = "none"
                filter_panel.styles.width = "100%"
            else:
                filter_panel.styles.display = "none"
            self.status_text = f"Maximized {'filter panel' if target == 'filter' else 'app list'} (press m to restore)"
        else:
            main_panel.styles.display = "block"
            filter_panel.styles.display = "block"
            filter_panel.styles.width = FILTER_PANEL_WIDTH
            self.maximized_panel = None
            self.status_text = "Restored panel layout"

        self.update_status()

    def _get_focused_navigable(self):
        """Return the currently focused DataTable/OptionList, if any."""
        widget = self.screen.focused
        if isinstance(widget, (DataTable, OptionList)):
            return widget
        return None

    def _goto_edge(self, top: bool) -> None:
        widget = self._get_focused_navigable()
        if widget is None:
            return
        if isinstance(widget, DataTable):
            if widget.row_count:
                row = 0 if top else widget.row_count - 1
                widget.move_cursor(row=row)
        elif isinstance(widget, OptionList):
            if top:
                widget.action_first()
            else:
                widget.action_last()

    def _move_cursor(self, down: bool) -> None:
        widget = self._get_focused_navigable()
        if widget is None:
            return
        if down:
            widget.action_cursor_down()
        else:
            widget.action_cursor_up()

    async def on_key(self, event: events.Key) -> None:
        # While loading, or on modal screens, absorb navigation input here.
        if self.is_loading or isinstance(
            self.screen, (HelpScreen, NoDeviceScreen, LoadingScreen)
        ):
            return

        # Determine mode directly from actual focus (not a stale flag) so
        # navigation keys can never desync from what's really focused.
        focused = self.screen.focused

        if isinstance(focused, Input):
            if event.key == "escape":
                self.query_one("#apps-table", DataTable).focus()
                self.update_status()
                event.stop()
            return

        # Focus is NOT on the search Input: vim-style bindings are always
        # live here, regardless of prior key history.
        if event.key == "i":
            self.action_focus_search()
            event.stop()
        elif event.key == "h":
            self.query_one("#filter-list", OptionList).focus()
            self.update_status()
            event.stop()
        elif event.key == "l":
            self.query_one("#apps-table", DataTable).focus()
            self.update_status()
            event.stop()
        elif event.key in ("j", "down"):
            self._move_cursor(down=True)
            event.stop()
        elif event.key in ("k", "up"):
            self._move_cursor(down=False)
            event.stop()
        elif event.key == "g":
            self._goto_edge(top=True)
            event.stop()
        elif event.key == "G":
            self._goto_edge(top=False)
            event.stop()
        elif event.key == "m":
            self.action_toggle_maximize()
            event.stop()
        elif event.key == "q":
            self.exit()
            event.stop()


if __name__ == "__main__":
    ScrcpyLauncher().run()
