"""The instructions and context sent to the model. Chunks are numbered [1], [2], ... and the model
must cite those numbers, or reply with the exact "I don't know" sentence for the answer language."""

from .store import Chunk

LANGUAGE_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi"}

DONT_KNOW = {
    "en": "I don't know based on the connected sources.",
    "te": "కనెక్ట్ చేసిన మూలాల ఆధారంగా నాకు ఈ సమాచారం తెలియదు.",
    "hi": "जुड़े हुए स्रोतों के आधार पर मुझे यह जानकारी नहीं पता।",
}


def rag_system(language: str) -> str:
    return (
        "You answer questions using ONLY the numbered sources given to you.\n"
        "Rules:\n"
        "1. Every sentence that states a fact must end with the number of the source it comes from, "
        "like [1] or [2][3]. Use only numbers that appear in the sources.\n"
        "2. If the sources do not contain the answer, reply with exactly this sentence and nothing else: "
        f"{DONT_KNOW[language]}\n"
        "3. Do not use outside knowledge and do not guess.\n"
        f"4. Write the answer in {LANGUAGE_NAMES[language]}, even if the sources are in another language. "
        "Keep numbers, names and amounts exactly as they appear in the sources.\n"
        "5. Be concise."
    )


def rag_prompt(question: str, chunks: list[Chunk]) -> str:
    blocks = []
    for n, c in enumerate(chunks, start=1):
        where = f"{c.file_name}, page {c.page}, chunk {c.chunk_index}" if c.page else f"{c.file_name}, chunk {c.chunk_index}"
        blocks.append(f"[{n}] ({where})\n{c.content}")
    return "Sources:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}"


def direct_system(language: str) -> str:
    return (
        "You are the assistant of BharatAgent Studio. No documents are connected to you for this question.\n"
        "Reply briefly to greetings and to questions about how to use the assistant.\n"
        "If the question asks about facts, documents, policies, prices or data, do not answer it: reply with "
        f"exactly this sentence: {DONT_KNOW[language]} Then suggest connecting a source.\n"
        f"Write in {LANGUAGE_NAMES[language]}."
    )
