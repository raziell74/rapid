#pragma once

#include <intrin.h>
#include <cstdint>

namespace RAPID
{
	enum class SimdTier
	{
		kScalar = 0,
		kAVX2   = 1,
		kAVX512 = 2
	};

	class CpuFeatures
	{
	public:
		SimdTier highestTier = SimdTier::kScalar;
		bool hasAVX2     = false;
		bool hasBMI1     = false;
		bool hasAVX512BW = false;
		bool hasAVX512VL = false;

		CpuFeatures()
		{
			int regs[4] = {};

			__cpuid(regs, 0);
			if (regs[0] < 7)
				return;

			__cpuid(regs, 1);
			if (!(regs[2] & (1 << 27)))
				return;

			const auto xcr0 = _xgetbv(0);
			const bool osAVX2   = (xcr0 & 0x6) == 0x6;
			const bool osAVX512 = (xcr0 & 0xE6) == 0xE6;

			__cpuidex(regs, 7, 0);
			const auto ebx = static_cast<std::uint32_t>(regs[1]);

			const bool hwAVX2    = (ebx & (1u << 5))  != 0;
			const bool hwAVX512F = (ebx & (1u << 16)) != 0;

			hasAVX2     = hwAVX2 && osAVX2;
			hasBMI1     = (ebx & (1u << 3))  != 0;
			hasAVX512BW = (ebx & (1u << 30)) != 0;
			hasAVX512VL = (ebx & (1u << 31)) != 0;

			if (hwAVX512F && osAVX512)
				highestTier = SimdTier::kAVX512;
			else if (hasAVX2)
				highestTier = SimdTier::kAVX2;
		}
	};

	inline CpuFeatures& GetCpuFeatures()
	{
		static CpuFeatures instance;
		return instance;
	}
}
