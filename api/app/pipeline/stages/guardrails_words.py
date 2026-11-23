"""Word lists for the guardrails stage, kept apart from the logic so they are easy to review and extend.
Matching is on whole words, lowercased. Add terms for your own users and domain here."""

# Abusive terms in English, Hindi (Devanagari and romanised) and Telugu (script and romanised).
ABUSE = {
    "fuck", "fucking", "bitch", "bastard", "asshole", "motherfucker", "slut",
    "chutiya", "madarchod", "bhenchod", "behenchod", "harami", "kamina",
    "चूतिया", "मादरचोद", "भेनचोद", "हरामी", "कमीना",
    "lanja", "dengu", "nakodaka",
    "లంజ", "దెంగు",
}

# Requests outside an office, clinic or college assistant: entertainment, gambling, fortune-telling,
# and attempts to make the assistant ignore its instructions.
OFF_TOPIC = {
    "lottery", "betting", "casino", "jackpot", "horoscope", "astrology", "kundli", "matka",
    "satta", "dating",
    "लॉटरी", "सट्टा", "कुंडली", "राशिफल",
    "లాటరీ", "జాతకం", "బెట్టింగ్",
}

OFF_TOPIC_PHRASES = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "ignore your instructions",
    "write a poem",
    "tell me a joke",
    "song lyrics",
]
