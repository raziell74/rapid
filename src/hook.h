#pragma once

#include "cache.h"
#include "RE/Skyrim.h"
#include "SKSE/SKSE.h"
#include "settings.h"

#include <chrono>
#include <cstdint>

namespace RAPID::Hooks
{
    bool InjectLooseFileCache(
        RE::BSResource::LooseFileLocation* a_this,
        RE::BSResource::LocationTraverser& a_traverser,
        const char* a_path);

    void AccumulateNativeTraversalTiming(std::uint64_t a_elapsedMicroseconds, std::uint64_t a_discoveredFiles);
    void AccumulateNativeStreamLoad(bool a_async);
    void FlushNativeTraversalTiming();

    class LooseFileTraverse
    {
    public:
        using DoTraversePrefix_t = RE::BSResource::ErrorCode(*)(
            RE::BSResource::LooseFileLocation*, 
            const char*, 
            RE::BSResource::LocationTraverser&
        );
        using DoCreateStream_t = RE::BSResource::ErrorCode(*)(
            RE::BSResource::LooseFileLocation*,
            const char*,
            RE::BSTSmartPointer<RE::BSResource::Stream>&,
            RE::BSResource::Location*&,
            bool
        );
        using DoCreateAsyncStream_t = RE::BSResource::ErrorCode(*)(
            RE::BSResource::LooseFileLocation*,
            const char*,
            RE::BSTSmartPointer<RE::BSResource::AsyncStream>&,
            RE::BSResource::Location*&,
            bool
        );

        static void Install()
        {
            REL::Relocation<std::uintptr_t> vtable{ RE::VTABLE_BSResource__LooseFileLocation[0] };
            
            _DoTraversePrefix = reinterpret_cast<DoTraversePrefix_t>(
                vtable.write_vfunc(0x05, reinterpret_cast<std::uintptr_t>(Hook_DoTraversePrefix))
            );
            _DoCreateStream = reinterpret_cast<DoCreateStream_t>(
                vtable.write_vfunc(0x03, reinterpret_cast<std::uintptr_t>(Hook_DoCreateStream))
            );
            _DoCreateAsyncStream = reinterpret_cast<DoCreateAsyncStream_t>(
                vtable.write_vfunc(0x04, reinterpret_cast<std::uintptr_t>(Hook_DoCreateAsyncStream))
            );
            
            SKSE::log::info("LooseFileLocation native metrics hooks installed successfully");
        }

    private:
        class CountingTraverser final : public RE::BSResource::LocationTraverser
        {
        public:
            explicit CountingTraverser(RE::BSResource::LocationTraverser& a_inner) :
                _inner(a_inner)
            {}

            void ProcessName(const char* a_name, RE::BSResource::Location& a_location) override
            {
                ++_count;
                _inner.ProcessName(a_name, a_location);
            }

            [[nodiscard]] std::uint64_t GetCount() const
            {
                return _count;
            }

        private:
            RE::BSResource::LocationTraverser& _inner;
            std::uint64_t _count{ 0 };
        };

        static RE::BSResource::ErrorCode Hook_DoTraversePrefix(
            RE::BSResource::LooseFileLocation* a_this, 
            const char* a_path, 
            RE::BSResource::LocationTraverser& a_traverser)
        {
            bool isCacheInjected = InjectLooseFileCache(a_this, a_traverser, a_path);

            if (isCacheInjected) {
                return RE::BSResource::ErrorCode::kNone; 
            }

            if (!RAPID::Settings::Get().enabled && RAPID::Settings::Get().performanceDiagnostics) {
                CountingTraverser countingTraverser(a_traverser);
                const auto t0 = std::chrono::steady_clock::now();
                const RE::BSResource::ErrorCode nativeResult = _DoTraversePrefix(a_this, a_path, countingTraverser);
                const auto t1 = std::chrono::steady_clock::now();
                const auto elapsedUs = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
                AccumulateNativeTraversalTiming(static_cast<std::uint64_t>(elapsedUs), countingTraverser.GetCount());
                return nativeResult;
            }

            return _DoTraversePrefix(a_this, a_path, a_traverser);
        }

        static RE::BSResource::ErrorCode Hook_DoCreateStream(
            RE::BSResource::LooseFileLocation* a_this,
            const char* a_path,
            RE::BSTSmartPointer<RE::BSResource::Stream>& a_stream,
            RE::BSResource::Location*& a_location,
            bool a_readOnly)
        {
            const RE::BSResource::ErrorCode nativeResult = _DoCreateStream(a_this, a_path, a_stream, a_location, a_readOnly);
            if (!RAPID::Settings::Get().enabled &&
                RAPID::Settings::Get().performanceDiagnostics &&
                nativeResult == RE::BSResource::ErrorCode::kNone) {
                AccumulateNativeStreamLoad(false);
            }
            return nativeResult;
        }

        static RE::BSResource::ErrorCode Hook_DoCreateAsyncStream(
            RE::BSResource::LooseFileLocation* a_this,
            const char* a_path,
            RE::BSTSmartPointer<RE::BSResource::AsyncStream>& a_stream,
            RE::BSResource::Location*& a_location,
            bool a_readOnly)
        {
            const RE::BSResource::ErrorCode nativeResult = _DoCreateAsyncStream(a_this, a_path, a_stream, a_location, a_readOnly);
            if (!RAPID::Settings::Get().enabled &&
                RAPID::Settings::Get().performanceDiagnostics &&
                nativeResult == RE::BSResource::ErrorCode::kNone) {
                AccumulateNativeStreamLoad(true);
            }
            return nativeResult;
        }

        static inline DoTraversePrefix_t _DoTraversePrefix;
        static inline DoCreateStream_t _DoCreateStream;
        static inline DoCreateAsyncStream_t _DoCreateAsyncStream;
    };
}
