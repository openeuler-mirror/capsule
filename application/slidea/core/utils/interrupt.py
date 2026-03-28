from enum import Enum


class InterruptType(Enum):
    SELECT = "select"
    QUESTION = "question"
    ACTION = "action"
    EDIT_TEXT = "edit_text"
