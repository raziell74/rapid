#include "log.h"
#include "hook.h"
#include "settings.h"


void OnDataLoaded()
{
	LogVerbose("RAPID OnDataLoaded: installing traversal hook");
	RAPID::Hook::Install();
}

void MessageHandler(SKSE::MessagingInterface::Message* a_msg)
{
	LogVerbose("RAPID received SKSE message: {}", static_cast<std::uint32_t>(a_msg->type));
	switch (a_msg->type) {
	case SKSE::MessagingInterface::kDataLoaded:
		OnDataLoaded();
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
	SKSE::Init(skse);
	SetupLog();

	if (!RAPID::Settings::Load()) {
		SKSE::log::error("RAPID settings failed to load; plugin continuing with defaults");
	}

	auto messaging = SKSE::GetMessagingInterface();
	if (!messaging->RegisterListener("SKSE", MessageHandler)) {
		return false;
	}

	return true;
}