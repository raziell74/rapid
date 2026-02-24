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
	}

	bool TryInjectLooseFileCache(
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

		const auto t0 = std::chrono::steady_clock::now();
		for (const auto& path : paths) {
			a_traverser.ProcessName(path.c_str(), rapidLocation);
		}
		const auto t1 = std::chrono::steady_clock::now();
		const double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();

		g_injectionState.store(InjectionState::kInjected, std::memory_order_release);
		SKSE::log::info("R.A.P.I.D. injected {} cached loose-file paths in {:.3f} ms", paths.size(), ms);
		return true;
	}
}
