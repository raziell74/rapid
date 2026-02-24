import mobase
import os
import queue
import struct
import threading
import time
import zlib
from collections import Counter
from typing import List

from mobase.widgets import TaskDialog, TaskDialogButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

HOOK_PLUGIN_NAME = "RAPID - Pre-Launch Game Hook"
CACHE_FILENAME = "rapid_vfs_cache.bin"

EXCLUDED_EXTENSIONS = (
    '.esp', '.esm', '.esl',                                  # Plugins (load order)
    '.bsa', '.ba2',                                          # Archives (mounted separately)
    '.exe', '.dll', '.asi',                                  # Executables / code
    '.skse',                                                 # Plugin metadata
    '.pdb', '.cdx',                                          # Debug / build
    '.md', '.pdf',                                           # Documentation
    '.bak', '.tmp', '.temp', '.orig',                        # Backup / temp
    '.log',                                                  # Logs
    '.gitignore', '.gitattributes',                          # Version control / IDE
    '.manifest', '.url', '.lnk',                             # Installer / shortcuts
)

def get_rapid_cache_path(organizer: mobase.IOrganizer, settings_plugin_name: str) -> str:
    """Resolve the cache file path from the output_to_mod setting (Overwrite or a mod name)."""
    raw = organizer.pluginSetting(settings_plugin_name, "output_to_mod")
    if raw is None:
        raw = ""
    value = (raw or "").strip()
    if not value or value.lower() == "overwrite" or value == "__overwrite__":
        base_dir = organizer.overwritePath()
    else:
        mod_list = organizer.modList()
        mod = mod_list.getMod(value)
        if mod is not None:
            base_dir = mod.absolutePath()
        else:
            base_dir = None
            for internal_name in mod_list.allMods():
                if mod_list.displayName(internal_name) == value:
                    mod = mod_list.getMod(internal_name)
                    if mod is not None:
                        base_dir = mod.absolutePath()
                    break
            if base_dir is None:
                print(f"RAPID: unknown output mod {value!r}, using Overwrite.")
                base_dir = organizer.overwritePath()
    return os.path.join(base_dir, CACHE_FILENAME)


def _get_excluded_extensions_for_settings(organizer: mobase.IOrganizer, settings_plugin_name: str) -> frozenset[str]:
    excluded: set[str] = set(EXCLUDED_EXTENSIONS)
    raw = organizer.pluginSetting(settings_plugin_name, "extension_blacklist")
    if raw and raw.strip():
        for part in raw.split(","):
            ext = part.strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = "." + ext
            excluded.add(ext)
    return frozenset(excluded)


def _create_progress_dialog() -> QProgressDialog:
    dialog = QProgressDialog("Scanning virtual file system…", "Cancel", 0, 1)
    dialog.setWindowTitle("RAPID - Indexing Loose Files")
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    dialog.setWindowFlags(dialog.windowFlags() | Qt.WindowType.Dialog)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumWidth(440)
    dialog.setMinimumHeight(140)
    dialog.setStyleSheet(
        "QProgressDialog { padding: 14px; }\n"
        "QProgressBar { min-height: 10px; }"
    )
    return dialog


def _update_progress_dialog(
    dialog: QProgressDialog,
    label_text: str,
    value: int,
    maximum: int,
    *,
    indeterminate: bool = False,
) -> bool:
    dialog.setLabelText(label_text)
    if indeterminate:
        dialog.setMinimum(0)
        dialog.setMaximum(0)
        dialog.setValue(0)
    else:
        maximum = max(1, maximum)
        value = max(0, min(value, maximum))
        dialog.setMinimum(0)
        if dialog.maximum() != maximum:
            dialog.setMaximum(maximum)
        dialog.setValue(value)
    QApplication.processEvents()
    return dialog.wasCanceled()


