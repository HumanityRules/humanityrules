/**
 * Chat streaming functionality using SSE and streaming-markdown.
 * Handles real-time message streaming, markdown rendering, and tool execution display.
 */
import { parser, parser_write, parser_end, default_renderer } from 'https://cdn.jsdelivr.net/npm/streaming-markdown@0.2.15/smd.min.js';

const container = document.getElementById('messages-container');
const messages = document.getElementById('messages');

function scrollToBottom() {
    container.scrollTop = container.scrollHeight;
}

/**
 * Render markdown content on page load (for messages loaded from database).
 * Uses streaming-markdown library for consistency with live streaming.
 */
function renderStoredMarkdown() {
    document.querySelectorAll('[data-markdown-content]').forEach(el => {
        const scriptEl = el.querySelector('script[type="text/markdown"]');
        if (scriptEl) {
            const markdown = scriptEl.textContent;
            scriptEl.remove();
            const renderer = default_renderer(el);
            const p = parser(renderer);
            parser_write(p, markdown);
            parser_end(p);
        }
    });
}

/**
 * Process OOB (out-of-band) elements from HTML string.
 * These elements have hx-swap-oob attribute and should replace elements by ID.
 */
function processOobElements(fragment) {
    const oobElements = fragment.querySelectorAll('[hx-swap-oob]');
    oobElements.forEach(oob => {
        const targetId = oob.id;
        const target = document.getElementById(targetId);
        if (target) {
            oob.removeAttribute('hx-swap-oob');
            target.outerHTML = oob.outerHTML;
        }
        oob.remove();
    });
}

/**
 * Handle sse-thinking events (show thinking indicator).
 * Why JS instead of HTMX? The thinking indicator HTML is entirely OOB
 * (hx-swap-oob="outerHTML" on root element). HTMX processes OOB AFTER
 * the default swap, so it briefly appends the content to #messages,
 * then moves it to #thinking-indicator. This causes a visible flash.
 * By handling in JS, we skip the default swap and apply OOB directly.
 */
function handleThinking(data) {
    const template = document.createElement('template');
    template.innerHTML = data;
    const oobElement = template.content.firstElementChild;
    if (oobElement && oobElement.id === 'thinking-indicator') {
        const target = document.getElementById('thinking-indicator');
        if (target) {
            oobElement.removeAttribute('hx-swap-oob');
            target.outerHTML = oobElement.outerHTML;
        }
    }
    scrollToBottom();
}

/**
 * Handle sse-start events (initialize streaming markdown parser).
 * Creates the streaming message container and sets up the markdown parser.
 */
function handleStart(data) {
    const template = document.createElement('template');
    template.innerHTML = data;
    const fragment = template.content;

    // Handle OOB elements (thinking indicator reset)
    processOobElements(fragment);

    // Append the main streaming message
    const streamingMessage = fragment.firstElementChild;
    if (streamingMessage) {
        messages.appendChild(streamingMessage);
    }

    // Initialize streaming-markdown parser
    const streamingText = document.getElementById('streaming-text');
    if (streamingText) {
        window.smdRenderer = default_renderer(streamingText);
        window.smdParser = parser(window.smdRenderer);
    }
    scrollToBottom();
}

/**
 * Handle sse-text-delta events (streaming text chunks via markdown parser).
 */
function handleTextDelta(data) {
    try {
        const parsed = JSON.parse(data);
        if (parsed.text && window.smdParser) {
            parser_write(window.smdParser, parsed.text);
            scrollToBottom();
        }
    } catch (e) {
        console.error('Failed to parse sse-text-delta:', e);
    }
}

/**
 * Handle sse-text-flush events (finalize streaming before tool call).
 * Completes the current markdown section so a tool can be displayed.
 */
function handleTextFlush() {
    // Finalize markdown parser
    if (window.smdParser) {
        parser_end(window.smdParser);
        window.smdParser = null;
        window.smdRenderer = null;
    }
    const streamingMessage = document.getElementById('streaming-message');
    if (streamingMessage) {
        const streamingText = streamingMessage.querySelector('#streaming-text');
        // If empty (no text before tool call), remove the container entirely
        if (streamingText && !streamingText.textContent.trim()) {
            streamingMessage.remove();
            return;
        }
        // Otherwise finalize: release IDs
        streamingMessage.removeAttribute('id');
        if (streamingText) streamingText.removeAttribute('id');
    }
}

/**
 * Handle sse-complete events (finalize final streaming message).
 * Cleans up the streaming state and releases DOM IDs.
 */
function handleComplete() {
    // Finalize markdown parser
    if (window.smdParser) {
        parser_end(window.smdParser);
        window.smdParser = null;
        window.smdRenderer = null;
    }
    // Reset thinking indicator to empty placeholder (so it can be reused)
    const thinkingIndicator = document.getElementById('thinking-indicator');
    if (thinkingIndicator) thinkingIndicator.outerHTML = '<div id="thinking-indicator"></div>';
    // Finalize streaming message if present
    const streamingMessage = document.getElementById('streaming-message');
    if (streamingMessage) {
        streamingMessage.classList.remove('streaming-active');
        streamingMessage.removeAttribute('id');
        const streamingText = streamingMessage.querySelector('#streaming-text');
        if (streamingText) streamingText.removeAttribute('id');
    }
}

// Initialize on page load
renderStoredMarkdown();
scrollToBottom();

// Scroll when new content is added (form submission or SSE)
container.addEventListener('htmx:afterSwap', scrollToBottom);
container.addEventListener('htmx:sseMessage', scrollToBottom);

// Handle custom SSE events that need JavaScript processing
messages.addEventListener('htmx:sseBeforeMessage', function(event) {
    const eventType = event.detail.type;

    if (eventType === 'sse-thinking') {
        event.preventDefault();
        handleThinking(event.detail.data);
        return;
    }

    if (eventType === 'sse-start') {
        event.preventDefault();
        handleStart(event.detail.data);
        return;
    }

    if (eventType === 'sse-text-delta') {
        event.preventDefault();
        handleTextDelta(event.detail.data);
        return;
    }

    if (eventType === 'sse-text-flush') {
        event.preventDefault();
        handleTextFlush();
        return;
    }

    if (eventType === 'sse-complete') {
        event.preventDefault();
        handleComplete();
        return;
    }
});
