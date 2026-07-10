#!/bin/bash

# Expand one stable HUMR preset into the concrete values used to render Hermes
# config.yaml. Blueprint/task configuration persists only HUMR_LLM_PRESET.
resolve_humr_llm_preset() {
    local preset="${1:-}"

    unset HUMR_LLM_PROVIDER HUMR_LLM_MODEL HUMR_LLM_BASE_URL
    unset HUMR_AUX_PROVIDER HUMR_AUX_MODEL HUMR_AUX_BASE_URL

    case "$preset" in
        codex)
            HUMR_LLM_PROVIDER="openai-codex"
            HUMR_LLM_MODEL="gpt-5.5"
            HUMR_LLM_BASE_URL=""
            HUMR_AUX_PROVIDER="openai-codex"
            HUMR_AUX_MODEL="gpt-5.4-mini"
            HUMR_AUX_BASE_URL=""
            ;;
        bedrock)
            HUMR_LLM_PROVIDER="bedrock"
            HUMR_LLM_MODEL="global.anthropic.claude-sonnet-5"
            HUMR_LLM_BASE_URL=""
            HUMR_AUX_PROVIDER="bedrock"
            HUMR_AUX_MODEL="global.anthropic.claude-sonnet-5"
            HUMR_AUX_BASE_URL=""
            ;;
        *)
            return 1
            ;;
    esac

    export HUMR_LLM_PROVIDER HUMR_LLM_MODEL HUMR_LLM_BASE_URL
    export HUMR_AUX_PROVIDER HUMR_AUX_MODEL HUMR_AUX_BASE_URL
}
