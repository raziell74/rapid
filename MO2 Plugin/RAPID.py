import mobase
import os
import queue
import struct
import threading
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
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
CACHE_SUBDIR = ("SKSE", "Plugins", "RAPID")
DATA_PREFIX = "data\\"
RAP2_MAGIC = b"RAP2"
RAP2_VERSION = 2
HASH_PARITY_VECTORS = (
    "textures/actors/character/face.dds",
    "meshes/armor/iron/ironhelmet.nif",
    "sound/voice/skyrim.esm/guard/hello.wav",
    "scripts/test.pex",
    "Data\\Textures\\Example.DDS",
)

# Unused - Need to do more in game logging to determine if there are more directories used by the engine.
# ENGINE_DATA_SUBDIRS = frozenset({
#     "textures","meshes", "facegen", "interface", "music", "sound",
#     "scripts", "maxheights", "vis", "grass", "strings", "shadersfx",
# })

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


def _normalize_path(raw: str) -> str:
    stripped = raw.strip(" \t")
    lowered = stripped.replace("/", "\\").lower()
    while "\\\\" in lowered:
        lowered = lowered.replace("\\\\", "\\")
    lowered = lowered.lstrip("\\")
    lowered = lowered.rstrip("\\")
    if not lowered.startswith(DATA_PREFIX):
        lowered = DATA_PREFIX + lowered
    return lowered


def _compute_rapid_hash64(path: str) -> int:
    normalized = _normalize_path(path)
    dot = normalized.rfind(".")
    if dot == -1:
        root = normalized
        ext = ""
    else:
        root = normalized[:dot]
        ext = normalized[dot:]

    low = 0
    if root:
        low = ord(root[-1]) & 0xFF
        if len(root) > 2:
            low |= (ord(root[-2]) & 0xFF) << 8
        low |= (len(root) & 0xFFFFFFFF) << 16
        low |= (ord(root[0]) & 0xFF) << 24
        low &= 0xFFFFFFFF

    if ext == ".kf":
        low |= 0x80
    elif ext == ".nif":
        low |= 0x8000
    elif ext == ".dds":
        low |= 0x8080
    elif ext == ".wav":
        low |= 0x80000000
    low &= 0xFFFFFFFF

    mid_hash = 0
    for char in root[1:-2]:
        mid_hash = ((mid_hash * 0x1003F) + ord(char)) & 0xFFFFFFFF

    ext_hash = 0
    for char in ext:
        ext_hash = ((ext_hash * 0x1003F) + ord(char)) & 0xFFFFFFFF

    high = (mid_hash + ext_hash) & 0xFFFFFFFF
    return ((high << 32) | low) & 0xFFFFFFFFFFFFFFFF


def _emit_hash_parity_vectors() -> None:
    for sample in HASH_PARITY_VECTORS:
        normalized = _normalize_path(sample)
        value = _compute_rapid_hash64(sample)
        print(f"RAPID hash vector path={sample!r} normalized={normalized!r} hash=0x{value:016X}")

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
    return os.path.join(base_dir, *CACHE_SUBDIR, CACHE_FILENAME)


def _get_cache_path_candidates(organizer: mobase.IOrganizer, settings_plugin_name: str) -> list[str]:
    candidates: list[str] = []
    output_path = get_rapid_cache_path(organizer, settings_plugin_name)
    candidates.append(output_path)
    overwrite_path = os.path.join(organizer.overwritePath(), *CACHE_SUBDIR, CACHE_FILENAME)
    if overwrite_path not in candidates:
        candidates.append(overwrite_path)
    try:
        game = organizer.managedGame()
        if game is not None:
            data_dir = game.dataDirectory()
            if data_dir is not None:
                data_path = os.path.join(data_dir.absolutePath(), *CACHE_SUBDIR, CACHE_FILENAME)
                if data_path not in candidates:
                    candidates.append(data_path)
    except Exception:
        pass
    return candidates


