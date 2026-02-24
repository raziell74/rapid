#pragma once

#include "PCH.h"

namespace RAPID
{
	class RapidLocation final : public RE::BSResource::Location
	{
	public:
		RE::BSResource::ErrorCode DoCreateStream(
			const char* a_path,
			RE::BSTSmartPointer<RE::BSResource::Stream>& a_stream,
			RE::BSResource::Location*& a_location,
			bool a_readOnly) override;

		RE::BSResource::ErrorCode DoCreateAsyncStream(
			const char* a_path,
			RE::BSTSmartPointer<RE::BSResource::AsyncStream>& a_out,
			RE::BSResource::Location*& a_location,
			bool a_readOnly) override;

		RE::BSResource::ErrorCode DoTraversePrefix(
			const char* a_path,
			RE::BSResource::LocationTraverser& a_traverser) override;

		RE::BSResource::ErrorCode DoGetInfo1(
			const char* a_path,
			RE::BSResource::Info& a_info,
			RE::BSResource::Location*& a_location) override;

		RE::BSResource::ErrorCode DoGetInfo2(
			const char* a_path,
			RE::BSResource::Info& a_info,
			RE::BSResource::LocationTraverser* a_traverser) override;

		RE::BSResource::ErrorCode DoDelete(const char* a_path) override;
		const char* DoGetName() const override;
		std::uint32_t DoGetMinimumAsyncPacketSize() const override;

		void BindLooseLocation(RE::BSResource::LooseFileLocation* location);
		bool Register();
		bool IsRegistered() const;

	private:
		RE::BSResource::LooseFileLocation* _looseLocation{ nullptr };
		bool _registered{ false };
	};

	RapidLocation& GetRapidLocation();
}
