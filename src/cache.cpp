#include "cache.h"
#include "log.h"
#include "settings.h"

#include <zlib.h>

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace RAPID
{
	namespace
	{
		std::filesystem::path GetCachePath()
		{
			return Settings::GetGameDataDirectory() / "rapid_vfs_cache.bin";
		}

		bool ReadCompressedCache(std::vector<std::uint8_t>& outBuffer)
		{
			const auto cachePath = GetCachePath();
			std::ifstream file(cachePath, std::ios::binary | std::ios::ate);
			if (!file.is_open()) {
				SKSE::log::warn("R.A.P.I.D. cache file not found at {}", cachePath.string());
				return false;
			}

			const auto fileSize = file.tellg();
			if (fileSize <= 0) {
				SKSE::log::warn("R.A.P.I.D. cache file is empty: {}", cachePath.string());
				return false;
			}

			outBuffer.resize(static_cast<std::size_t>(fileSize));
			file.seekg(0, std::ios::beg);
			file.read(reinterpret_cast<char*>(outBuffer.data()), static_cast<std::streamsize>(outBuffer.size()));
			if (!file.good() && !file.eof()) {
				SKSE::log::error("R.A.P.I.D. failed to read cache bytes from {}", cachePath.string());
				return false;
			}
			return true;
		}

		bool InflateCache(const std::vector<std::uint8_t>& compressed, std::vector<std::uint8_t>& uncompressed)
		{
			z_stream stream{};
			stream.next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(compressed.data()));
			stream.avail_in = static_cast<uInt>(compressed.size());

			if (inflateInit(&stream) != Z_OK) {
				SKSE::log::error("R.A.P.I.D. zlib inflateInit failed");
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
					SKSE::log::error("R.A.P.I.D. zlib inflate failed with error code {}", inflateResult);
					return false;
				}

				const auto produced = chunk.size() - static_cast<std::size_t>(stream.avail_out);
				uncompressed.insert(uncompressed.end(), chunk.begin(), chunk.begin() + static_cast<std::ptrdiff_t>(produced));
			} while (inflateResult != Z_STREAM_END);

			inflateEnd(&stream);
			return true;
		}

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

		void NormalizePathToLower(std::string& path)
		{
			std::replace(path.begin(), path.end(), '/', '\\');
			for (auto& c : path) {
				if (c >= 'A' && c <= 'Z') {
					c = static_cast<char>(c + ('a' - 'A'));
				}
			}
		}

		bool ParseCacheEntries(const std::vector<std::uint8_t>& data, std::vector<std::string>& outPaths)
		{
			if (data.size() < sizeof(std::uint32_t)) {
				SKSE::log::error("R.A.P.I.D. cache payload too small for header");
				return false;
			}

			const std::uint32_t expectedCount = ReadU32LE(data, 0);
			std::size_t cursor = sizeof(std::uint32_t);

			outPaths.clear();
			outPaths.reserve(expectedCount);

			for (std::uint32_t i = 0; i < expectedCount; ++i) {
				if (cursor + sizeof(std::uint16_t) > data.size()) {
					SKSE::log::error("R.A.P.I.D. cache truncated reading length at index {}", i);
					return false;
				}

				const std::uint16_t pathLength = ReadU16LE(data, cursor);
				cursor += sizeof(std::uint16_t);

				if (cursor + pathLength > data.size()) {
					SKSE::log::error("R.A.P.I.D. cache truncated reading path bytes at index {}", i);
					return false;
				}

				std::string path(reinterpret_cast<const char*>(data.data() + cursor), pathLength);
				cursor += pathLength;

				if (path.empty()) {
					continue;
				}

				NormalizePathToLower(path);
				outPaths.push_back(std::move(path));
			}

			if (cursor != data.size() && Settings::Get().verboseLogging) {
				SKSE::log::warn(
					"R.A.P.I.D. cache parse consumed {} of {} bytes (trailing bytes={})",
					cursor,
					data.size(),
					data.size() - cursor);
			}
			return true;
		}

		std::string NormalizeTraversalPrefix(const char* traversalPath)
		{
			if (!traversalPath || traversalPath[0] == '\0') {
				return "";
			}
			std::string s(traversalPath);
			NormalizePathToLower(s);
			if (s == "root") {
				return "";
			}
			if (!s.empty() && s.back() != '\\') {
				s += '\\';
			}
			return s;
		}
	}

	bool LooseFileCache::Load()
	{
		if (_loaded) {
			return true;
		}

		std::vector<std::uint8_t> compressed;
		if (!ReadCompressedCache(compressed)) {
			return false;
		}

		std::vector<std::uint8_t> uncompressed;
		if (!InflateCache(compressed, uncompressed)) {
			return false;
		}

		if (!ParseCacheEntries(uncompressed, _paths)) {
			return false;
		}

		if (_paths.empty()) {
			SKSE::log::warn("R.A.P.I.D. cache contains no entries");
			return false;
		}

		std::ranges::sort(_paths);
		_loaded = true;

		if (Settings::Get().verboseLogging) {
			SKSE::log::info("R.A.P.I.D. cache loaded: {} paths in memory", _paths.size());
		}
		return true;
	}

	std::span<const std::string> LooseFileCache::GetPathsForPrefix(const char* traversalPath)
	{
		if (!_loaded || _paths.empty()) {
			return {};
		}

		const std::string prefix = NormalizeTraversalPrefix(traversalPath);

		if (prefix.empty()) {
			return std::span<const std::string>(_paths);
		}

		// Range of paths starting with prefix: [lower_bound(prefix), lower_bound(prefixEnd)).
		// prefixEnd = first string lexicographically after all "prefix*" (e.g. prefix with last char +1).
		std::string prefixEnd = prefix;
		prefixEnd.back() = static_cast<char>(static_cast<unsigned char>(prefixEnd.back()) + 1);

		const auto itStart = std::lower_bound(_paths.begin(), _paths.end(), prefix);
		const auto itEnd = std::lower_bound(itStart, _paths.end(), prefixEnd);

		return std::span<const std::string>(itStart, itEnd);
	}

	void LooseFileCache::Release()
	{
		_paths.clear();
		_paths.shrink_to_fit();
		_loaded = false;
		if (Settings::Get().verboseLogging) {
			SKSE::log::info("R.A.P.I.D. cache released");
		}
	}

	LooseFileCache& GetLooseFileCache()
	{
		static LooseFileCache instance;
		return instance;
	}
}
