import asyncio
import shutil
import subprocess
from typing import List, Optional

from screeninfo import get_monitors
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from models import AndroidApp
from screens import (
    ErrorScreen,
    HelpScreen,
    LoadingScreen,
    NoDeviceScreen,
    UnauthorizedScreen,
)
from utils import clean_app_label

# Bulk-fetch command
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

# Constants
FILTER_PANEL_WIDTH = "28%"


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
        margin: 0 1 0 1;
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
        background: $accent 40%;
        color: $text;
        text-style: bold;
    }

    OptionList > .option-list--option-highlighted {
        background: $accent 40%;
        color: $text;
        text-style: bold;
    }
    """

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
        self.maximized_panel: Optional[str] = None

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

        adb_status, dev_msg = await self.check_adb_device()
        if adb_status == "none":
            self.is_loading = False
            await self.push_screen(NoDeviceScreen(dev_msg))
            return
        elif adb_status == "unauthorized":
            self.is_loading = False
            await self.push_screen(UnauthorizedScreen())
            return

        self.is_loading = True
        await self.push_screen(LoadingScreen())
        self.load_apps()

    async def check_adb_device(self):
        if shutil.which("adb") is None:
            return (
                "none",
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
                "none",
                "The 'adb' binary was not found in PATH.\nPlease install Android platform-tools.",
            )

        lines = stdout.decode(errors="ignore").strip().splitlines()
        devices = []
        unauthorized = False
        for ln in lines[1:]:
            ln = ln.strip()
            if not ln:
                continue
            parts = ln.split()
            if len(parts) >= 2:
                if parts[1] == "device":
                    devices.append(parts[0])
                elif parts[1] == "unauthorized":
                    unauthorized = True

        if unauthorized:
            return "unauthorized", ""
        if not devices:
            return (
                "none",
                "No connected Android devices/emulators were detected.\nConnect a device and enable USB debugging.",
            )
        return "device", ""

    async def check_device_and_proceed(self):
        """Checks for device authorization and proceeds to loading if authorized."""
        if not isinstance(self.screen, UnauthorizedScreen):
            return

        adb_status, dev_msg = await self.check_adb_device()

        if adb_status == "device":
            await self.pop_screen()
            self.is_loading = True
            await self.push_screen(LoadingScreen())
            self.load_apps()
        elif adb_status == "none":
            await self.pop_screen()
            await self.push_screen(NoDeviceScreen(dev_msg))

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
            self.push_screen(
                ErrorScreen(title="ADB Error", message=f"Error running adb: {e}")
            )
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
        await self.apply_filters()

        if apps:
            self.status_text = f"{len(apps)} apps loaded."
        else:
            self.status_text = "No apps found on device."

        self._finish_loading()

    def _finish_loading(self) -> None:
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

    async def apply_filters(self) -> None:
        query = (await self.query_one("#search-input", Input)).value.strip().lower()
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

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search-input":
            return
        await self.apply_filters()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search-input":
            return
        if self.filtered_apps:
            self.launch_scrcpy(self.filtered_apps[0].package)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        pkg = event.row_key.value
        if pkg:
            self.launch_scrcpy(pkg)

    async def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option_list.id != "filter-list":
            return
        option_id = event.option.id
        if option_id in ("all", "user", "system"):
            self.current_filter = option_id
            await self.apply_filters()

    def launch_scrcpy(self, package_id: str) -> None:
        if not package_id or self.is_loading:
            return
        self.status_text = f"Launching {package_id}..."
        self.update_status()
        try:
            self.status_text = f"Starting scrcpy for {package_id}..."
            self.update_status()

            monitors = get_monitors()
            primary_monitor = monitors[0] if monitors else None

            scrcpy_command = [
                "scrcpy",
                "-f",
                "--window-title",
                f"App: {package_id}",
                "--push-target=/sdcard/",
                f"--start-app={package_id}",
            ]
            if primary_monitor:
                dpi = 160
                if primary_monitor.width >= 2560:
                    dpi = 480
                elif primary_monitor.width >= 1920:
                    dpi = 320
                scrcpy_command.append(
                    f"--new-display={primary_monitor.width}x{primary_monitor.height}/{dpi}"
                )

            # For diagnostics, log scrcpy's output.
            with open("scrcpy_launcher.log", "a") as logfile:
                subprocess.Popen(
                    scrcpy_command,
                    stdout=logfile,
                    stderr=logfile,
                    start_new_session=True,
                )

            self.status_text = (
                f"Launched: {package_id}. See scrcpy_launcher.log for details."
            )
            self.update_status()

        except FileNotFoundError:
            self.push_screen(
                ErrorScreen(
                    title="Dependency Error",
                    message="'adb' or 'scrcpy' not found in PATH.",
                )
            )
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode().strip()
            self.push_screen(
                ErrorScreen(
                    title="App Launch Error",
                    message=f"Error starting app on device: {err}",
                )
            )
        except Exception as e:
            self.push_screen(ErrorScreen(title="Unexpected Error", message=str(e)))

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
        if self.is_loading or isinstance(
            self.screen, (HelpScreen, NoDeviceScreen, LoadingScreen)
        ):
            return

        focused = self.screen.focused

        if isinstance(focused, Input):
            if event.key == "escape":
                self.query_one("#apps-table", DataTable).focus()
                self.update_status()
                event.stop()
            return

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
        elif event.key == "j":
            self._move_cursor(down=True)
            event.stop()
        elif event.key == "k":
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


def main():
    """The main entry point for the application."""
    app = ScrcpyLauncher()
    app.run()


if __name__ == "__main__":
    main()