def _prompt_continue_without_rapid(errors: list[str]) -> bool:
    details = "\n".join(errors[:10])
    if len(errors) > 10:
        details += f"\n...and {len(errors) - 10} more error(s)."
    dialog = TaskDialog(
        title="RAPID Indexing Error",
        main="RAPID failed to build a complete VFS cache.",
        content="Continue launching without RAPID performance gains?",
        details=details,
        icon=QMessageBox.Icon.Warning
    )
    dialog.addButton(TaskDialogButton("Yes", QMessageBox.StandardButton.Yes))
    dialog.addButton(TaskDialogButton("No", QMessageBox.StandardButton.No))
    result = dialog.exec()
    return result == QMessageBox.StandardButton.Yes


def run_index_vfs(organizer: mobase.IOrganizer, settings_plugin_name: str) -> bool:
    """Run VFS indexing and write rapid_vfs_cache.bin to the configured output (Overwrite or named mod)."""
    vfs_tree = organizer.virtualFileTree()
    excluded_extensions = _get_excluded_extensions_for_settings(organizer, settings_plugin_name)

    dir_queue = queue.Queue()
    root_files = []
    for entry in vfs_tree:
        if entry.isDir():
            dir_queue.put(entry)
        else:
            ext = os.path.splitext(entry.name())[1].lower()
            if ext not in excluded_extensions:
                root_files.append(entry.path('\\'))

    cpu_count = os.cpu_count() or 4
    configured = max(1, min(int(organizer.pluginSetting(settings_plugin_name, "worker_threads")), cpu_count))
    worker_count = min(dir_queue.qsize(), configured)

    all_batches = [root_files]
    lock = threading.Lock()
    errors = []
    error_lock = threading.Lock()
    progress_lock = threading.Lock()
    cancel_event = threading.Event()

    discovered_dirs = dir_queue.qsize()
    processed_dirs = 0

    def worker():
        nonlocal discovered_dirs, processed_dirs
        local_paths = []
        while True:
            try:
                node = dir_queue.get(timeout=0.1)
            except queue.Empty:
                if cancel_event.is_set():
                    break
                continue
            try:
                if node is None:
                    break
                if cancel_event.is_set():
                    continue
                with progress_lock:
                    processed_dirs += 1
                for entry in node:
                    if cancel_event.is_set():
                        break
                    if entry.isDir():
                        dir_queue.put(entry)
                        with progress_lock:
                            discovered_dirs += 1
                    else:
                        ext = os.path.splitext(entry.name())[1].lower()
                        if ext not in excluded_extensions:
                            local_paths.append(entry.path('\\'))
            except Exception as e:
                error_message = f"Worker failed while indexing VFS node: {e!r}"
                print(f"RAPID worker error while indexing VFS: {e!r}")
                with error_lock:
                    errors.append(error_message)
            finally:
                dir_queue.task_done()
        with lock:
            all_batches.append(local_paths)

    progress_dialog = _create_progress_dialog()
    progress_dialog.show()
    QApplication.processEvents()
    if progress_dialog.wasCanceled():
        print("RAPID indexing canceled by user before start; launching without RAPID cache.")
        progress_dialog.close()
        return True

    try:
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(worker_count)]
        for t in threads:
            t.start()

        last_update = 0.0
        while True:
            now = time.monotonic()
            if now - last_update >= 0.05:
                with progress_lock:
                    current_processed = processed_dirs
                    current_discovered = discovered_dirs
                if current_discovered == 0:
                    label = "Scanning virtual file system"
                    use_indeterminate = True
                else:
                    total = max(1, current_discovered)
                    pct = (100 * current_processed) // total
                    label = (
                        "Scanning virtual file system\n"
                        f"{current_processed:,} / {current_discovered:,} directories ({pct}%)"
                    )
                    use_indeterminate = False
                if _update_progress_dialog(
                    progress_dialog,
                    label,
                    current_processed,
                    current_discovered,
                    indeterminate=use_indeterminate,
                ):
                    cancel_event.set()
                last_update = now

            if dir_queue.unfinished_tasks == 0:
                break

            if cancel_event.wait(0.01):
                continue

        for _ in range(worker_count):
            dir_queue.put(None)
        for t in threads:
            t.join()

        if cancel_event.is_set():
            print("RAPID indexing canceled by user; launching without RAPID cache.")
            return True

        if _update_progress_dialog(
            progress_dialog, "Building RAPID cache…", 0, 1, indeterminate=True
        ):
            print("RAPID cache build canceled by user; launching without RAPID cache.")
            return True

        if errors:
            try:
                return _prompt_continue_without_rapid(errors)
            except Exception as e:
                print(f"RAPID failed to display error prompt: {e!r}")
                return True

        file_paths = [p for batch in all_batches for p in batch]

        _pack_u32 = struct.Struct('<I')
        _pack_u16 = struct.Struct('<H')
        chunks = [_pack_u32.pack(len(file_paths))]
        for path in file_paths:
            encoded_path = path.encode('utf-8')
            chunks.append(_pack_u16.pack(len(encoded_path)))
            chunks.append(encoded_path)
        binary_data = b''.join(chunks)

        compressed_data = zlib.compress(binary_data, level=1)

        if _update_progress_dialog(
            progress_dialog, "Writing cache to disk…", 0, 1, indeterminate=True
        ):
            print("RAPID cache write canceled by user; launching without RAPID cache.")
            return True

        output_path = get_rapid_cache_path(organizer, settings_plugin_name)
        output_dir = os.path.dirname(output_path)
        if not os.path.isdir(output_dir):
            organizer.setPluginSetting(settings_plugin_name, "output_to_mod", "")
            output_path = get_rapid_cache_path(organizer, settings_plugin_name)
        with open(output_path, "wb") as f:
            f.write(compressed_data)

        _update_progress_dialog(
            progress_dialog, "RAPID cache complete.", 1, 1, indeterminate=False
        )
        print(f"RAPID Cache built successfully! Indexed {len(file_paths)} loose files.")
        return True
    finally:
        progress_dialog.close()


