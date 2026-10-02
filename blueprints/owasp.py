"""OWASP Top 10 for LLM Applications. Verified against https://genai.owasp.org/llm-top-10/ on 2026-10-01."""
OWASP_VERSION = "2025"
OWASP_SOURCE = "https://genai.owasp.org/llm-top-10/"
OWASP_LLM = {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM04": "Data and Model Poisoning",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
    "LLM07": "System Prompt Leakage",
    "LLM08": "Vector and Embedding Weaknesses",
    "LLM09": "Misinformation",
    "LLM10": "Unbounded Consumption",
}
