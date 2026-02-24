# R.A.P.I.D. (Resource Asset Path Indexing and Dispatch)

Welcome to **R.A.P.I.D.**, the mod that finally stops the Skyrim Creation Engine from asking Windows "Are we there yet?" 100,000 times before the main menu even loads.

R.A.P.I.D. is a two-part performance optimization framework for Skyrim Special Edition / Anniversary Edition and Mod Organizer 2. It fundamentally rewrites how the engine's `BSResource` subsystem discovers and indexes loose files during the initial boot sequence. By completely bypassing the operating system's native file enumeration APIs and skipping the heavy interception overhead of Mod Organizer 2's User Space Virtual File System (USVFS), R.A.P.I.D. achieves near-instant, O(1) loose file registration. Because the only thing that should take five minutes to load is deciding on your character's nose shape.

## The Loose File Bottleneck

When Skyrim launches, it builds a Virtual File System (VFS). Bethesda Softworks Archives (BSAs) are the golden children here—they load blazingly fast because the engine just reads a single, highly compressed binary header.

"Loose files," on the other hand, are the problem children. To find them, the engine insists on manually crawling your physical storage drive using ancient Windows APIs like `FindFirstFile` and `FindNextFile`. Add 100,000+ loose 4K sweetroll textures to your load order, and throw in Mod Organizer 2's USVFS proxy—which has to individually intercept, translate, and return every single one of those API calls—and suddenly your CPU is crying, your disk is thrashing, and you're staring at a black screen wondering if your PC finally gave up on life.

## The Solution

R.A.P.I.D. fixes this by treating your chaotic pile of loose files exactly like a highly-optimized BSA archive. We achieve this with a two-step handshake:

### 1. The MO2 Python Plugin (The Indexer)

Before `SkyrimSE.exe` even realizes it's alive, the R.A.P.I.D. MO2 plugin swoops in. It taps into the `mobase.IFileTree` interface to instantly grab the final, conflict-resolved state of your virtual `Data` directory. It then serializes this tree into a compressed binary cache file (essentially a fake BSA header for your loose files) and gently places it into your active MO2 profile.

### 2. The SKSE CommonLibSSE-NG Plugin (The Injector)

Once the game actually launches, the R.A.P.I.D. SKSE plugin uses `CommonLibSSE-NG` and the Address Library to execute a Trampoline hook right into the `BSResource::LooseFileLocation` directory traversal routines.

Instead of letting the engine painfully interrogating Windows for files, we intercept the call, slam our pre-generated binary cache into memory, and manually populate the `BSResource::EntryDB` hash maps with your asset paths. The engine is happily tricked into believing it just successfully enumerated the hard drive. We bypass the USVFS proxy overhead entirely, turning a process that takes minutes into a process that takes milliseconds. *It just works.*

## Requirements

* **Skyrim Special Edition or Anniversary Edition** (Supports 1.5.97, 1.6.xx, and VR via CommonLibSSE-NG cross-version magic).
* **SKSE64** (You know the drill).
* **Address Library for SKSE Plugins** (Because hardcoding offsets is so 2013).
* **Mod Organizer 2** (Version 2.3.0 or higher required for full Python module support). *Note: Vortex is not supported. Sorry, Vortex users, we need MO2's specific VFS brain for this one. Get Wrecked Vortex*

## Installation

Because this mod relies on two completely different environments (MO2 and SKSE), the installation requires a brief manual step alongside your standard mod manager download.

**Step 1: Install the SKSE Injector Plugin (Main File)**

1. On the R.A.P.I.D. Nexus Mods page, navigate to the Files tab.
2. Click **Mod Manager Download** under the Main Files section for `RAPID SKSE Plugin`.
3. Install and enable it in Mod Organizer 2 just like any other standard mod. Ensure the checkbox in your left pane is ticked.

**Step 2: Install the MO2 Indexer Plugin (Miscellaneous File)**
_MO2 plugins operate at the application level, not inside the virtual game folder, so this part must be placed manually._

1. On the Nexus Mods page, click Manual Download under the Miscellaneous/Optional Files section for the `RAPID MO2 Plugin`.
2. Extract the downloaded archive. Move the Python script directly into your Mod Organizer 2 plugins folder (e.g., `C:\Modding\MO2\plugins\`).
3. Restart Mod Organizer 2.
4. Verify it's installed by clicking the Tools -> Tool Plugins menu at the top of MO2. Ensure the setting to "Run automatically on executable launch" is enabled so it can work its magic in the background.

## MO2 Plugin Settings

The RAPID MO2 plugin can be configured via **Plugins → RAPID - Pre-Launch Game Hook → Settings** (or the equivalent plugin settings entry in your MO2 version). These options control how the virtual file tree is scanned and which files are written into the binary cache.

| Setting | Description |
|--------|-------------|
| **Worker threads** | Number of threads used to traverse the virtual file tree (1 up to your CPU core count). Higher values speed up cache generation; the default is the lesser of 8 or your core count. |
| **Extension whitelist** | *Additional* file extensions to index, comma-separated (e.g. `.foo,.bar`). These are **added to** the built-in BSA-style default; the default list already includes meshes (`.nif`, `.tri`, `.btr`, `.bto`, etc.), textures (`.dds`, `.tga`), audio (`.wav`, `.xwm`, `.fuz`, `.lip`), animations (`.hkx`, `.btt`), scripts (`.pex`, `.seq`), interface (`.swf`), config (`.ini`, `.json`, `.toml`, `.xml`), localization (`.strings`, `.dlstrings`, `.ilstrings`), video (`.bik`), fonts (`.ttf`, `.otf`), materials (`.bgsm`, `.bgem`), and other engine-relevant types. Leave this field **empty** to use only the default. Add extensions here if you use mods that load loose files with custom extensions. |

Only paths whose file extension is in the combined whitelist are written to the cache. This keeps the game's internal hash maps small and avoids indexing non-asset files (e.g. `.txt`, `.psc`, `.esp`, `.dll`).

## SKSE Config

R.A.P.I.D. reads settings from:

- `Data/SKSE/Plugins/RAPID/config.ini`

If the file or directory is missing, the SKSE plugin creates it on startup with defaults.

Default file contents:

```ini
[General]
Enabled = true
VerboseLogging = true
PerformanceDiagnostics = false
```

## Cache Format (RAP2)

`rapid_vfs_cache.bin` is zlib-compressed. After decompression, the current layout is:

- `magic[4]`: `RAP2`
- `version_u32`: `2`
- `record_count_u32`
- repeated records:
  - `path_hash_u64_le`
  - `path_len_u16_le`
  - `path_utf8[path_len]` (canonical lowercase `\\` path, optional leading `data\\` stripped)
- metadata blob (for MO2 cache stats)
- `metadata_len_u32_le`

## Startup Validation Checklist

Use `%SKSE_LOG_DIR%/RAPID.log` to validate startup behavior:

- Missing cache: confirm fallback message and normal traversal continues.
- Corrupt cache: confirm decompression/parse error is logged and traversal falls back.
- Zero-entry cache: confirm warning is logged and traversal falls back.
- Large cache: confirm decompressed byte count and parsed entry count are logged.
- Non-ASCII paths: confirm parse completes without crash and counts are reported.
