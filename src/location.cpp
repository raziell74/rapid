#include "location.h"

#include "bsa_hash.h"
#include "cache.h"
#include "settings.h"

namespace RAPID
{
	namespace
	{
		constexpr std::uint64_t kDetailedHotPathLogs = 100;
		constexpr std::uint64_t kPeriodicHotPathLogs = 5000;
	}

	RE::BSResource::ErrorCode RapidLocation::DoCreateStream(
		const char* a_path,
		RE::BSTSmartPointer<RE::BSResource::Stream>& a_stream,
		RE::BSResource::Location*& a_location,
		bool a_readOnly)
	{
		const auto requestCount = _requests.fetch_add(1) + 1;
		const std::string normalizedInput = NormalizePath(a_path ? a_path : "");
		const std::uint64_t inputHash = normalizedInput.empty() ? 0 : ComputeRapidHash64(normalizedInput);
		const ResolveResult resolve = GetLooseFileCache().ResolvePath(a_path);
		if (!resolve.path) {
			_misses.fetch_add(1);
			return RE::BSResource::ErrorCode::kNotExist;
		}

		if (resolve.collisionCandidates > 1) {
			_collisions.fetch_add(1);
		}

		if (!_looseLocation) {
			SKSE::log::warn("R.A.P.I.D. DoCreateStream has no bound loose location");
			return RE::BSResource::ErrorCode::kUnsupported;
		}

		const auto result = _looseLocation->DoCreateStream(resolve.path->c_str(), a_stream, a_location, a_readOnly);
		if (result == RE::BSResource::ErrorCode::kNone) {
			_hits.fetch_add(1);
		} else {
			_misses.fetch_add(1);
		}

		return result;
	}

	RE::BSResource::ErrorCode RapidLocation::DoCreateAsyncStream(
		const char* a_path,
		RE::BSTSmartPointer<RE::BSResource::AsyncStream>& a_out,
		RE::BSResource::Location*& a_location,
		bool a_readOnly)
	{
		const ResolveResult resolve = GetLooseFileCache().ResolvePath(a_path);
		if (!resolve.path || !_looseLocation) {
			return RE::BSResource::ErrorCode::kNotExist;
		}
		return _looseLocation->DoCreateAsyncStream(resolve.path->c_str(), a_out, a_location, a_readOnly);
	}

	RE::BSResource::ErrorCode RapidLocation::DoTraversePrefix(
		const char* a_path,
		RE::BSResource::LocationTraverser& a_traverser)
	{
		const std::span<const std::string> paths = GetLooseFileCache().GetPathsForPrefix(a_path);
		if (paths.empty()) {
			return RE::BSResource::ErrorCode::kNotExist;
		}

		for (const auto& path : paths) {
			a_traverser.ProcessName(path.c_str(), *this);
		}
		return RE::BSResource::ErrorCode::kNone;
	}

	RE::BSResource::ErrorCode RapidLocation::DoGetInfo1(
		const char* a_path,
		RE::BSResource::Info& a_info,
		RE::BSResource::Location*& a_location)
	{
		const ResolveResult resolve = GetLooseFileCache().ResolvePath(a_path);
		if (!resolve.path || !_looseLocation) {
			return RE::BSResource::ErrorCode::kNotExist;
		}
		return _looseLocation->DoGetInfo1(resolve.path->c_str(), a_info, a_location);
	}

	RE::BSResource::ErrorCode RapidLocation::DoGetInfo2(
		const char* a_path,
		RE::BSResource::Info& a_info,
		RE::BSResource::LocationTraverser* a_traverser)
	{
		const ResolveResult resolve = GetLooseFileCache().ResolvePath(a_path);
		if (!resolve.path || !_looseLocation) {
			return RE::BSResource::ErrorCode::kNotExist;
		}
		return _looseLocation->DoGetInfo2(resolve.path->c_str(), a_info, a_traverser);
	}

	RE::BSResource::ErrorCode RapidLocation::DoDelete(const char*)
	{
		return RE::BSResource::ErrorCode::kUnsupported;
	}

	const char* RapidLocation::DoGetName() const
	{
		return "RAPIDLocation";
	}

	std::uint32_t RapidLocation::DoGetMinimumAsyncPacketSize() const
	{
		return 0;
	}

	void RapidLocation::BindLooseLocation(RE::BSResource::LooseFileLocation* location)
	{
		if (Settings::Get().verboseLogging && _looseLocation != location) {
			SKSE::log::info(
				"R.A.P.I.D. binding loose location old={} new={} prefix=\"{}\"",
				static_cast<const void*>(_looseLocation),
				static_cast<const void*>(location),
				location ? location->prefix.c_str() : "(null)");
		}
		_looseLocation = location;
	}

	bool RapidLocation::Register()
	{
		if (_registered) {
			return true;
		}
		_registered = true;
		SKSE::log::info("R.A.P.I.D. custom location registered (hook-backed)");
		return true;
	}

	bool RapidLocation::IsRegistered() const
	{
		return _registered;
	}

	RapidLocation& GetRapidLocation()
	{
		static RapidLocation instance;
		return instance;
	}
}
