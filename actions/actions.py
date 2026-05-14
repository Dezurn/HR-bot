from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Text

from rasa_sdk import Action, FormValidationAction, Tracker
from rasa_sdk.events import FollowupAction, SlotSet
from rasa_sdk.executor import CollectingDispatcher
import yaml

from actions.scoring import SKILL_TERMS, calculate_result, score_skill_evidence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_COMPETENCIES_PATH = (
    PROJECT_ROOT / "data/knowledge_base/external_role_competencies.yml"
)
INTERVIEW_RESULTS_DIR = PROJECT_ROOT / "interview_results"


class ActionChooseFollowupSkill(Action):
    def name(self) -> Text:
        return "action_choose_followup_skill"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        if tracker.get_slot("candidate_is_rejected"):
            return []

        hard_skills = split_slot_list(tracker.get_slot("hard_skills"))

        current_skill = choose_supported_skill(hard_skills)

        if current_skill is None:
            return []

        return [
            SlotSet("current_skill", current_skill),
            SlotSet("skill_evidence_answer", None),
            FollowupAction("skill_evidence_form"),
        ]


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
        append_skill_evidence_to_interview_result(
            tracker.get_slot("interview_result_file"), result
        )

        dispatcher.utter_message(text=result["justification"])

        return []


class ActionCalculateScreeningResult(Action):
    def name(self) -> Text:
        return "action_calculate_screening_result"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict[Text, Any]]:
        slots = {
            "full_name": tracker.get_slot("full_name"),
            "email": tracker.get_slot("email"),
            "phone": tracker.get_slot("phone"),
            "target_role": normalize_role_key(tracker.get_slot("target_role")),
            "experience_years": tracker.get_slot("experience_years"),
            "hard_skills": split_slot_list(tracker.get_slot("hard_skills")),
            "tools": split_slot_list(tracker.get_slot("tools")),
            "project_experience": tracker.get_slot("project_experience"),
            "salary_expectation": tracker.get_slot("salary_expectation"),
            "education_level": tracker.get_slot("education_level"),
            "english_level": tracker.get_slot("english_level"),
            "availability": tracker.get_slot("availability"),
            "work_format": tracker.get_slot("work_format"),
        }

        result = calculate_result(slots)
        saved_path = save_interview_result(slots, result, tracker.sender_id)
        if result.get("is_blacklisted"):
            dispatcher.utter_message(text=build_blacklist_rejection_message(result))
            return [
                SlotSet("candidate_is_rejected", True),
                SlotSet("candidate_is_blacklisted", True),
                SlotSet("interview_result_file", str(saved_path)),
            ]

        missing_requirements, general_messages = split_screening_messages(
            result.get("missing_requirements") or []
        )

        if missing_requirements:
            missing_text = "\n".join(f"- {item}" for item in missing_requirements)
        else:
            missing_text = "нет"

        target_role = get_display_role(result)
        recommendation_text = ""
        if result.get("recommended_role_key") and result.get(
            "recommended_role_key"
        ) != result.get("target_role_key"):
            recommendation_text = (
                f"\nПо ответам вы также можете лучше подойти на роль "
                f"{result['recommended_role']}."
            )

        general_text = ""
        if general_messages:
            general_text = "\n" + "\n".join(general_messages)

        dispatcher.utter_message(
            text=(
                f"Решение по роли {target_role}: {result['decision']}.\n"
                f"Чего не хватает:\n{missing_text}"
                f"{general_text}"
                f"{recommendation_text}"
            )
        )

        return [
            SlotSet("candidate_is_rejected", result.get("decision") == "не подходит"),
            SlotSet("candidate_is_blacklisted", False),
            SlotSet("interview_result_file", str(saved_path)),
        ]


class ValidateCandidateForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_candidate_form"

    def validate_email(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        email = str(slot_value or "").strip().lower()
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return {"email": email}

        dispatcher.utter_message(
            text="Похоже, email указан некорректно. Введите адрес в формате name@example.com."
        )
        return {"email": None}

    def validate_phone(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        phone = str(slot_value or "").strip()
        normalized_phone = re.sub(r"[^\d+]", "", phone)
        digits_count = len(re.sub(r"\D", "", normalized_phone))

        if 10 <= digits_count <= 15 and normalized_phone.count("+") <= 1:
            return {"phone": normalized_phone}

        dispatcher.utter_message(
            text="Похоже, номер телефона указан некорректно. Введите номер с кодом страны, например +79990000001."
        )
        return {"phone": None}

    def validate_experience_years(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        experience = parse_positive_float(slot_value)
        if experience is not None and experience <= 60:
            return {"experience_years": experience}

        dispatcher.utter_message(
            text="Укажите опыт числом в годах, например 0.5, 1 или 3."
        )
        return {"experience_years": None}

    def validate_salary_expectation(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> Dict[Text, Any]:
        salary = parse_positive_float(slot_value)
        if salary is not None and salary >= 0:
            return {"salary_expectation": salary}

        dispatcher.utter_message(
            text="Укажите зарплатные ожидания числом в рублях, например 150000."
        )
        return {"salary_expectation": None}


def choose_supported_skill(hard_skills):
    for skill in hard_skills:
        normalized_skill = normalize_skill_name(skill)
        if normalized_skill in SKILL_TERMS:
            return normalized_skill

    return None


def split_slot_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value

    result = []
    for item in str(value).replace(";", ",").split(","):
        item = item.strip()
        if item:
            result.append(item)

    return result


def parse_positive_float(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", ".")
        match = re.search(r"\d+(?:\.\d+)?", text.replace(" ", ""))
        if not match:
            return None
        number = float(match.group(0))

    if number < 0:
        return None
    return number


def save_interview_result(slots, result, sender_id=None):
    INTERVIEW_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc)
    candidate_name = slots.get("full_name") or "candidate"
    file_stem = build_interview_result_file_stem(candidate_name, created_at)
    path = INTERVIEW_RESULTS_DIR / f"{file_stem}.json"

    payload = {
        "created_at": created_at.isoformat(),
        "sender_id": sender_id,
        "candidate": slots,
        "screening_result": {
            "target_role": result.get("target_role"),
            "target_role_key": result.get("target_role_key"),
            "recommended_role": result.get("recommended_role"),
            "recommended_role_key": result.get("recommended_role_key"),
            "screening_score": result.get("screening_score"),
            "best_role_score": result.get("best_role_score"),
            "decision": result.get("decision"),
            "is_blacklisted": result.get("is_blacklisted"),
            "has_blacklist_match": result.get("has_blacklist_match"),
            "blacklist_match": result.get("blacklist_match"),
            "missing_requirements": result.get("missing_requirements") or [],
            "role_scores": result.get("role_scores") or {},
        },
    }

    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    return path


def append_skill_evidence_to_interview_result(path_value, skill_evidence_result):
    if not path_value:
        return None

    path = Path(path_value)
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    payload["skill_evidence_result"] = skill_evidence_result

    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)

    return path


def split_screening_messages(messages):
    general_messages = []
    missing_requirements = []

    for message in messages:
        if message == "Нет подходящей роли среди доступных вакансий":
            general_messages.append(message)
        else:
            missing_requirements.append(message)

    return missing_requirements, general_messages


def build_blacklist_rejection_message(result):
    blacklist_match = result.get("blacklist_match") or {}
    reason = blacklist_match.get("reason")

    if reason:
        return f"Кандидат отклонен.\nПричина отклонения: {reason}."

    return (
        "Кандидат отклонен.\n"
        "Причина отклонения: кандидат не прошёл проверку "
        "по внутренним правилам компании."
    )


def build_interview_result_file_stem(candidate_name, created_at):
    timestamp = created_at.strftime("%Y%m%d_%H%M%S_%f")
    normalized_name = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", "_", str(candidate_name))
    normalized_name = normalized_name.strip("_").lower()

    if not normalized_name:
        normalized_name = "candidate"

    return f"{timestamp}_{normalized_name}"


def normalize_skill_name(skill):
    for supported_skill in SKILL_TERMS:
        if str(skill).lower() == supported_skill.lower():
            return supported_skill

    return str(skill)


def normalize_role_key(role_name):
    if not role_name:
        return None

    normalized = str(role_name).strip().lower().replace("ё", "е")
    normalized = normalized.replace("-", " ").replace("_", " ")

    role_aliases = {
        "project manager": "project_manager",
        "проджект менеджер": "project_manager",
        "проект менеджер": "project_manager",
        "проектный менеджер": "project_manager",
        "менеджер проектов": "project_manager",
        "pm": "project_manager",
        "data analyst": "data_analyst",
        "аналитик данных": "data_analyst",
        "data engineer": "data_engineer",
        "дата инженер": "data_engineer",
        "инженер данных": "data_engineer",
        "data scientist": "data_scientist",
        "дата сайентист": "data_scientist",
        "дата саентист": "data_scientist",
        "mlops engineer": "mlops_engineer",
        "ml ops engineer": "mlops_engineer",
        "инженер mlops": "mlops_engineer",
        "mlops": "mlops_engineer",
    }

    return role_aliases.get(normalized, normalized.replace(" ", "_"))


def get_display_role(result):
    target_role_key = result.get("target_role_key")
    role_scores = result.get("role_scores") or {}

    if target_role_key in role_scores:
        return role_scores[target_role_key].get("role_title", target_role_key)

    return result.get("target_role")


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

    return f"{base_question}\nДополнительно по роли: {role_hint}"
