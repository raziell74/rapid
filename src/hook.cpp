#include "hook.h"

#include "log.h"
#include "settings.h"

#include <zlib.h>

#include <algorithm>
#include <fstream>
#include <string>
#include <vector>

namespace RAPID::Hook
{
	namespace
	{
		using TraverseFn = RE::BSResource::ErrorCode (*)(
			RE::BSResource::LooseFileLocation*,
			const char*,
			RE::BSResource::LocationTraverser&);

		TraverseFn g_originalTraverse{ nullptr };
		bool g_installed{ false };
		bool g_cacheInjected{ false };
		constexpr std::size_t kDoTraversePrefixVIndex = 5;

		std::uint32_t ReadU32LE(const std::vector<std::uint8_t>& bytes, std::size_t offset)
		{
			return static_cast<std::uint32_t>(bytes[offset]) |
			       (static_cast<std::uint32_t>(bytes[offset + 1]) << 8) |
			       (static_cast<std::uint32_t>(bytes[offset + 2]) << 16) |
			       (static_cast<std::uint32_t>(bytes[offset + 3]) << 24);
		}

		std::uint16_t ReadU16LE(const std::vector<std::uint8_t>& bytes, std::size_t offset)
		{
			return static_cast<std::uint16_t>(bytes[offset]) |
			       (static_cast<std::uint16_t>(bytes[offset + 1]) << 8);
		}

		std::filesystem::path GetCachePath()
		{
			return Settings::GetGameDataDirectory() / "rapid_vfs_cache.bin";
		}

		bool ReadCompressedCache(std::vector<std::uint8_t>& outBuffer)
		{
			const auto cachePath = GetCachePath();
			std::ifstream file(cachePath, std::ios::binary | std::ios::ate);
			if (!file.is_open()) {
				SKSE::log::warn("RAPID cache file not found at {}", cachePath.string());
				return false;
			}

			const auto fileSize = file.tellg();
			if (fileSize <= 0) {
				SKSE::log::warn("RAPID cache file is empty: {}", cachePath.string());
				return false;
			}

			outBuffer.resize(static_cast<std::size_t>(fileSize));
			file.seekg(0, std::ios::beg);
			file.read(reinterpret_cast<char*>(outBuffer.data()), static_cast<std::streamsize>(outBuffer.size()));
			if (!file.good() && !file.eof()) {
				SKSE::log::error("Failed reading RAPID cache bytes from {}", cachePath.string());
				return false;
			}

			LogVerbose("RAPID cache read path={} size={}", cachePath.string(), static_cast<std::size_t>(fileSize));
			return true;
		}

		bool InflateCache(const std::vector<std::uint8_t>& compressed, std::vector<std::uint8_t>& uncompressed)
		{
			LogVerbose("RAPID inflate start compressedSize={}", compressed.size());
			z_stream stream{};
			stream.next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(compressed.data()));
			stream.avail_in = static_cast<uInt>(compressed.size());

			if (inflateInit(&stream) != Z_OK) {
				SKSE::log::error("zlib inflateInit failed");
				return false;
			}

			constexpr std::size_t kChunkSize = 1 << 20;
			std::vector<std::uint8_t> chunk(kChunkSize);

			int inflateResult = Z_OK;
			do {
				stream.next_out = reinterpret_cast<Bytef*>(chunk.data());
				stream.avail_out = static_cast<uInt>(chunk.size());
				inflateResult = inflate(&stream, Z_NO_FLUSH);

				if (inflateResult != Z_OK && inflateResult != Z_STREAM_END) {
					inflateEnd(&stream);
					SKSE::log::error("zlib inflate failed with error code {}", inflateResult);
					return false;
				}

				const auto produced = chunk.size() - static_cast<std::size_t>(stream.avail_out);
				uncompressed.insert(uncompressed.end(), chunk.begin(), chunk.begin() + static_cast<std::ptrdiff_t>(produced));
			} while (inflateResult != Z_STREAM_END);

			inflateEnd(&stream);
			LogVerbose("RAPID inflate done uncompressedSize={}", uncompressed.size());
			return true;
		}

		void NormalizePath(std::string& path)
		{
			std::replace(path.begin(), path.end(), '/', '\\');
		}

