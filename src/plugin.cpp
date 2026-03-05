#include "cache.h"
#include "log.h"
#include "hook.h"
#include "location.h"
#include "settings.h"

#include <chrono>

namespace {
	std::chrono::steady_clock::time_point g_pluginLoadStart;
}

void MessageHandler(SKSE::MessagingInterface::Message* a_msg)
{
	switch (a_msg->type) {
	case SKSE::MessagingInterface::kDataLoaded:
		if (RAPID::Settings::Get().performanceDiagnostics) {
			const auto now = std::chrono::steady_clock::now();
			const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - g_pluginLoadStart);
			SKSE::log::info(
				"[PerformanceDiagnostics] Time from plugin load to kDataLoaded: {} ms",
				elapsed.count());
		}
		RAPID::Hooks::FlushNativeTraversalTiming();
		RAPID::GetLooseFileCache().Release();
		break;
	case SKSE::MessagingInterface::kPostLoad:
		break;
	case SKSE::MessagingInterface::kPreLoadGame:
		break;
	case SKSE::MessagingInterface::kPostLoadGame:
        break;
	case SKSE::MessagingInterface::kNewGame:
		break;
	}
}

SKSEPluginLoad(const SKSE::LoadInterface *skse) {
	g_pluginLoadStart = std::chrono::steady_clock::now();
	
	SKSE::Init(skse);
	SetupLog();

	if (!RAPID::Settings::Load()) {
		SKSE::log::error("RAPID settings failed to load; plugin continuing with defaults");
	}

	auto& rapidLocation = RAPID::GetRapidLocation();
	const bool locationRegistered = rapidLocation.Register();
	SKSE::log::info(
		"R.A.P.I.D. location registration {}",
		locationRegistered ? "ok" : "failed");

	// Allocate memory for the trampoline buffer. 
    // 14 bytes per hook is generally safe. We're only making 1 hook, so 64 bytes is plenty.
    SKSE::AllocTrampoline(64);

    // Call your hook installer
    RAPID::Hooks::LooseFileTraverse::Install();

	auto messaging = SKSE::GetMessagingInterface();
	if (!messaging->RegisterListener("SKSE", MessageHandler)) {
		return false;
	}

	return true;
}