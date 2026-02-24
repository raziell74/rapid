#include "cache.h"
#include "bsa_hash.h"
#include "log.h"
#include "settings.h"

#include <zlib.h>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace RAPID
{
	namespace
	{
		constexpr std::uint32_t kRap2Version = 2;

		std::filesystem::path GetCachePath()
		{
			return Settings::GetConfigDirectory() / "rapid_vfs_cache.bin";
		}

		bool ReadCompressedCache(std::vector<std::uint8_t>& outBuffer)
		{
			const auto cachePath = GetCachePath();
			SKSE::log::info("R.A.P.I.D. cache lookup path: {}", cachePath.string());
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

		bool ParseRap2(const std::vector<std::uint8_t>& data, std::vector<std::string>& outPaths)
		{
			if (data.size() < 12) {
				SKSE::log::error("R.A.P.I.D. RAP2 cache payload too small for header");
				return false;
			}

			if (!(data[0] == 'R' && data[1] == 'A' && data[2] == 'P' && data[3] == '2')) {
				SKSE::log::error("R.A.P.I.D. RAP2 cache invalid magic");
				return false;
			}

			const std::uint32_t version = ReadU32LE(data, 4);
			if (version != kRap2Version) {
				SKSE::log::error(
					"R.A.P.I.D. RAP2 cache version mismatch (expected {}, got {})",
					kRap2Version,
					version);
				return false;
			}

			const std::uint32_t expectedCount = ReadU32LE(data, 8);
			std::size_t cursor = 12;

			outPaths.clear();
			outPaths.reserve(expectedCount);

			for (std::uint32_t i = 0; i < expectedCount; ++i) {
				if (cursor + sizeof(std::uint64_t) + sizeof(std::uint16_t) > data.size()) {
					SKSE::log::error("R.A.P.I.D. RAP2 cache truncated reading record header at index {}", i);
					return false;
				}

				cursor += sizeof(std::uint64_t);

				const std::uint16_t pathLength = ReadU16LE(data, cursor);
				cursor += sizeof(std::uint16_t);

				if (cursor + pathLength > data.size()) {
					SKSE::log::error("R.A.P.I.D. RAP2 cache truncated reading path bytes at index {}", i);
					return false;
				}

				std::string path(reinterpret_cast<const char*>(data.data() + cursor), pathLength);
				cursor += pathLength;
				if (!path.empty()) {
					outPaths.push_back(std::move(path));
				}
			}

			if (cursor < data.size()) {
				if (data.size() - cursor < sizeof(std::uint32_t)) {
					SKSE::log::error("R.A.P.I.D. RAP2 metadata trailer is truncated");
					return false;
				}
				const std::uint32_t metadataLen = ReadU32LE(data, data.size() - sizeof(std::uint32_t));
				if (metadataLen > data.size() - cursor - sizeof(std::uint32_t) && Settings::Get().verboseLogging) {
					SKSE::log::warn(
						"R.A.P.I.D. RAP2 metadata length appears invalid (len={}, trailing={})",
						metadataLen,
						data.size() - cursor - sizeof(std::uint32_t));
				}
			}

			return true;
		}

		bool ParseCacheEntries(
			const std::vector<std::uint8_t>& data,
			std::vector<std::string>& outPaths,
			CacheFormat& outFormat)
		{
			outFormat = CacheFormat::kUnknown;
			if (!ParseRap2(data, outPaths)) {
				return false;
			}
			outFormat = CacheFormat::kRap2;
			return true;
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

		if (!ParseCacheEntries(uncompressed, _paths, _format)) {
			return false;
		}

		if (_paths.empty()) {
			SKSE::log::warn("R.A.P.I.D. cache contains no entries");
			return false;
		}

		_hashToPathIndexes.clear();
		_hashToPathIndexes.reserve(_paths.size());
		for (std::uint32_t i = 0; i < _paths.size(); ++i) {
			const std::uint64_t hash = ComputeRapidHash64(_paths[i]);
			_hashToPathIndexes[hash].push_back(i);
		}
		_loaded = true;

		SKSE::log::info(
			"R.A.P.I.D. cache loaded from {}: {} paths (format={}, inflated={} bytes)",
			GetCachePath().string(),
			_paths.size(),
			static_cast<std::uint32_t>(_format),
			uncompressed.size());
		return true;
	}

	std::span<const std::string> LooseFileCache::GetAllPaths() const
	{
		if (!_loaded || _paths.empty()) {
			return {};
		}
		return std::span<const std::string>(_paths);
	}

	ResolveResult LooseFileCache::ResolvePath(const char* path) const
	{
		ResolveResult result{};
		if (!_loaded || _paths.empty() || !path) {
			return result;
		}

		const std::string normalized = NormalizePath(path);
		if (normalized.empty()) {
			return result;
		}

		const std::uint64_t hash = ComputeRapidHash64(normalized);
		const auto it = _hashToPathIndexes.find(hash);
		if (it == _hashToPathIndexes.end()) {
			return result;
		}

		result.collisionCandidates = it->second.size();
		for (const std::uint32_t index : it->second) {
			if (index < _paths.size() && _paths[index] == normalized) {
				result.path = &_paths[index];
				return result;
			}
		}
		return result;
	}

	std::size_t LooseFileCache::GetEntryCount() const
	{
		return _paths.size();
	}

	CacheFormat LooseFileCache::GetFormat() const
	{
		return _format;
	}

	void LooseFileCache::Release()
	{
		_paths.clear();
		_paths.shrink_to_fit();
		_hashToPathIndexes.clear();
		_loaded = false;
		_format = CacheFormat::kUnknown;
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