def read_cache_stats(
    cache_path: str,
) -> tuple[list[str], Counter[str], Counter[str], dict[str, list[str]]] | None:
    """Read and parse rapid_vfs_cache.bin; return (paths, ext_counter, root_counter, samples_per_ext) or None."""
    if not os.path.isfile(cache_path):
        return None
    try:
        with open(cache_path, "rb") as f:
            compressed = f.read()
        raw = zlib.decompress(compressed)
    except (OSError, zlib.error):
        return None
    offset = 0
    if len(raw) < 4:
        return None
    (num_files,) = struct.unpack_from("<I", raw, offset)
    offset += 4
    paths: list[str] = []
    for _ in range(num_files):
        if offset + 2 > len(raw):
            return None
        (path_len,) = struct.unpack_from("<H", raw, offset)
        offset += 2
        if offset + path_len > len(raw):
            return None
        path = raw[offset : offset + path_len].decode("utf-8")
        offset += path_len
        paths.append(path)

    ext_counter: Counter[str] = Counter()
    root_counter: Counter[str] = Counter()
    for p in paths:
        _, ext = os.path.splitext(p)
        ext_key = ext.lower() if ext else "(no ext)"
        ext_counter[ext_key] += 1
        parts = p.split("\\")
        root_counter[parts[0] if parts else ""] += 1

    samples_per_ext: dict[str, list[str]] = {}
    for p in paths:
        _, ext = os.path.splitext(p)
        ext_key = ext.lower() if ext else "(no ext)"
        if ext_key not in samples_per_ext:
            match_ext = ext if ext else ""
            samples = [q for q in paths if (os.path.splitext(q)[1].lower() or "") == (match_ext.lower() if match_ext else "")]
            samples_per_ext[ext_key] = samples[:2]

    return (paths, ext_counter, root_counter, samples_per_ext)


