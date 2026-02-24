import mobase
import os
import queue
import struct
import threading
import zlib
from mobase.widgets import TaskDialog, TaskDialogButton
from PyQt6.QtWidgets import QMessageBox

class PreLaunchGameHook(mobase.IPlugin):
    def __init__(self):
        super().__init__()
        self._organizer = None

    def init(self, organizer: mobase.IOrganizer) -> bool:
        self._organizer = organizer
        self._organizer.onAboutToRun(self._on_about_to_run)
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

    def index_vfs(self) -> bool:
        vfs_tree = self._organizer.virtualFileTree()

        dir_queue = queue.Queue()
        root_files = []
        for entry in vfs_tree:
            if entry.isDir():
                dir_queue.put(entry)
            else:
                root_files.append(entry.path('\\'))

        worker_count = min(dir_queue.qsize(), os.cpu_count() or 4, 8)

        all_batches = [root_files]
        lock = threading.Lock()
        errors = []
        error_lock = threading.Lock()

        def worker():
            local_paths = []
            while True:
                node = dir_queue.get()
                try:
                    if node is None:
                        break
                    for entry in node:
                        if entry.isDir():
                            dir_queue.put(entry)
                        else:
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

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(worker_count)]
        for t in threads:
            t.start()

        dir_queue.join()
        for _ in range(worker_count):
            dir_queue.put(None)
        for t in threads:
            t.join()

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

        profile_path = self._organizer.profile().absolutePath()
        output_path = os.path.join(profile_path, "rapid_vfs_cache.bin")
        with open(output_path, "wb") as f:
            f.write(compressed_data)

        print(f"RAPID Cache built successfully! Indexed {len(file_paths)} loose files.")
        return True

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
