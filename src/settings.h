#pragma once

#include "PCH.h"

#include <SimpleIni.h>

namespace RAPID::Settings
{
	struct Config
	{
		bool enabled{ true };
		bool verboseLogging{ false };
		bool performanceDiagnostics{ false };
	};

	inline Config& Get()
	{
		static Config config{};
		return config;
	}

	inline std::filesystem::path GetGameDataDirectory()
	{
		const auto cwd = std::filesystem::current_path();
		if (cwd.filename() == "Data"sv) {
			return cwd;
		}

		const auto candidate = cwd / "Data";
		if (std::filesystem::exists(candidate) && std::filesystem::is_directory(candidate)) {
			return candidate;
		}

		return cwd;
	}

	inline std::filesystem::path GetConfigDirectory()
	{
		return GetGameDataDirectory() / "SKSE" / "Plugins" / "RAPID";
	}

	inline std::filesystem::path GetConfigFilePath()
	{
		return GetConfigDirectory() / "config.ini";
	}

	inline void WriteDefaultIni(const std::filesystem::path& iniPath)
	{
		CSimpleIniA ini;
		ini.SetUnicode();
		ini.SetBoolValue("General", "Enabled", true);
		ini.SetBoolValue("General", "VerboseLogging", false);
		ini.SetBoolValue("General", "PerformanceDiagnostics", false);
		const auto rc = ini.SaveFile(iniPath.string().c_str());
		if (rc < 0) {
			SKSE::log::error("Failed to create default INI at {}", iniPath.string());
		}
	}

	inline bool Load()
	{
		const auto configDir = GetConfigDirectory();
		const auto iniPath = GetConfigFilePath();

		std::error_code ec;
		std::filesystem::create_directories(configDir, ec);
		if (ec) {
			SKSE::log::error("Failed to create config directory {}: {}", configDir.string(), ec.message());
			return false;
		}

		if (!std::filesystem::exists(iniPath)) {
			WriteDefaultIni(iniPath);
		}

		CSimpleIniA ini;
		ini.SetUnicode();
		const auto loadRc = ini.LoadFile(iniPath.string().c_str());
		if (loadRc < 0) {
			SKSE::log::error("Failed to load INI {}", iniPath.string());
			return false;
		}

		auto& config = Get();
		config.enabled = ini.GetBoolValue("General", "Enabled", true);
		config.verboseLogging = ini.GetBoolValue("General", "VerboseLogging", false);
		config.performanceDiagnostics = ini.GetBoolValue("General", "PerformanceDiagnostics", false);

		SKSE::log::info(
			"Settings loaded from {} (enabled={}, verboseLogging={}, performanceDiagnostics={})",
			iniPath.string(),
			config.enabled,
			config.verboseLogging,
			config.performanceDiagnostics);

		return true;
	}
}