def _find_existing_cache_path(candidates: list[str]) -> str | None:
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _default_excluded_extensions_setting() -> str:
    return ",".join(EXCLUDED_EXTENSIONS)


def _get_excluded_extensions_for_settings(organizer: mobase.IOrganizer, settings_plugin_name: str) -> frozenset[str]:
    raw = organizer.pluginSetting(settings_plugin_name, "extension_blacklist")
    if raw is None or not raw.strip():
        return frozenset(EXCLUDED_EXTENSIONS)
    excluded: set[str] = set()
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


def _serialize_metadata(
    build_time_ms: int,
    ext_counter: Counter[str],
    root_counter: Counter[str],
) -> bytes:
    _pack_u32 = struct.Struct('<I')
    _pack_u16 = struct.Struct('<H')
    _pack_u64 = struct.Struct('<Q')
    parts = [_pack_u64.pack(build_time_ms)]
    ext_items = ext_counter.most_common()
    parts.append(_pack_u32.pack(len(ext_items)))
    for ext, count in ext_items:
        b = ext.encode("utf-8")
        parts.append(_pack_u16.pack(len(b)))
        parts.append(b)
        parts.append(_pack_u32.pack(count))
    root_items = root_counter.most_common()
    parts.append(_pack_u32.pack(len(root_items)))
    for root, count in root_items:
        b = root.encode("utf-8")
        parts.append(_pack_u16.pack(len(b)))
        parts.append(b)
        parts.append(_pack_u32.pack(count))
    return b"".join(parts)


def _parse_metadata(
    raw: bytes, path_block_end: int
) -> tuple[int, Counter[str], Counter[str]] | None:
    remaining = len(raw) - path_block_end
    if remaining < 4:
        return None
    meta_len = struct.unpack_from("<I", raw, len(raw) - 4)[0]
    if meta_len <= 0 or meta_len > remaining - 4:
        return None
    max_meta = 1 << 20
    if meta_len > max_meta:
        return None
    start = len(raw) - 4 - meta_len
    if start < path_block_end:
        return None
    meta = raw[start : start + meta_len]
    off = 0
    if off + 8 > len(meta):
        return None
    (build_time_ms,) = struct.unpack_from("<Q", meta, off)
    off += 8
    ext_counter: Counter[str] = Counter()
    if off + 4 > len(meta):
        return None
    (num_ext,) = struct.unpack_from("<I", meta, off)
    off += 4
    for _ in range(num_ext):
        if off + 2 > len(meta):
            return None
        (slen,) = struct.unpack_from("<H", meta, off)
        off += 2
        if off + slen + 4 > len(meta):
            return None
        ext_counter[meta[off : off + slen].decode("utf-8")] = struct.unpack_from("<I", meta, off + slen)[0]
        off += slen + 4
    if off + 4 > len(meta):
        return None
    (num_root,) = struct.unpack_from("<I", meta, off)
    off += 4
    root_counter: Counter[str] = Counter()
    for _ in range(num_root):
        if off + 2 > len(meta):
            return None
        (slen,) = struct.unpack_from("<H", meta, off)
        off += 2
        if off + slen + 4 > len(meta):
            return None
        root_counter[meta[off : off + slen].decode("utf-8")] = struct.unpack_from("<I", meta, off + slen)[0]
        off += slen + 4
    return (build_time_ms, ext_counter, root_counter)


