"""Bedrock (aws_sdk) auxiliary client.

Wraps the AWS Bedrock Converse API behind the OpenAI-compatible
``client.chat.completions.create()`` interface that ``call_llm()`` expects.

Installed by DOH's apply.py as ``agent/bedrock_aux_client.py``. Imported by the
patched ``agent/auxiliary_client.py`` to service the ``aws_sdk`` auth_type
(Bedrock provider).

This file is DOH-owned — not from upstream hermes-agent. It exists because
upstream's ``resolve_provider_client()`` has no handler for ``aux_sdk``, so
every auxiliary call (session_search, vision, compression, etc.) silently
falls back to None when the user selects Bedrock. See patches/README.md for
the full story (upstream PR #11700).
"""

import asyncio


class _BedrockCompletionsAdapter:
    """OpenAI-client-compatible adapter for AWS Bedrock Converse API."""

    def __init__(self, region: str, model: str):
        self._region = region
        self._model = model

    def create(self, **kwargs):
        from agent.bedrock_adapter import call_converse

        messages = kwargs.get("messages", [])
        model = kwargs.get("model", self._model)
        tools = kwargs.get("tools")
        max_tokens = (
            kwargs.get("max_tokens")
            or kwargs.get("max_completion_tokens")
            or 2000
        )
        temperature = kwargs.get("temperature")

        return call_converse(
            region=self._region,
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )


class _BedrockChatShim:
    def __init__(self, adapter: _BedrockCompletionsAdapter):
        self.completions = adapter


class BedrockAuxiliaryClient:
    """OpenAI-client-compatible wrapper over the Bedrock Converse API."""

    def __init__(self, region: str, model: str):
        adapter = _BedrockCompletionsAdapter(region, model)
        self.chat = _BedrockChatShim(adapter)
        # Checked by some callers but not meaningful for Bedrock.
        self.api_key = "bedrock-aws-sdk"
        self.base_url = f"bedrock://{region}"

    def close(self):
        pass


class _AsyncBedrockCompletionsAdapter:
    def __init__(self, sync_adapter: _BedrockCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs):
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncBedrockChatShim:
    def __init__(self, adapter: _AsyncBedrockCompletionsAdapter):
        self.completions = adapter


class AsyncBedrockAuxiliaryClient:
    def __init__(self, sync_wrapper: "BedrockAuxiliaryClient"):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncBedrockCompletionsAdapter(sync_adapter)
        self.chat = _AsyncBedrockChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url
