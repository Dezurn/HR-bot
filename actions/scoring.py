from pathlib import Path

import yaml
import re

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIREMENTS_PATH = PROJECT_ROOT / "data/knowledge_base/roles_requirements.yml"


def calculate_result(slots, requirements_path=DEFAULT_REQUIREMENTS_PATH):
    knowledge_base = load_requirements(requirements_path)
    candidate = dict(slots)
    roles = knowledge_base["roles"]
    scoring = knowledge_base["scoring"]

    role_scores = {}
    for role_key, role_config in roles.items():
        role_scores[role_key] = score_candidate_for_role(
            candidate, role_key, role_config, scoring
        )

    best_role_key = max(role_scores, key=lambda key: role_scores[key]["score"])
    best_score = role_scores[best_role_key]["score"]

    if best_score >= scoring.get("recommended_role_threshold"):
        recommended_role_key = best_role_key
        recommended_role = roles[best_role_key]["title"]
    else:
        recommended_role_key = None
        recommended_role = "не определена"

    target_role_key = candidate.get("target_role")
    if target_role_key not in roles:
        target_role_key = None

    missing_role_key = target_role_key or best_role_key

    return {
        "target_role": candidate.get("target_role"),
        "target_role_key": target_role_key,
        "recommended_role": recommended_role,
        "recommended_role_key": recommended_role_key,
        "screening_score": best_score,
        "decision": get_decision_label(best_score),
        "missing_requirements": get_missing_requirements(
            candidate, roles[missing_role_key], scoring, best_score
        ),
        "role_scores": role_scores,
    }


def load_requirements(requirements_path=None):
    path = Path(requirements_path)
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def score_candidate_for_role(candidate, role_key, role_config, scoring):
    weights = scoring["weights"]
    requirements = role_config["requirements"]

    experience_score = score_experience(
        candidate.get("experience_years"),
        requirements.get("min_experience_years"),
        weights["experience_years"],
    )

    hard_score, matched_hard, missing_hard = score_list_match(
        candidate.get("hard_skills", []),
        requirements["hard_skills"],
        weights["hard_skills"],
    )

    tools_score, matched_tools, missing_tools = score_list_match(
        candidate.get("tools", []), requirements["tools"], weights["tools"]
    )

    project_score = score_project_experience(
        candidate.get("project_experience"),
        requirements.get("project_experience_required", False),
        requirements.get("project_experience_preferred", False),
        weights["project_experience"],
    )

    salary_score = score_salary(
        candidate.get("salary_expectation"),
        requirements.get("salary_limit"),
        weights["salary_expectation"],
    )

    education_score = score_ordered_level(
        candidate.get("education_level"),
        requirements.get("min_education_level"),
        scoring.get("education_levels_order"),
        weights["education_level"],
    )

    english_score = score_ordered_level(
        candidate.get("english_level"),
        requirements.get("min_english_level"),
        scoring.get("english_levels_order"),
        weights["english_level"],
    )

    breakdown = {
        "experience_years": round(experience_score, 2),
        "hard_skills": round(hard_score, 2),
        "tools": round(tools_score, 2),
        "project_experience": round(project_score, 2),
        "salary_expectation": round(salary_score, 2),
        "education_level": round(education_score, 2),
        "english_level": round(english_score, 2),
    }

    total_score = sum(breakdown.values())
    total_score = apply_score_caps(total_score, candidate, role_config, scoring)

    return {
        "role_title": role_config.get("title", role_key),
        "score": round(total_score),
        "breakdown": breakdown,
        "matched": {
            "hard_skills": matched_hard,
            "tools": matched_tools,
        },
        "missing": {
            "hard_skills": missing_hard,
            "tools": missing_tools,
        },
    }


def score_experience(candidate_experience, required_experience, max_score):
    if required_experience is None or required_experience == 0:
        return max_score
    if candidate_experience is None:
        return 0
    if candidate_experience >= required_experience:
        return max_score
    return max_score * max(candidate_experience, 0) / required_experience


def score_list_match(candidate_values, required_values, max_score):
    if candidate_values is None:
        candidate_values = []

    required_values = list(required_values)
    candidate_values = list(candidate_values)

    matched = []
    missing = []

    for item in required_values:
        if item in candidate_values:
            matched.append(item)
        else:
            missing.append(item)

    score = max_score * len(matched) / len(required_values)
    return score, matched, missing


def score_project_experience(
    candidate_project_experience, required, preferred, max_score
):
    if candidate_project_experience is True:
        return max_score
    if required:
        return 0.0
    if preferred:
        return max_score / 2
    return max_score


