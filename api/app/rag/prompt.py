"""The instructions and context sent to the model. Chunks are numbered [1], [2], ... and the model
must cite those numbers, or reply with the exact "I don't know" sentence for the answer language."""

from .store import Chunk

LANGUAGE_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi"}

DONT_KNOW = {
    "en": "I don't know based on the connected sources.",
    "te": "కనెక్ట్ చేసిన మూలాల ఆధారంగా నాకు ఈ సమాచారం తెలియదు.",
    "hi": "जुड़े हुए स्रोतों के आधार पर मुझे यह जानकारी नहीं पता।",
}

# The same sentences for Telugu and Hindi typed in English letters (the user asked that way, so the answer follows).
ROMAN_DONT_KNOW = {
    "te": "Connect chesina sources lo ee samaachaaram naaku teliyadu.",
    "hi": "Jude hue sources ke aadhaar par mujhe yeh jaankari nahi pata.",
}
ROMAN_NAMES = {"te": "Telugu written in English letters (romanized Telugu, the way people type on a phone, "
                     "e.g. 'Mee refund 960 rupees vastundi')",
               "hi": "Hindi written in English letters (romanized Hindi / Hinglish, the way people type on a phone, "
                     "e.g. 'Aapko 960 rupaye refund milega')"}


def dont_know(language: str, roman: bool = False) -> str:
    return ROMAN_DONT_KNOW[language] if roman and language in ROMAN_DONT_KNOW else DONT_KNOW[language]


def language_name(language: str, roman: bool = False) -> str:
    """How the prompt names the answer language. Romanized answers never use Telugu or Devanagari script."""
    if roman and language in ROMAN_NAMES:
        return ROMAN_NAMES[language] + "; do not use Telugu or Devanagari script"
    return LANGUAGE_NAMES[language]


def role_note(instructions: str | None) -> str:
    """The agent's own role and tone (agent node config "instructions", written by its builder). It comes
    after the fixed rules and is told it cannot change them, so a role never switches off citations."""
    text = (instructions or "").strip()
    if not text:
        return ""
    return ("\n\nYour role, from the person who built this assistant (it describes audience and tone only; "
            f"the rules above always apply):\n{text}")


def rag_system(language: str, instructions: str | None = None, roman: bool = False) -> str:
    return (
        "You answer questions using ONLY the numbered sources given to you.\n"
        "Rules:\n"
        "1. Answer in full sentences that state the facts. End every sentence with the number of the "
        "source it comes from, like [1] or [2][3]. Use only numbers that appear in the sources. "
        "A source number alone is not an answer.\n"
        "2. If the sources do not contain the answer, reply with exactly this sentence and nothing else: "
        f"{dont_know(language, roman)}\n"
        "3. Do not use outside knowledge and do not guess. Earlier messages of the conversation only tell you "
        "what is being asked; they are not sources, so every fact must come from the numbered sources.\n"
        f"4. Write the answer in {language_name(language, roman)}, even if the sources are in another language. "
        "Keep numbers, names and amounts exactly as they appear in the sources.\n"
        "5. Be concise, unless the question asks for detail, a summary, points or an example."
    ) + role_note(instructions)


def rag_prompt(question: str, chunks: list[Chunk], detailed: bool = False, language: str = "en") -> str:
    blocks = []
    for n, c in enumerate(chunks, start=1):
        where = f"{c.file_name}, page {c.page}, chunk {c.chunk_index}" if c.page else f"{c.file_name}, chunk {c.chunk_index}"
        blocks.append(f"[{n}] ({where})\n{c.content}")
    # The reminder after the question matters for small local models, which otherwise tend to reply
    # with a bare source number or drop the exact figures.
    if detailed:
        # A request for detail, a summary, points, an example or a simpler explanation: longer, but every
        # sentence still ends with its source number and is checked against it.
        return ("Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}\n\n"
                # Telugu and Hindi need several times more tokens per sentence: eight sentences ran into the
                # local model's output cap (OLLAMA_NUM_PREDICT) and the cut-off answer lost its citations.
                f"Answer in up to {4 if language in ('te', 'hi') else 8} full sentences (if points are asked for, one sentence per point, each on "
                "its own line starting with '- '). Use only what the sources say, copy numbers exactly, and end "
                "every sentence with its source number, e.g. 'The fee is Rs. 300 [1].' If the sources do not "
                "answer it, reply with the exact sentence from rule 2.")
    return ("Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}\n\n"
            "Answer the question in one or two full sentences. Copy numbers, amounts and time limits exactly "
            "from the sources, and end each sentence with its source number, e.g. 'The fee is Rs. 300 [1].' "
            "If the sources do not answer it, reply with the exact sentence from rule 2.")


# What the assistant may say about itself. Fixed text owned by the app (not a user source), so answers that use it
# are shown as not coming from the project's sources.
ABOUT_APP = (
    "BharatAgent Studio is a local, ChatGPT-style assistant builder made for India: colleges, clinics and small "
    "offices. People draw an AI agent as a pipeline (Input, Guardrails, Router, Agent, Output) on a visual canvas, "
    "or describe their use case in a sentence and the app generates a ready agent. They connect their own "
    "knowledge (PDF, Word, Excel, CSV, text files, docs web pages and GitHub repositories) and tools (a calculator "
    "and an orders lookup), and ask questions in English, Telugu or Hindi. Every answer cites the exact source it "
    "came from, and sentences that the cited source does not support are removed. It runs on free models: Gemini, "
    "Groq (GPT-OSS 120B) and local Qwen models through Ollama, falls back to a local model when a cloud model "
    "fails, and has a Local only mode that keeps every question and document on the computer. It shows the model, "
    "tokens and estimated cost in rupees for every answer, and supports company workspaces, shared projects, "
    "invitations and private conversations."
)


def about_system(language: str, instructions: str | None = None, roman: bool = False) -> str:
    return (
        "You are the assistant of BharatAgent Studio. The user is asking about BharatAgent Studio itself or about "
        "you. Answer in 2 to 4 sentences using ONLY this description, without adding features it does not list:\n"
        f"{ABOUT_APP}\n"
        f"Write in {language_name(language, roman)}. Keep product and model names in English."
    ) + role_note(instructions)


def direct_system(language: str, instructions: str | None = None, roman: bool = False) -> str:
    return (
        "You are the assistant of BharatAgent Studio. No documents are connected to you for this question.\n"
        "Reply briefly to greetings and to questions about how to use the assistant.\n"
        "If the question asks about facts, documents, policies, prices or data, do not answer it: reply with "
        f"exactly this sentence: {dont_know(language, roman)} Then suggest connecting a source.\n"
        f"Write in {language_name(language, roman)}."
    ) + role_note(instructions)