		bool ParseCacheEntries(const std::vector<std::uint8_t>& data, std::vector<std::string>& outPaths)
		{
			LogVerbose("RAPID parsing cache entries payloadSize={}", data.size());
			if (data.size() < sizeof(std::uint32_t)) {
				SKSE::log::error("RAPID cache payload too small for header");
				return false;
			}

			const auto expectedCount = ReadU32LE(data, 0);
			LogVerbose("RAPID cache expectedEntryCount={}", expectedCount);
			std::size_t cursor = sizeof(std::uint32_t);
			std::size_t malformedCount = 0;

			outPaths.clear();
			outPaths.reserve(expectedCount);

			for (std::uint32_t i = 0; i < expectedCount; ++i) {
				if (cursor + sizeof(std::uint16_t) > data.size()) {
					SKSE::log::error("RAPID cache truncated while reading length at index {}", i);
					return false;
				}

				const auto pathLength = ReadU16LE(data, cursor);
				cursor += sizeof(std::uint16_t);

				if (cursor + pathLength > data.size()) {
					SKSE::log::error("RAPID cache truncated while reading path bytes at index {}", i);
					return false;
				}

				std::string path(reinterpret_cast<const char*>(data.data() + cursor), pathLength);
				cursor += pathLength;

				if (path.empty()) {
					++malformedCount;
					continue;
				}

				NormalizePath(path);
				outPaths.push_back(std::move(path));
			}

			if (cursor != data.size()) {
				SKSE::log::warn(
					"RAPID cache parse consumed {} of {} bytes (trailing bytes={})",
					cursor,
					data.size(),
					data.size() - cursor);
			}

			if (Settings::Get().verboseLogging) {
				LogVerbose(
					"RAPID cache parsed entries={} malformed={} bytes={}",
					outPaths.size(),
					malformedCount,
					data.size());

				for (std::size_t i = 0; i < outPaths.size(); ++i) {
					LogVerbose("RAPID filetree [{} / {}] {}", i + 1, outPaths.size(), outPaths[i]);
				}
			}

			return true;
		}

		bool InjectEntriesIntoEntryDB(const std::vector<std::string>& paths)
		{
			if (paths.empty()) {
				SKSE::log::warn("RAPID injection skipped: cache contains no entries");
				return false;
			}

			constexpr std::size_t kVerbosePathCap = 50;
			std::size_t successCount = 0;
			for (const auto& path : paths) {
				if (path.empty()) {
					continue;
				}
				RE::BSResource::RegisterGlobalPath(path.c_str());
				if (Settings::Get().verboseLogging && successCount < kVerbosePathCap) {
					LogVerbose("RAPID register path [{}] {}", successCount + 1, path);
				}
				++successCount;
			}

			if (successCount == 0) {
				SKSE::log::error("RAPID injection failed: no paths registered");
				return false;
			}

			g_cacheInjected = true;
			SKSE::log::info("RAPID injected {} loose-file paths into BSResource", successCount);
			if (Settings::Get().verboseLogging && successCount > kVerbosePathCap) {
				LogVerbose("RAPID registered first {} paths (total {}); remaining paths not logged", kVerbosePathCap, successCount);
			}
			return true;
		}

		bool TryInjectFromCache()
		{
			if (g_cacheInjected) {
				LogVerbose("RAPID cache already injected, skipping load");
				return true;
			}

			LogVerbose("RAPID loading cache...");
			std::vector<std::uint8_t> compressed;
			if (!ReadCompressedCache(compressed)) {
				return false;
			}

			std::vector<std::uint8_t> uncompressed;
			if (!InflateCache(compressed, uncompressed)) {
				return false;
			}

			LogVerbose(
				"RAPID cache decompressed compressedBytes={} uncompressedBytes={}",
				compressed.size(),
				uncompressed.size());

			std::vector<std::string> paths;
			if (!ParseCacheEntries(uncompressed, paths)) {
				return false;
			}

			return InjectEntriesIntoEntryDB(paths);
		}

		RE::BSResource::ErrorCode HookedTraversePrefix(
			RE::BSResource::LooseFileLocation* a_this,
			const char* a_path,
			RE::BSResource::LocationTraverser& a_traverser)
		{
			static bool g_firstTraverseLogged = false;
			if (Settings::Get().verboseLogging && !g_firstTraverseLogged) {
				LogVerbose("RAPID traverse hook first invocation path={}", a_path ? a_path : "(null)");
				g_firstTraverseLogged = true;
			}
			if (Settings::Get().enabled && TryInjectFromCache()) {
				return RE::BSResource::ErrorCode::kNone;
			}

			if (g_originalTraverse) {
				return g_originalTraverse(a_this, a_path, a_traverser);
			}

			SKSE::log::error("RAPID fallback failed: original traversal pointer is null");
			return RE::BSResource::ErrorCode::kFileError;
		}
	}

	bool Install()
	{
		if (g_installed) {
			return true;
		}

		if (!Settings::Get().enabled) {
			SKSE::log::info("RAPID hook install skipped: plugin disabled via settings");
			return false;
		}

		try {
			REL::Relocation<std::uintptr_t> vtbl{ RE::VTABLE_BSResource__LooseFileLocation[0] };
			const auto originalAddress = vtbl.write_vfunc(kDoTraversePrefixVIndex, HookedTraversePrefix);
			g_originalTraverse = reinterpret_cast<TraverseFn>(originalAddress);
			g_installed = g_originalTraverse != nullptr;
		} catch (const std::exception& e) {
			SKSE::log::error("RAPID hook install threw exception: {}", e.what());
			g_installed = false;
		}

		if (!g_installed) {
			SKSE::log::error("RAPID hook install failed");
			return false;
		}

		SKSE::log::info("RAPID traversal hook installed");
		LogVerbose("RAPID traversal hook installed at vfunc 5");
		return true;
	}

	bool IsInstalled()
	{
		return g_installed;
	}
}
