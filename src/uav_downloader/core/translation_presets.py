"""Existing editable compatible endpoint templates shared by frontends."""
COMPATIBLE_PRESETS = {
    "DeepSeek": (
        "https://api.deepseek.com/chat/completions",
        "deepseek-v4-flash",
    ),
    "OpenRouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        "openai/gpt-4.1-mini",
    ),
    "Groq": (
        "https://api.groq.com/openai/v1/chat/completions",
        "llama-3.3-70b-versatile",
    ),
    "Ollama": (
        "http://127.0.0.1:11434/v1/chat/completions",
        "your-installed-model",
    ),
    "LiteLLM": (
        "http://127.0.0.1:4000/v1/chat/completions",
        "your-model",
    ),
}