def run_index_vfs(organizer: mobase.IOrganizer, settings_plugin_name: str) -> bool:
    """Run VFS indexing and write rapid_vfs_cache.bin to the configured output (Overwrite or named mod)."""
    _emit_hash_parity_vectors()
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

        file_paths = [_normalize_path(p) for batch in all_batches for p in batch]
        file_paths = [p for p in file_paths if p]
        serializable_paths = [p for p in file_paths if len(p.encode("utf-8")) <= 0xFFFF]

        ext_counter: Counter[str] = Counter()
        root_counter: Counter[str] = Counter()
        for p in serializable_paths:
            _, ext = os.path.splitext(p)
            ext_key = ext.lower() if ext else "(no ext)"
            ext_counter[ext_key] += 1
            parts = p.split("\\")
            root_counter[parts[0] if parts else ""] += 1

        build_time_ms = int(time.time() * 1000)
        metadata_payload = _serialize_metadata(build_time_ms, ext_counter, root_counter)

        _pack_u32 = struct.Struct('<I')
        _pack_u16 = struct.Struct('<H')
        _pack_u64 = struct.Struct('<Q')
        chunks = [RAP2_MAGIC, _pack_u32.pack(RAP2_VERSION), _pack_u32.pack(len(serializable_paths))]
        for path in serializable_paths:
            encoded_path = path.encode('utf-8')
            path_hash = _compute_rapid_hash64(path)
            chunks.append(_pack_u64.pack(path_hash))
            chunks.append(_pack_u16.pack(len(encoded_path)))
            chunks.append(encoded_path)
        chunks.append(metadata_payload)
        chunks.append(_pack_u32.pack(len(metadata_payload)))
        binary_data = b''.join(chunks)

        compressed_data = zlib.compress(binary_data, level=1)

        if _update_progress_dialog(
            progress_dialog, "Writing cache to disk…", 0, 1, indeterminate=True
        ):
            print("RAPID cache write canceled by user; launching without RAPID cache.")
            return True

        output_path = get_rapid_cache_path(organizer, settings_plugin_name)
        output_dir = os.path.dirname(output_path)
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(compressed_data)

        _update_progress_dialog(
            progress_dialog, "RAPID cache complete.", 1, 1, indeterminate=False
        )
        print(f"RAPID Cache built successfully! Indexed {len(serializable_paths)} loose files.")
        return True
    finally:
        progress_dialog.close()


def read_cache_stats(
    cache_path: str,
) -> tuple[list[str], Counter[str], Counter[str], int | None] | None:
    """Read and parse rapid_vfs_cache.bin; return (paths, ext_counter, root_counter, build_time_utc_ms) or None."""
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

    if len(raw) < 12 or raw[:4] != RAP2_MAGIC:
        return None
    version = struct.unpack_from("<I", raw, 4)[0]
    if version != RAP2_VERSION:
        return None
    (num_files,) = struct.unpack_from("<I", raw, 8)
    offset = 12

    paths: list[str] = []
    for _ in range(num_files):
        if offset + 8 > len(raw):
            return None
        offset += 8
        if offset + 2 > len(raw):
            return None
        (path_len,) = struct.unpack_from("<H", raw, offset)
        offset += 2
        if offset + path_len > len(raw):
            return None
        path = _normalize_path(raw[offset : offset + path_len].decode("utf-8"))
        offset += path_len
        paths.append(path)

    path_block_end = offset
    parsed = _parse_metadata(raw, path_block_end)
    if parsed is not None:
        build_time_ms, ext_counter, root_counter = parsed
    else:
        build_time_ms = None
        ext_counter = Counter()
        root_counter = Counter()
        for p in paths:
            _, ext = os.path.splitext(p)
            ext_key = ext.lower() if ext else "(no ext)"
            ext_counter[ext_key] += 1
            parts = p.split("\\")
            root_counter[parts[0] if parts else ""] += 1

    return (paths, ext_counter, root_counter, build_time_ms)


