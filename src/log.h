#pragma once

#include <source_location>

#include <fmt/core.h>
#include <fmt/format.h>
#include <spdlog/sinks/basic_file_sink.h>

#include "settings.h"

inline void SetupLog() {
    auto logsFolder = SKSE::log::log_directory();
    if (!logsFolder) SKSE::stl::report_and_fail("SKSE log_directory not provided, logs disabled.");
    auto pluginName = SKSE::PluginDeclaration::GetSingleton()->GetName();
    auto logFilePath = *logsFolder / std::format("{}.log", pluginName);
    auto fileLoggerPtr = std::make_shared<spdlog::sinks::basic_file_sink_mt>(logFilePath.string(), true);
    auto loggerPtr = std::make_shared<spdlog::logger>("log", std::move(fileLoggerPtr));
    spdlog::set_default_logger(std::move(loggerPtr));
    spdlog::set_level(spdlog::level::trace);
    spdlog::flush_on(spdlog::level::trace);
}

inline void LogVerbose(
	const std::source_location& loc,
	const char* msg)
{
	if (RAPID::Settings::Get().verboseLogging) {
		SKSE::log::info("{} ({}:{})", msg, loc.file_name(), loc.line());
	}
}

inline void LogVerbose(const char* msg)
{
	LogVerbose(std::source_location::current(), msg);
}

template<typename... Args>
void LogVerbose(
	const std::source_location& loc,
	fmt::format_string<Args...> fmt,
	Args&&... args)
{
	if (RAPID::Settings::Get().verboseLogging) {
		SKSE::log::info("{} ({}:{})", fmt::format(fmt, std::forward<Args>(args)...), loc.file_name(), loc.line());
	}
}

template<typename... Args>
void LogVerbose(
	fmt::format_string<Args...> fmt,
	Args&&... args)
{
	LogVerbose(std::source_location::current(), fmt, std::forward<Args>(args)...);
}

inline void LogPerformanceDiagnostics(bool a_isRapidPath, const char* a_mode, std::size_t a_looseFileCount, double a_executionMs)
{
	const char* title = a_isRapidPath ? "RAPID Performance" : "Vanilla Performance";
	SKSE::log::info("========== {} ==========", title);
	SKSE::log::info("Mode: {}", a_mode);
	SKSE::log::info("Loose file count: {}", a_looseFileCount);
	SKSE::log::info("Execution time: {:.3f} ms", a_executionMs);
	SKSE::log::info("========================================");
}
