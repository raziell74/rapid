#pragma once

#include "cache.h"
#include "RE/Skyrim.h"
#include "SKSE/SKSE.h"

namespace RAPID::Hooks
{
    bool TryInjectLooseFileCache(
        RE::BSResource::LooseFileLocation* a_this,
        RE::BSResource::LocationTraverser& a_traverser,
        const char* a_path);

    class LooseFileTraverse
    {
    public:
        // 1. Define the exact signature of the function as a type alias so C++ doesn't panic.
        using DoTraversePrefix_t = RE::BSResource::ErrorCode(*)(
            RE::BSResource::LooseFileLocation*, 
            const char*, 
            RE::BSResource::LocationTraverser&
        );

        static void Install()
        {
            REL::Relocation<std::uintptr_t> vtable{ RE::VTABLE_BSResource__LooseFileLocation[0] };
            
            // 2. Write the hook, and strictly cast the returned vanilla address to our function pointer type.
            _DoTraversePrefix = reinterpret_cast<DoTraversePrefix_t>(
                vtable.write_vfunc(0x05, reinterpret_cast<std::uintptr_t>(Hook_DoTraversePrefix))
            );
            
            SKSE::log::info("LooseFileLocation::DoTraversePrefix VTable Hook installed successfully!");
        }

    private:
        static RE::BSResource::ErrorCode Hook_DoTraversePrefix(
            RE::BSResource::LooseFileLocation* a_this, 
            const char* a_path, 
            RE::BSResource::LocationTraverser& a_traverser)
        {
            bool isCacheInjected = TryInjectLooseFileCache(a_this, a_traverser, a_path);

            if (isCacheInjected) {
                return RE::BSResource::ErrorCode::kNone; 
            }

            return _DoTraversePrefix(a_this, a_path, a_traverser);
        }

        // 4. Declare the raw function pointer variable
        static inline DoTraversePrefix_t _DoTraversePrefix;
    };
}