def _format_build_time(build_time_utc_ms: int | None) -> str:
    if build_time_utc_ms is None:
        return "unknown"
    dt = datetime.fromtimestamp(build_time_utc_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


class RapidCacheStatsDialog(QDialog):
    """Dialog showing RAPID cache stats: summary, extensions table, mod roots table."""

    def __init__(
        self,
        cache_path: str,
        file_size: int,
        paths: list[str],
        ext_counter: Counter[str],
        root_counter: Counter[str],
        build_time_utc_ms: int | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("RAPID cache stats")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)

        summary = QGroupBox("Summary")
        summary_layout = QVBoxLayout()
        summary_layout.addWidget(QLabel(f"Total paths: {len(paths):,}"))
        summary_layout.addWidget(QLabel(f"Cache file size: {file_size:,} bytes"))
        summary_layout.addWidget(QLabel(f"Built: {_format_build_time(build_time_utc_ms)}"))
        summary_layout.addWidget(QLabel(f"Cache path: {cache_path}"))
        summary.setLayout(summary_layout)
        layout.addWidget(summary)

        tabs = QTabWidget()

        # Mod roots table (top 50)
        root_group = QWidget()
        root_layout = QVBoxLayout(root_group)
        root_rows = root_counter.most_common(50)
        root_table = QTableWidget(len(root_rows), 2)
        root_table.setHorizontalHeaderLabels(["Mod root", "Count"])
        root_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row, (root, count) in enumerate(root_rows):
            root_table.setItem(row, 0, QTableWidgetItem(root))
            root_table.setItem(row, 1, QTableWidgetItem(f"{count:,}"))
        root_layout.addWidget(root_table)
        tabs.addTab(root_group, "Mod roots")

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
                "File extensions to exclude from the cache (comma-separated). Pre-filled with defaults (plugins, archives, executables, etc.). Remove an extension to include it; add more to exclude.",
                _default_excluded_extensions_setting()
            ),
            mobase.PluginSetting(
                "output_to_mod",
                "Where to write the cache file (always under SKSE/Plugins/RAPID in that folder). "
                "Leave empty or type 'Overwrite' for MO2's Overwrite folder (default). "
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
    """Tool that opens a dialog showing RAPID cache stats (RAPID - View Cache Stats)."""

    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        return True

    def name(self) -> str:
        return "RAPID - View Cache Stats"

    def author(self) -> str:
        return "Raziell74"

    def description(self) -> str:
        return "View decompressed RAPID cache stats (extensions, mod roots)."

    def version(self) -> mobase.VersionInfo:
        return mobase.VersionInfo(1, 0, 0, mobase.ReleaseType.FINAL)

    def isActive(self) -> bool:
        return True

    def settings(self) -> list[mobase.PluginSetting]:
        return []

    def displayName(self) -> str:
        return "RAPID - View Cache Stats"

    def tooltip(self) -> str:
        return "View decompressed RAPID cache stats (extensions, mod roots)."

    def icon(self) -> QIcon:
        return QIcon()

    def display(self) -> None:
        parent = self._parentWidget() if hasattr(self, "_parentWidget") else None
        candidates = _get_cache_path_candidates(self._organizer, HOOK_PLUGIN_NAME)
        cache_path = _find_existing_cache_path(candidates)
        if cache_path is None:
            if not run_index_vfs(self._organizer, HOOK_PLUGIN_NAME):
                return
            cache_path = get_rapid_cache_path(self._organizer, HOOK_PLUGIN_NAME)
            if not os.path.isfile(cache_path):
                QMessageBox.warning(
                    parent,
                    "RAPID cache",
                    f"The cache file is missing or invalid.\n\nPath: {cache_path}\n\nBuild was cancelled or failed.",
                )
                return
        result = read_cache_stats(cache_path)
        if result is None:
            QMessageBox.warning(
                parent,
                "RAPID cache",
                f"The cache file is missing or invalid.\n\nPath: {cache_path}\n\nBuild the cache first using \"Build RAPID cache\" or launch the game.",
            )
            return
        paths, ext_counter, root_counter, build_time_utc_ms = result
        file_size = os.path.getsize(cache_path) if os.path.isfile(cache_path) else 0
        dialog = RapidCacheStatsDialog(
            cache_path=cache_path,
            file_size=file_size,
            paths=paths,
            ext_counter=ext_counter,
            root_counter=root_counter,
            build_time_utc_ms=build_time_utc_ms,
            parent=parent,
        )
        dialog.exec()


# MO2 requires this factory function to initialize the plugin
def createPlugins() -> List[mobase.IPlugin]:
    return [PreLaunchGameHook(), RapidCacheTool(), RapidCacheViewerTool()]