class RapidCacheStatsDialog(QDialog):
    """Dialog showing RAPID cache stats: summary, extensions table, mod roots table, sample paths."""

    def __init__(
        self,
        cache_path: str,
        file_size: int,
        paths: list[str],
        ext_counter: Counter[str],
        root_counter: Counter[str],
        samples_per_ext: dict[str, list[str]],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("RAPID cache stats")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)

        # Summary
        summary = QGroupBox("Summary")
        summary_layout = QVBoxLayout()
        summary_layout.addWidget(QLabel(f"Total paths: {len(paths):,}"))
        summary_layout.addWidget(QLabel(f"Cache file size: {file_size:,} bytes"))
        summary_layout.addWidget(QLabel(f"Cache path: {cache_path}"))
        summary.setLayout(summary_layout)
        layout.addWidget(summary)

        tabs = QTabWidget()

        # Extensions table
        ext_group = QWidget()
        ext_layout = QVBoxLayout(ext_group)
        ext_table = QTableWidget(len(ext_counter), 2)
        ext_table.setHorizontalHeaderLabels(["Extension", "Count"])
        ext_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (ext, count) in enumerate(ext_counter.most_common()):
            ext_table.setItem(row, 0, QTableWidgetItem(ext))
            ext_table.setItem(row, 1, QTableWidgetItem(f"{count:,}"))
        ext_layout.addWidget(ext_table)
        tabs.addTab(ext_group, "Extensions")

        # Mod roots table (top 40)
        root_group = QWidget()
        root_layout = QVBoxLayout(root_group)
        root_rows = root_counter.most_common(40)
        root_table = QTableWidget(len(root_rows), 2)
        root_table.setHorizontalHeaderLabels(["Mod root", "Count"])
        root_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (root, count) in enumerate(root_rows):
            root_table.setItem(row, 0, QTableWidgetItem(root))
            root_table.setItem(row, 1, QTableWidgetItem(f"{count:,}"))
        root_layout.addWidget(root_table)
        tabs.addTab(root_group, "Mod roots")

        # Sample paths per extension
        sample_group = QWidget()
        sample_layout = QVBoxLayout(sample_group)
        sorted_exts = sorted(samples_per_ext.keys(), key=lambda e: ext_counter.get(e, 0), reverse=True)
        sample_table = QTableWidget(len(sorted_exts), 3)
        sample_table.setHorizontalHeaderLabels(["Extension", "Sample 1", "Sample 2"])
        sample_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, ext in enumerate(sorted_exts):
            samples = samples_per_ext.get(ext, [])
            sample_table.setItem(row, 0, QTableWidgetItem(ext))
            sample_table.setItem(row, 1, QTableWidgetItem(samples[0] if len(samples) > 0 else ""))
            sample_table.setItem(row, 2, QTableWidgetItem(samples[1] if len(samples) > 1 else ""))
        sample_layout.addWidget(sample_table)
        tabs.addTab(sample_group, "Sample paths")

        layout.addWidget(tabs)
        self.setLayout(layout)


