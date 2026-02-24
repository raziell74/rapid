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
