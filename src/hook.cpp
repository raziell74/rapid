#include "cache.h"
#include "hook.h"
#include "log.h"
#include "location.h"
#include "settings.h"

#include <atomic>
#include <chrono>
#include <thread>

namespace RAPID::Hooks
{
	namespace
	{
		enum class InjectionState : std::uint8_t
		{
			kUninitialized = 0,
			kInitializing = 1,
			kInjected = 2,
			kNativeFallback = 3
		};

		std::atomic<InjectionState> g_injectionState{ InjectionState::kUninitialized };
		std::atomic<std::uint64_t> g_nativeTraversalCount{ 0 };
		std::atomic<std::uint64_t> g_nativeTraversalTotalMicroseconds{ 0 };
		std::atomic<std::uint64_t> g_nativeTraversalDiscoveredFiles{ 0 };
		std::atomic<std::uint64_t> g_nativeLoadedSyncFiles{ 0 };
		std::atomic<std::uint64_t> g_nativeLoadedAsyncFiles{ 0 };
		std::atomic<bool> g_nativeTraversalTimingFlushed{ false };
	}

	void AccumulateNativeTraversalTiming(std::uint64_t a_elapsedMicroseconds, std::uint64_t a_discoveredFiles)
	{
		g_nativeTraversalCount.fetch_add(1, std::memory_order_relaxed);
		g_nativeTraversalTotalMicroseconds.fetch_add(a_elapsedMicroseconds, std::memory_order_relaxed);
		g_nativeTraversalDiscoveredFiles.fetch_add(a_discoveredFiles, std::memory_order_relaxed);
	}

	void AccumulateNativeStreamLoad(bool a_async)
	{
		if (a_async) {
			g_nativeLoadedAsyncFiles.fetch_add(1, std::memory_order_relaxed);
			return;
		}
		g_nativeLoadedSyncFiles.fetch_add(1, std::memory_order_relaxed);
	}

	void FlushNativeTraversalTiming()
	{
		if (RAPID::Settings::Get().enabled || !RAPID::Settings::Get().performanceDiagnostics) {
			return;
		}
		if (g_nativeTraversalTimingFlushed.exchange(true, std::memory_order_acq_rel)) {
			return;
		}

		const std::uint64_t nativeTraversalCount = g_nativeTraversalCount.load(std::memory_order_relaxed);
		const std::uint64_t totalMicroseconds = g_nativeTraversalTotalMicroseconds.load(std::memory_order_relaxed);
		const std::uint64_t discoveredFiles = g_nativeTraversalDiscoveredFiles.load(std::memory_order_relaxed);
		const std::uint64_t loadedSyncFiles = g_nativeLoadedSyncFiles.load(std::memory_order_relaxed);
		const std::uint64_t loadedAsyncFiles = g_nativeLoadedAsyncFiles.load(std::memory_order_relaxed);
		const std::uint64_t loadedTotalFiles = loadedSyncFiles + loadedAsyncFiles;
		const double totalMilliseconds = static_cast<double>(totalMicroseconds) / 1000.0;

		SKSE::log::info(
			"R.A.P.I.D. performance diagnostics: native traversal events={}, discovered={}, loaded={} (sync={}, async={}), totalTraversalTimeMs={:.3f}",
			nativeTraversalCount,
			discoveredFiles,
			loadedTotalFiles,
			loadedSyncFiles,
			loadedAsyncFiles,
			totalMilliseconds);
	}

	bool InjectLooseFileCache(
		RE::BSResource::LooseFileLocation* a_this,
		RE::BSResource::LocationTraverser& a_traverser,
		const char* a_path)
	{
		if (!RAPID::Settings::Get().enabled) {
			return false;
		}

		while (true) {
			const InjectionState state = g_injectionState.load(std::memory_order_acquire);
			if (state == InjectionState::kInjected) {
				return true;
			}
			if (state == InjectionState::kNativeFallback) {
				return false;
			}
			if (state == InjectionState::kInitializing) {
				std::this_thread::yield();
				continue;
			}

			InjectionState expected = InjectionState::kUninitialized;
			if (g_injectionState.compare_exchange_strong(
					expected,
					InjectionState::kInitializing,
					std::memory_order_acq_rel,
					std::memory_order_acquire)) {
				break;
			}
		}

		const char* currentPath = a_path && a_path[0] != '\0' ? a_path : "ROOT";
		SKSE::log::info("R.A.P.I.D. first traversal intercepted at \"{}\"", currentPath);

		const auto t0 = std::chrono::steady_clock::now();

		auto& cache = RAPID::GetLooseFileCache();
		if (!cache.Load()) {
			g_injectionState.store(InjectionState::kNativeFallback, std::memory_order_release);
			SKSE::log::warn("R.A.P.I.D. cache unavailable; using native traversal for this session");
			return false;
		}

		const std::span<const std::string> paths = cache.GetAllPaths();
		if (paths.empty()) {
			g_injectionState.store(InjectionState::kNativeFallback, std::memory_order_release);
			SKSE::log::warn("R.A.P.I.D. cache is empty; using native traversal for this session");
			return false;
		}

		auto& rapidLocation = RAPID::GetRapidLocation();
		rapidLocation.BindLooseLocation(a_this);

		for (const auto& path : paths) {
			a_traverser.ProcessName(path.c_str(), rapidLocation);
		}
		const auto t1 = std::chrono::steady_clock::now();
		const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

		g_injectionState.store(InjectionState::kInjected, std::memory_order_release);
		if (Settings::Get().performanceDiagnostics || Settings::Get().verboseLogging) {
			SKSE::log::info("R.A.P.I.D. performance diagnostics: injected {} cached loose-file paths in {:.3f} ms", paths.size(), ms);
		}
		return true;
	}
}
