import mobase
import os
import queue
import struct
import threading
import time
import zlib
from mobase.widgets import TaskDialog, TaskDialogButton
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

ALLOWED_EXTENSIONS = (
    '.nif', '.tri', '.egm', '.egt', '.btr', '.bto', '.lod',  # Meshes & Geometry
    '.dds', '.tga',                                          # Textures
    '.wav', '.xwm', '.fuz', '.lip',                          # Audio
    '.hkx', '.btt',                                          # Animations & Behavior
    '.pex', '.seq',                                          # Scripts & Logic
    '.swf',                                                  # Interface
    '.ini', '.json', '.toml', '.xml',                        # Config (Papyrus extenders, HDT, MCM)
    '.bat',                                                  # Console-callable
    '.bik',                                                  # Video
    '.strings', '.dlstrings', '.ilstrings',                  # Localization
    '.jslot', '.osp',                                        # Presets (RaceMenu, BodySlide)
    '.ttf', '.otf',                                          # Fonts
    '.bgsm', '.bgem',                                        # Materials
    '.osd',                                                  # Object/scene data
)

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
        return "RAPID - Pre-Launch Game Hook"

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
                "extension_whitelist",
                "Additional file extensions to index (comma-separated, e.g. .foo,.bar). Added to the built-in BSA-style default. Leave empty to use only the default.",
                ""
            )
        ]

    def _get_allowed_extensions(self) -> tuple[str, ...]:
        result: list[str] = list(ALLOWED_EXTENSIONS)
        seen: set[str] = set(ALLOWED_EXTENSIONS)
        raw = self._organizer.pluginSetting(self.name(), "extension_whitelist")
        if raw and raw.strip():
            for part in raw.split(","):
                ext = part.strip().lower()
                if not ext:
                    continue
                if not ext.startswith("."):
                    ext = "." + ext
                if ext not in seen:
                    seen.add(ext)
                    result.append(ext)
        return tuple(result)

    def _on_setting_changed(self, plugin_name: str, key: str, old_value, new_value) -> None:
        if plugin_name != self.name() or key != "worker_threads":
            return
        cpu_count = os.cpu_count() or 4
        if int(new_value) > cpu_count:
            self._organizer.setPluginSetting(self.name(), "worker_threads", cpu_count)

    def _prompt_continue_without_rapid(self, errors: list[str]) -> bool:
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

    def _create_progress_dialog(self) -> QProgressDialog:
        dialog = QProgressDialog("Indexing virtual files...", "Cancel", 0, 1)
        dialog.setWindowTitle("RAPID Indexing")
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        return dialog

    def _update_progress_dialog(
        self,
        dialog: QProgressDialog,
        label_text: str,
        value: int,
        maximum: int
    ) -> bool:
        maximum = max(1, maximum)
        value = max(0, min(value, maximum))
        dialog.setLabelText(label_text)
        if dialog.maximum() != maximum:
            dialog.setMaximum(maximum)
        dialog.setValue(value)
        QApplication.processEvents()
        return dialog.wasCanceled()

    def index_vfs(self) -> bool:
        vfs_tree = self._organizer.virtualFileTree()
        allowed_extensions = self._get_allowed_extensions()

        dir_queue = queue.Queue()
        root_files = []
        for entry in vfs_tree:
            if entry.isDir():
                dir_queue.put(entry)
            else:
                if entry.name().lower().endswith(allowed_extensions):
                    root_files.append(entry.path('\\'))

        cpu_count = os.cpu_count() or 4
        configured = max(1, min(int(self._organizer.pluginSetting(self.name(), "worker_threads")), cpu_count))
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
                            if entry.name().lower().endswith(allowed_extensions):
                                local_paths.append(entry.path('\\'))
                except Exception as e:
                    error_message = f"Worker failed while indexing VFS node: {e!r}"
                    print(f"RAPID worker error while indexing VFS: {e!r}")
                    with error_lock:
                        errors.append(error_message)
                finally:
                    # Every successful get() must be paired with task_done().
                    dir_queue.task_done()
            with lock:
                all_batches.append(local_paths)

        progress_dialog = self._create_progress_dialog()
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
                    if self._update_progress_dialog(
                        progress_dialog,
                        f"Indexing virtual files... ({current_processed} dirs scanned)",
                        current_processed,
                        current_discovered
                    ):
                        cancel_event.set()
                    last_update = now

                if dir_queue.unfinished_tasks == 0:
                    break

                if cancel_event.wait(0.01):
                    # Keep looping so workers can quickly drain queue items and exit cleanly.
                    continue

            for _ in range(worker_count):
                dir_queue.put(None)
            for t in threads:
                t.join()

            if cancel_event.is_set():
                print("RAPID indexing canceled by user; launching without RAPID cache.")
                return True

            if self._update_progress_dialog(progress_dialog, "Building RAPID cache...", 0, 1):
                print("RAPID cache build canceled by user; launching without RAPID cache.")
                return True

            if errors:
                try:
                    return self._prompt_continue_without_rapid(errors)
                except Exception as e:
                    # If UI prompt fails, fail safe by allowing launch without RAPID cache.
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

            if self._update_progress_dialog(progress_dialog, "Writing RAPID cache...", 1, 1):
                print("RAPID cache write canceled by user; launching without RAPID cache.")
                return True

            game_data_path = self._organizer.managedGame().dataDirectory().absolutePath()
            output_path = os.path.join(game_data_path, "rapid_vfs_cache.bin")
            with open(output_path, "wb") as f:
                f.write(compressed_data)

            self._update_progress_dialog(progress_dialog, "RAPID cache complete.", 1, 1)
            print(f"RAPID Cache built successfully! Indexed {len(file_paths)} loose files.")
            return True
        finally:
            progress_dialog.close()

    def _on_about_to_run(self, app_path: str) -> bool:
        exe_name = os.path.basename(app_path).lower()

        target_executables = [
            "skyrimse.exe",
            "skse64_loader.exe"
        ]

        if exe_name in target_executables:
            return self.index_vfs()

        return True

# MO2 requires this factory function to initialize the plugin
def createPlugin() -> mobase.IPlugin:
    return PreLaunchGameHook()