def score_salary(candidate_salary, salary_limit, max_score):
    if (
        salary_limit is None
        or salary_limit <= 0
        or candidate_salary is None
        or candidate_salary <= salary_limit
    ):
        return max_score

    max_salary = salary_limit * 1.5
    if candidate_salary >= max_salary:
        return 0.0

    salary_range = max_salary - salary_limit
    over_limit = candidate_salary - salary_limit
    return max_score * (1 - over_limit / salary_range)


def score_ordered_level(candidate_level, required_level, levels_order, max_score):
    if not required_level:
        return max_score
    if not candidate_level:
        return 0.0

    levels = list(levels_order)

    if candidate_level not in levels:
        return 0.0
    if required_level not in levels:
        return max_score

    candidate_index = levels.index(candidate_level)
    required_index = levels.index(required_level)

    if candidate_index >= required_index:
        return max_score
    if required_index - candidate_index == 1:
        return max_score / 2
    return 0.0


def is_level_lower(candidate_level, required_level, levels_order):
    if not required_level:
        return False
    if not candidate_level:
        return True

    levels = list(levels_order)

    if candidate_level not in levels:
        return True
    if required_level not in levels:
        return False

    return levels.index(candidate_level) < levels.index(required_level)


def apply_score_caps(score, candidate, role_config, scoring):
    requirements = role_config["requirements"]
    caps = scoring.get("score_caps")

    candidate_experience = candidate.get("experience_years")
    required_experience = requirements.get("min_experience_years")
    if required_experience is not None:
        if candidate_experience is None or candidate_experience < required_experience:
            score = min(score, caps.get("below_min_experience"))

    hard_ratio = get_match_ratio(
        candidate.get("hard_skills", []), requirements.get("hard_skills")
    )
    critical_ratio = caps.get("no_profile_hard_skills_max_ratio")

    if hard_ratio <= critical_ratio:
        score = min(score, caps.get("no_profile_hard_skills"))
    elif hard_ratio < 0.5:
        score = min(score, caps.get("insufficient_hard_skills"))

    if requirements.get("project_experience_required", False):
        if candidate.get("project_experience") is not True:
            score = min(score, caps.get("missing_required_project_experience"))

    if is_level_lower(
        candidate.get("education_level"),
        requirements.get("min_education_level"),
        scoring.get("education_levels_order"),
    ):
        score = min(score, caps.get("insufficient_education"))

    candidate_salary = candidate.get("salary_expectation")
    salary_limit = requirements.get("salary_limit")
    if candidate_salary is not None and salary_limit:
        max_salary = salary_limit * 1.5
        if candidate_salary >= max_salary:
            score = min(score, caps.get("salary_over_limit_critical"))
        elif candidate_salary > salary_limit:
            score = min(score, caps.get("salary_over_limit"))

    return score


def get_missing_requirements(candidate, role_config, scoring, best_score):
    messages = scoring["missing_requirements_messages"]
    requirements = role_config["requirements"]
    missing = []

    candidate_experience = candidate.get("experience_years")
    required_experience = requirements.get("min_experience_years")
    if required_experience is not None:
        if candidate_experience is None or candidate_experience < required_experience:
            missing.append(messages["insufficient_experience"])

    hard_ratio = get_match_ratio(
        candidate.get("hard_skills", []), requirements["hard_skills"]
    )
    if hard_ratio < 0.5:
        missing.append(messages["insufficient_hard_skills"])

    tools_ratio = get_match_ratio(candidate.get("tools", []), requirements["tools"])
    if tools_ratio < 0.5:
        missing.append(messages["insufficient_tools"])

    if requirements.get("project_experience_required", False):
        if candidate.get("project_experience") is not True:
            missing.append(messages["no_project_experience"])

    if is_level_lower(
        candidate.get("education_level"),
        requirements.get("min_education_level"),
        scoring.get("education_levels_order"),
    ):
        missing.append(messages["insufficient_education"])

    salary = candidate.get("salary_expectation")
    salary_limit = requirements.get("salary_limit")
    if salary is not None and salary_limit is not None and salary > salary_limit:
        missing.append(messages["salary_above_limit"])

    english_score = score_ordered_level(
        candidate.get("english_level", None),
        requirements.get("min_english_level"),
        scoring.get("english_levels_order"),
        1,
    )
    if english_score < 1:
        missing.append(messages["insufficient_english"])

    if best_score < scoring.get("recommended_role_threshold"):
        missing.append(messages["no_matching_role"])

    return missing


def get_decision_label(score):
    if score >= 75:
        return "подходит"
    if score >= 50:
        return "резерв"
    return "не подходит"


def get_match_ratio(candidate_values, required_values):
    required_values = list(required_values)
    if not required_values:
        return 1.0

    matched_count = 0
    for item in required_values:
        if item in candidate_values:
            matched_count += 1

    return matched_count / len(required_values)