class PreLaunchGameHook(mobase.IPlugin):
    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        self._organizer.onAboutToRun(self._on_about_to_run)
        self._organizer.onPluginSettingChanged(self._on_setting_changed)
        return True

    def name(self) -> str:
        return HOOK_PLUGIN_NAME

    def author(self) -> str:
        return "Raziell74"

    def description(self) -> str:
        return "Generates a compressed binary cache of the current virtual file system."

    def icon(self):
        return tuple() # Adding a custom PyQt6 icon here just to be fancy

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)

    def isActive(self) -> bool:
        return True

    def settings(self) -> list[mobase.PluginSetting]:
        cpu_count = os.cpu_count() or 4
        return [
            mobase.PluginSetting(
                "worker_threads",
                f"How many threads to use when scanning your mod files (1–{cpu_count}). "
                "Higher values are faster up to your CPU's thread count — going beyond that won't help.",
                min(8, cpu_count)
            ),
            mobase.PluginSetting(
                "extension_blacklist",
                "Additional file extensions to exclude from the cache (comma-separated, e.g. .foo,.bar). "
                "Built-in list excludes plugins, archives, executables, debug, docs, backup/temp, and logs. Leave empty to use only the built-in list.",
                ""
            ),
            mobase.PluginSetting(
                "output_to_mod",
                "Where to write the cache file. Leave empty or type 'Overwrite' for MO2's Overwrite folder (default). "
                "To write into a specific mod, type the exact mod name as shown in the left pane.",
                ""
            )
        ]

    def _on_setting_changed(self, plugin_name: str, key: str, old_value, new_value) -> None:
        if plugin_name != self.name() or key != "worker_threads":
            return
        cpu_count = os.cpu_count() or 4
        if int(new_value) > cpu_count:
            self._organizer.setPluginSetting(self.name(), "worker_threads", cpu_count)

    def index_vfs(self) -> bool:
        return run_index_vfs(self._organizer, self.name())

    def _on_about_to_run(self, app_path: str) -> bool:
        exe_name = os.path.basename(app_path).lower()

        target_executables = [
            "skyrimse.exe",
            "skse64_loader.exe"
        ]

        if exe_name in target_executables:
            return self.index_vfs()

        return True


class RapidCacheTool(mobase.IPluginTool):
    """Tool that runs the same cache build as the pre-launch hook (Build RAPID cache)."""

    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "RAPID - Build Cache Tool"

    def author(self) -> str:
        return "Raziell74"

    def description(self) -> str:
        return "Manually trigger RAPID VFS cache building (same as pre-launch)."

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)

    def isActive(self) -> bool:
        return True

    def settings(self) -> list[mobase.PluginSetting]:
        return []

    def displayName(self) -> str:
        return "RAPID - Build cache"

    def tooltip(self) -> str:
        return "Manually build the RAPID VFS cache (same as when launching the game)."

    def icon(self) -> QIcon:
        return QIcon()

    def display(self) -> None:
        run_index_vfs(self._organizer, HOOK_PLUGIN_NAME)


class RapidCacheViewerTool(mobase.IPluginTool):
    """Tool that opens a dialog showing RAPID cache stats (View RAPID cache)."""

    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "RAPID - View Cache Tool"

    def author(self) -> str:
        return "Raziell74"

    def description(self) -> str:
        return "View decompressed RAPID cache stats (extensions, mod roots, sample paths)."

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)

    def isActive(self) -> bool:
        return True

    def settings(self) -> list[mobase.PluginSetting]:
        return []

    def displayName(self) -> str:
        return "View RAPID cache"

    def tooltip(self) -> str:
        return "View decompressed RAPID cache stats (extensions, mod roots, sample paths)."

    def icon(self) -> QIcon:
        return QIcon()

    def display(self) -> None:
        cache_path = get_rapid_cache_path(self._organizer, HOOK_PLUGIN_NAME)
        result = read_cache_stats(cache_path)
        if result is None:
            parent = self._parentWidget() if hasattr(self, "_parentWidget") else None
            QMessageBox.warning(
                parent,
                "RAPID cache",
                f"The cache file is missing or invalid.\n\nPath: {cache_path}\n\nBuild the cache first using \"Build RAPID cache\" or launch the game.",
            )
            return
        paths, ext_counter, root_counter, samples_per_ext = result
        file_size = os.path.getsize(cache_path) if os.path.isfile(cache_path) else 0
        parent = self._parentWidget() if hasattr(self, "_parentWidget") else None
        dialog = RapidCacheStatsDialog(
            cache_path=cache_path,
            file_size=file_size,
            paths=paths,
            ext_counter=ext_counter,
            root_counter=root_counter,
            samples_per_ext=samples_per_ext,
            parent=parent,
        )
        dialog.exec()


# MO2 requires this factory function to initialize the plugin
def createPlugins() -> List[mobase.IPlugin]:
    return [PreLaunchGameHook(), RapidCacheTool(), RapidCacheViewerTool()]
