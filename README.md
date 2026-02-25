# ![RAPID logo](build/images/rapid-logo-icon.png) R.A.P.I.D. -Resource Asset Path Indexing and Dispatch

RAPID slashes loose-file mount time by ~70%—often saving over two minutes on extremely heavy mod setups. It pre-builds an index before launch so the engine skips slow directory crawling and registers hundreds of thousands of assets from memory instead. Faster startup, same game.

## How does it work?

Bethesda's vanilla loose-file loading is basically like trying to find every stone of Barenziah in the game without looking it up. On top of that MO2 has to do a translation through it's own virtual file system every time the engine attempts to find a specific file, like using a wiki to look up the stone locations but the wiki is in japanese and you have to copy paste it into google for the translation. Sure it gets you what you need but it's a bad frametime for your patience bar.

With RAPID instead of the engine walking around aimlessly in the file system, RAPID hands it a pre-made list of every file and where they are. It's like installing a mod that adds map markers. No more bumbling around looking for every stone of Barenziah, just pop open the map and instantly see where they are.

## Technical Details

RAPID mirrors the way BSA archives are fast to register assets: use hashed path entries instead of asking Windows to enumerate folders in real time.

### MO2 pre-launch indexing

Before the game starts, an MO2 plugin reads the final virtual Data tree from MO2, normalizes the loose file paths, computes a BSA-style 64-bit hash per path, and writes everything into a compressed RAP2 cache.

Each cache record stores:

- path hash (for quick lookup)
- normalized path string (for exact resolution)

### SKSE startup injection

At startup, the SKSE side intercepts loose-file traversal, loads the RAP2 cache, and injects the cached entries directly into the engine's resource registration flow.

That means the engine can register loose assets from an in-memory hashed index, similar to how BSA file trees are registered quickly, instead of doing slow OS-level `FindFirstFile`/`FindNextFile` traversal across the loose file system.

If the cache is missing, invalid, or empty, RAPID cleanly falls back to vanilla traversal for that session.

### Runtime behavior

RAPID accelerates asset discovery and registration at mount time. Actual file streaming still uses Skyrim's normal loose-file stream path once a resource is resolved.

## Performance

Measured on a respectable heavy load order with over 800,000 loose files.

|  | Native File Loader | RAPID Cache |
|--------|---------------|------------|
| Events | 27 traversal requests | 1 cache request |
| Discovered at runtime | 879,495 files | -- |
| Loaded | 1,075,620 files | 836,470 files |
| Processing time | 173,978 ms (~2.9 min) | 49,128 ms (~49 s) |
| | | **~125 s (~71.7%) faster** |

The native engine scans for loose files using `FindFirstFile`/`FindNextFile` during 27 traversal events, finding 879,495 files and loading 1,075,620—taking nearly three minutes. RAPID, by contrast, loads a pre-built cache (~66 MB inflated) and injects all 836,470 paths in under 50 seconds—about 3.5× faster.

RAPID is faster for several reasons. It doesn't indiscriminately include every discovered file; instead, it excludes system files and unneeded types like PSDs or script source files (.psc). The full exclusion list can be viewed or customized in the RAPID MO2 plugin settings. Another key difference: while the native engine may repeatedly load the same files due to multiple traversal event calls—creating unnecessary overhead—RAPID only loads each file once, and only those truly needed for the game to function. This selective, one-pass approach delivers a significant performance gain.

Performance will vary depending on your load order and number of loose files. To see the impact on your system, set `PerformanceDiagnostics = true` in `config.ini` and check the timings in `RAPID.log`.

## Requirements

- Skyrim SE/AE
- SKSE64
- Address Library for SKSE Plugins
- Mod Organizer 2 (MO2)

## Installation

RAPID has two components and both are required:

1. Install the SKSE plugin mod in MO2 like a normal mod.
2. Install the MO2 Python plugin file into your MO2 `plugins` folder.
3. Restart MO2 and ensure the RAPID pre-launch hook is enabled.

## MO2 Plugin Settings

Configure via MO2 plugin settings for `RAPID - Pre-Launch Game Hook`:

- `worker_threads`: number of scan workers (default is `min(8, CPU threads)`)
- `extension_blacklist`: comma-separated extensions to exclude from cache, helps avoid mounting loose files that the engine doesn't even use.
- `output_to_mod`: write cache to a specific mod folder. (if left blank or doesn't match an existing mod name, it will default to the Overwrite folder)

## SKSE Config

Path:

- `Data/SKSE/Plugins/RAPID/config.ini`

Default values:

```ini
[General]
Enabled = true
VerboseLogging = false
PerformanceDiagnostics = false
```

## Startup Validation

Check `%SKSE_LOG_DIR%/RAPID.log`:

- hook installed message
- first traversal interception message
- cache load success + path count, or explicit fallback reason (missing/invalid/empty cache)
