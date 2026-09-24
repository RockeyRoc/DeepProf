"""Gateway-owned event names for product teaching flows."""

TEACHING_DECISION_EVENT = "teaching.decision"
TEACHING_TURN_COMPLETED_EVENT = "teaching.turn.completed"
CONVERSATION_TURN_COMPLETED_EVENT = "conversation.turn.completed"
QUIZ_EVENT_TYPES = ("quiz.issued", "quiz.scored", "quiz.review_pending")
LEARNER_EVENT_TYPES = ("pedagogy.attempt", "pedagogy.attempt_skipped")
DOCUMENT_EVENT_TYPES = ("document.converted",)
GATEWAY_EVENT_TYPES = (TEACHING_DECISION_EVENT, TEACHING_TURN_COMPLETED_EVENT, *QUIZ_EVENT_TYPES,
                       CONVERSATION_TURN_COMPLETED_EVENT, *LEARNER_EVENT_TYPES, *DOCUMENT_EVENT_TYPES)

__all__ = ["DOCUMENT_EVENT_TYPES", "GATEWAY_EVENT_TYPES", "LEARNER_EVENT_TYPES", "QUIZ_EVENT_TYPES",
           "CONVERSATION_TURN_COMPLETED_EVENT", "TEACHING_DECISION_EVENT", "TEACHING_TURN_COMPLETED_EVENT"]
