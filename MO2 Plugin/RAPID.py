import mobase
import os
import struct
import zlib
from collections import deque

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

    def index_vfs(self):
        vfs_tree = self._organizer.virtualFileTree()

        file_paths = []
        stack = deque([vfs_tree])
        while stack:
            node = stack.pop()
            for entry in node:
                if entry.isDir():
                    stack.append(entry)
                else:
                    file_paths.append(entry.path('\\'))

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

    def _on_about_to_run(self, app_path: str) -> bool:
        exe_name = os.path.basename(app_path).lower()

        target_executables = [
            "skyrimse.exe",
            "skse64_loader.exe"
        ]

        if exe_name in target_executables:
            self.index_vfs()

        return True

# MO2 requires this factory function to initialize the plugin
def createPlugin() -> mobase.IPlugin:
    return PreLaunchGameHook()
