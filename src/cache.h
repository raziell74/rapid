#pragma once

#include <cstddef>
#include <span>
#include <string>
#include <vector>

namespace RAPID
{
	/// In-memory cache of loose file paths from rapid_vfs_cache.bin.
	/// Load once, query by traversal prefix, release at kDataLoaded.
	class LooseFileCache
	{
	public:
		/// Load and parse the cache from disk (idempotent after first success).
		/// Paths are normalized (backslash, lowercase) and sorted for prefix lookup.
		/// @return true if loaded (or already loaded), false on error
		bool Load();

		/// Return paths that start with the given traversal prefix (e.g. "data\\TEXTURES\\").
		/// Prefix is normalized (backslash, lowercase); empty or "ROOT" returns all paths.
		/// Span is valid until the next non-const call (Release or Load).
		std::span<const std::string> GetPathsForPrefix(const char* traversalPath);

		/// Clear in-memory cache; called at kDataLoaded. Load() may be called again after.
		void Release();

	private:
		std::vector<std::string> _paths;
		bool _loaded{ false };
	};

	/// Singleton cache instance used by the hook and plugin.
	LooseFileCache& GetLooseFileCache();
}
