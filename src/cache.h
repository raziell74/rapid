#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>

namespace RAPID
{
	enum class CacheFormat : std::uint32_t
	{
		kUnknown = 0,
		kRap2 = 2
	};

	struct ResolveResult
	{
		const std::string* path{ nullptr };
		std::size_t collisionCandidates{ 0 };
	};

	class LooseFileCache
	{
	public:
		bool Load();
		std::span<const std::string> GetPathsForPrefix(const char* traversalPath);
		ResolveResult ResolvePath(const char* path) const;
		std::size_t GetEntryCount() const;
		CacheFormat GetFormat() const;
		void Release();

	private:
		std::vector<std::string> _paths;
		std::unordered_map<std::uint64_t, std::vector<std::uint32_t>> _hashToPathIndexes;
		bool _loaded{ false };
		CacheFormat _format{ CacheFormat::kUnknown };
	};

	LooseFileCache& GetLooseFileCache();
}
