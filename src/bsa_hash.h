#pragma once

#include <cstdint>
#include <cstddef>
#include <string>
#include <string_view>

namespace RAPID
{
	constexpr char ToLowerAscii(char c)
	{
		return (c >= 'A' && c <= 'Z') ? static_cast<char>(c + ('a' - 'A')) : c;
	}

	inline std::string NormalizePath(std::string_view rawPath)
	{
		std::size_t begin = 0;
		std::size_t end = rawPath.size();
		while (begin < end && (rawPath[begin] == ' ' || rawPath[begin] == '\t')) {
			++begin;
		}
		while (end > begin && (rawPath[end - 1] == ' ' || rawPath[end - 1] == '\t')) {
			--end;
		}
		rawPath = rawPath.substr(begin, end - begin);

		std::string normalized;
		normalized.reserve(rawPath.size());

		bool previousWasSlash = false;
		for (const char cRaw : rawPath) {
			char c = cRaw == '/' ? '\\' : ToLowerAscii(cRaw);
			if (c == '\\') {
				if (previousWasSlash) {
					continue;
				}
				previousWasSlash = true;
			} else {
				previousWasSlash = false;
			}
			normalized.push_back(c);
		}

		while (!normalized.empty() && normalized.front() == '\\') {
			normalized.erase(normalized.begin());
		}

		while (!normalized.empty() && normalized.back() == '\\') {
			normalized.pop_back();
		}

		constexpr std::string_view kDataPrefix = "data\\";
		if (normalized.size() < kDataPrefix.size() ||
		    normalized.compare(0, kDataPrefix.size(), kDataPrefix) != 0) {
			normalized.insert(0, kDataPrefix);
		}

		return normalized;
	}

	inline std::string NormalizeTraversalPrefix(const char* traversalPath)
	{
		if (!traversalPath || traversalPath[0] == '\0') {
			return {};
		}

		std::string prefix = NormalizePath(traversalPath);
		constexpr std::string_view kDataRoot = "data\\root";
		if (prefix == kDataRoot) {
			return {};
		}
		if (!prefix.empty() && prefix.back() != '\\') {
			prefix.push_back('\\');
		}
		return prefix;
	}

	inline std::uint64_t ComputeRapidHash64(std::string_view canonicalPath)
	{
		std::string normalized(canonicalPath);

		const auto dotPos = normalized.find_last_of('.');
		const std::string root = dotPos == std::string::npos ? normalized : normalized.substr(0, dotPos);
		const std::string ext = dotPos == std::string::npos ? std::string{} : normalized.substr(dotPos);

		std::uint32_t low = 0;
		if (!root.empty()) {
			low = static_cast<std::uint8_t>(root.back());
			if (root.size() > 2) {
				low |= static_cast<std::uint32_t>(static_cast<std::uint8_t>(root[root.size() - 2])) << 8;
			}
			low |= static_cast<std::uint32_t>(root.size()) << 16;
			low |= static_cast<std::uint32_t>(static_cast<std::uint8_t>(root.front())) << 24;
		}

		if (ext == ".kf") {
			low |= 0x80;
		} else if (ext == ".nif") {
			low |= 0x8000;
		} else if (ext == ".dds") {
			low |= 0x8080;
		} else if (ext == ".wav") {
			low |= 0x80000000;
		}

		std::uint32_t midHash = 0;
		for (std::size_t i = 1; i + 2 < root.size(); ++i) {
			midHash = midHash * 0x1003F + static_cast<std::uint8_t>(root[i]);
		}

		std::uint32_t extHash = 0;
		for (const char c : ext) {
			extHash = extHash * 0x1003F + static_cast<std::uint8_t>(c);
		}

		const std::uint32_t high = midHash + extHash;
		return (static_cast<std::uint64_t>(high) << 32) | low;
	}
}