SKILL_TERMS = {
    "Docker": {
        "technical_terms": [
            "dockerfile",
            "docker-compose",
            "container",
            "image",
            "logs",
            "env",
            "postgresql",
        ],
        "project_context": [
            "fastapi",
            "backend",
            "сервис",
            "микросервис",
        ],
        "problem_solution_result": [
            "проблема",
            "проблемы",
            "ошибка",
            "решил",
            "исправил",
            "настроил",
            "дебажил",
        ],
        "action_verbs": [
            "писал",
            "использовал",
            "запускал",
            "смотрел",
            "исправлял",
            "настраивал",
        ],
    }
}


SKILL_SIGNAL_RULES = {
    "technical_terms": {
        "points_per_match": 5,
        "max_score": 30,
        "missing_message": "технических деталей",
    },
    "project_context": {
        "points_per_match": 5,
        "max_score": 15,
        "missing_message": "контекста проекта",
    },
    "problem_solution_result": {
        "points_per_match": 5,
        "max_score": 20,
        "missing_message": "описания проблемы, решения или результата",
    },
    "action_verbs": {
        "points_per_match": 5,
        "max_score": 15,
        "missing_message": "ясного описания действий кандидата",
    },
}


def find_matched_terms(text, terms):
    pattern = "|".join(re.escape(term.lower()) for term in terms)
    matches = re.findall(pattern, text.lower())
    return list(set(matches))


def count_matches(text, skill_name):
    result_skills = {}
    skill_groups = SKILL_TERMS[skill_name]
    result_skills[skill_name] = {}
    for group_name, terms in skill_groups.items():
        matched_terms = find_matched_terms(text, terms)
        result_skills[skill_name][group_name] = matched_terms
    return result_skills


def score_skill_evidence(answer, skill_name):
    if skill_name not in SKILL_TERMS:
        return {
            "skill": skill_name,
            "score": 0,
            "justification": "Для этого навыка пока нет правил оценки.",
        }

    matched = count_matches(answer, skill_name)
    skill_matches = matched[skill_name]
    concreteness_score = score_concreteness(answer)
    score = calculate_skill_evidence_score(skill_matches, concreteness_score)
    justification = build_skill_evidence_justification(
        score, skill_matches, concreteness_score
    )

    return {
        "skill": skill_name,
        "score": score,
        "justification": justification,
    }


def calculate_skill_evidence_score(skill_matches, concreteness_score):
    score = concreteness_score

    for signal_name, rule in SKILL_SIGNAL_RULES.items():
        matched_terms = skill_matches.get(signal_name, [])
        signal_score = len(matched_terms) * rule["points_per_match"]
        score += min(signal_score, rule["max_score"])

    return score


def score_concreteness(answer):
    words_count = len(answer.split())

    if words_count < 5:
        return 0
    if words_count < 10:
        return 5
    if words_count < 20:
        return 10
    return 20


def build_skill_evidence_justification(score, skill_matches, concreteness_score):
    missing_parts = []

    if concreteness_score < 10:
        missing_parts.append("конкретности ответа")

    for signal_name, rule in SKILL_SIGNAL_RULES.items():
        if not skill_matches.get(signal_name):
            missing_parts.append(rule["missing_message"])

    if not missing_parts:
        return "Ответ хорошо подтверждает навык: есть конкретика, детали, контекст, действия и problem/solution."

    if score >= 40:
        prefix = "Ответ частично подтверждает навык"
    else:
        prefix = "Ответ слабо подтверждает навык"

    missing_text = ", ".join(missing_parts[:3])
    return f"{prefix}: не хватает {missing_text}."


if __name__ == "__main__":
    # пример можно попробовать разные значения в example_slots для проверки разных кейсов
    example_slots = {
        "target_role": "data_scientist",
        "experience_years": 3,
        "hard_skills": [
            "Python",
            "машинное обучение",
            "статистика",
            "анализ данных",
            "построение моделей",
        ],
        "tools": ["Python", "pandas", "numpy", "sklearn", "matplotlib", "Jupyter"],
        "project_experience": True,
        "salary_expectation": 200000,
        "education_level": "высшее",
        "english_level": "C2",
    }

    result = calculate_result(example_slots)
    print(yaml.safe_dump(result, allow_unicode=True, sort_keys=False))

    bad_answer = "Да, использовал Docker в проектах."
    good_answer = "Я писал Dockerfile для FastAPI-сервиса, использовал docker-compose с PostgreSQL, смотрел docker logs, были проблемы с env variables."
    print(score_skill_evidence(bad_answer, "Docker"))
    print(score_skill_evidence(good_answer, "Docker"))
