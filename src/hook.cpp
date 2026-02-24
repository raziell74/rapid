#include "cache.h"
#include "bsa_hash.h"
#include "hook.h"
#include "log.h"
#include "location.h"
#include "settings.h"

#include <chrono>

namespace RAPID::Hooks
{
	bool TryInjectLooseFileCache(
		RE::BSResource::LooseFileLocation* a_this,
		RE::BSResource::LocationTraverser& a_traverser,
		const char* a_path)
	{
		const char* currentPath = a_path && a_path[0] != '\0' ? a_path : "ROOT";
		const bool verbose = RAPID::Settings::Get().verboseLogging;

		if (verbose) {
			SKSE::log::info("R.A.P.I.D. TryInjectLooseFileCache invoked for prefix: \"{}\"", currentPath);
		}

		if (!RAPID::Settings::Get().enabled) {
			if (verbose) {
				SKSE::log::info("R.A.P.I.D. disabled; skipping cache injection for \"{}\"", currentPath);
			}
			return false;
		}

		auto& cache = RAPID::GetLooseFileCache();
		if (!cache.Load()) {
			SKSE::log::warn("R.A.P.I.D. cache not loaded; skipping injection for \"{}\"", currentPath);
			return false;
		}

		auto& rapidLocation = RAPID::GetRapidLocation();
		rapidLocation.BindLooseLocation(a_this);

		const bool perfDiag = RAPID::Settings::Get().performanceDiagnostics;
		const auto t0 = perfDiag ? std::chrono::steady_clock::now() : std::chrono::steady_clock::time_point{};

		const std::span<const std::string> paths = cache.GetPathsForPrefix(a_path);
		if (verbose) {
			const std::string normalizedPrefix = NormalizeTraversalPrefix(a_path);
			SKSE::log::info(
				"R.A.P.I.D. traversal prefix raw=\"{}\" normalized=\"{}\" cacheEntries={} matchCount={}",
				a_path ? a_path : "(null)",
				normalizedPrefix,
				cache.GetEntryCount(),
				paths.size());
		}

		if (paths.empty()) {
			SKSE::log::info(
				"R.A.P.I.D. no paths in cache for prefix \"{}\" (raw a_path \"{}\"); falling back to vanilla traversal",
				currentPath,
				a_path ? a_path : "(null)");
			return false;
		}

		for (const auto& path : paths) {
			a_traverser.ProcessName(path.c_str(), rapidLocation);
		}

		if (verbose) {
			SKSE::log::info("R.A.P.I.D. injected {} loose-file paths for prefix \"{}\"", paths.size(), currentPath);
		}

		if (perfDiag && paths.size() > 0) {
			const auto t1 = std::chrono::steady_clock::now();
			const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
			SKSE::log::info(
				"R.A.P.I.D. traversal prefix \"{}\": {} paths in {:.3f} ms",
				currentPath,
				paths.size(),
				ms);
		}
		return true;
	}
}
