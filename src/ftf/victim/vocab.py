"""Fixed vocabularies of the perception fingerprint.

Kept apart from `perceive.py` so the pipeline layers can use them without importing the
model gateway (`ftf ingest` and `ftf run` must make no model imports; see
`tests/test_checkpoint.py`).
"""

LEVERS = (
    "fear_of_missing_out",
    "urgency",
    "scarcity",
    "greed_easy_money",
    "authority",
    "social_proof",
    "outrage_at_institutions",
    "secrecy_conspiracy",
    "fear_of_poverty",
    "health_fear",
    "hope_of_cure",
    "trust_reassurance",
    "personal_attention",
    "exclusivity",
    "sympathy",
)
ACTIONS = (
    "keep_reading",
    "click_through",
    "answer_quiz",
    "chat",
    "leave_contact",
    "wait_for_call",
    "register_account",
    "deposit",
    "pay",
    "install_app",
    "share_id_document",
    "watch_video",
    "other",
)
DATA = (
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "age",
    "sex",
    "town",
    "country",
    "income",
    "savings",
    "occupation",
    "family",
    "bank_card",
    "bank_account",
    "id_document",
    "investment_experience",
    "other",
)
