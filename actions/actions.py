from pathlib import Path
from typing import Any, Dict, List, Text

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher
import yaml

from actions.scoring import SKILL_TERMS, score_skill_evidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_COMPETENCIES_PATH = PROJECT_ROOT / "data/knowledge_base/external_role_competencies.yml"


class ActionChooseFollowupSkill(Action):
    def name(self) -> Text:
        return "action_choose_followup_skill"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        hard_skills = tracker.get_slot("hard_skills") or []

        if isinstance(hard_skills, str):
            hard_skills = [hard_skills]

        current_skill = choose_supported_skill(hard_skills)

        if current_skill is None:
            dispatcher.utter_message(
                text="Я пока не вижу навыка, по которому могу задать уточняющий вопрос."
            )
            return []

        target_role = normalize_role_key(tracker.get_slot("target_role"))
        role_hint = get_role_question_hint(target_role)
        message = build_followup_message(current_skill, role_hint)

        dispatcher.utter_message(text=message)
        return [SlotSet("current_skill", current_skill)]


class ActionScoreSkillEvidence(Action):
    def name(self) -> Text:
        return "action_score_skill_evidence"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        current_skill = tracker.get_slot("current_skill")
        evidence_answer = tracker.get_slot("skill_evidence_answer")

        if not current_skill or not evidence_answer:
            dispatcher.utter_message(
                text="Мне не хватило данных, чтобы оценить подтверждение навыка."
            )
            return []

        result = score_skill_evidence(evidence_answer, current_skill)

        dispatcher.utter_message(
            text=(
                f"Оценка подтверждения навыка {current_skill}: {result['score']} баллов.\n"
                f"{result['justification']}"
            )
        )

        return []


def choose_supported_skill(hard_skills):
    for skill in hard_skills:
        normalized_skill = normalize_skill_name(skill)
        if normalized_skill in SKILL_TERMS:
            return normalized_skill

    return None


def normalize_skill_name(skill):
    for supported_skill in SKILL_TERMS:
        if str(skill).lower() == supported_skill.lower():
            return supported_skill

    return str(skill)


def normalize_role_key(role_name):
    if not role_name:
        return None

    normalized = str(role_name).strip().lower()
    normalized = normalized.replace("-", " ").replace("_", " ")

    role_aliases = {
        "project manager": "project_manager",
        "pm": "project_manager",
        "data analyst": "data_analyst",
        "аналитик данных": "data_analyst",
        "data engineer": "data_engineer",
        "дата инженер": "data_engineer",
        "инженер данных": "data_engineer",
        "data scientist": "data_scientist",
        "дата сайентист": "data_scientist",
        "mlops engineer": "mlops_engineer",
        "mlops": "mlops_engineer",
    }

    return role_aliases.get(normalized, normalized.replace(" ", "_"))


def get_role_question_hint(role_key):
    if not role_key:
        return None

    knowledge_base = load_external_competencies()
    role_config = knowledge_base.get("roles", {}).get(role_key)
    if not role_config:
        return None

    hints = role_config.get("interview_question_hints") or []
    if not hints:
        return None

    return hints[0]


def load_external_competencies():
    with EXTERNAL_COMPETENCIES_PATH.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def build_followup_message(current_skill, role_hint=None):
    base_question = (
        f"Вы указали навык {current_skill}. "
        "Расскажите, как вы использовали его в реальном проекте."
    )

    if not role_hint:
        return base_question

    return f"{base_question}\n\nДополнительно по роли: {role_hint}"
