"""
evaluate.py — Évalue le classifieur sur tests/test_tickets.json
et affiche la précision par champ (category, priority).

Usage :
    python evaluate.py
"""

import json
import sys
import os
from pathlib import Path
from openai import OpenAI

# Ajouter le dossier backend au path pour importer config
sys.path.insert(0, str(Path(__file__).parent / "backend"))
from config import OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL, CATEGORIES, CONFIDENCE_THRESHOLD

# Chemin vers le fichier de test
TEST_FILE = Path(__file__).parent / "tests" / "test_tickets.json"

client = OpenAI(
    api_key=OLLAMA_API_KEY,
    base_url=f"{OLLAMA_BASE_URL}/v1"
)

SYSTEM_PROMPT = f"""You are a support ticket classifier.

Categories allowed: {", ".join(CATEGORIES)}.

Priority rules (follow strictly):
- urgent: service is completely broken/inaccessible, security issue, or explicit words like "immediately", "urgent", "ASAP", "right now"
- high: significant frustration, money at stake (refund, double charge), time-sensitive request, recurring bug that blocks usage, or account deletion/closure request
- normal: standard request, no explicit urgency signal, but action is needed
- low: general question, invoice copy request, no action needed urgently, informational

Sentiment allowed: frustrated, neutral, positive, angry.

Respond ONLY with valid JSON, no markdown, no explanation, in this exact shape:
{{
  "category": "...",
  "priority": "...",
  "sentiment": "...",
  "confidence": 0.0,
  "reason": "..."
}}
"""


def classify(subject: str, message: str) -> dict:
    """Appelle le modèle et retourne le résultat parsé."""
    user_content = f"Subject: {subject}\nMessage: {message}"
    response = client.chat.completions.create(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content}
        ],
        temperature=0.2
    )
    raw = response.choices[0].message.content.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)


def run_evaluation():
    with open(TEST_FILE, encoding="utf-8") as f:
        tickets = json.load(f)

    total = len(tickets)
    correct_category = 0
    correct_priority = 0
    sent_to_review = 0

    errors = []

    print(f"\n{'='*60}")
    print(f"  Évaluation du classifieur — {total} tickets")
    print(f"  Modèle : {OLLAMA_MODEL}")
    print(f"  Seuil de confiance (confidence gate) : {CONFIDENCE_THRESHOLD}")
    print(f"{'='*60}\n")

    for i, ticket in enumerate(tickets, 1):
        subject = ticket["subject"]
        message = ticket["message"]
        expected_cat = ticket["expected_category"]
        expected_prio = ticket["expected_priority"]

        try:
            result = classify(subject, message)
        except Exception as e:
            print(f"[{i:02d}] ERREUR sur '{subject}': {e}")
            errors.append({"ticket": subject, "error": str(e)})
            continue

        predicted_cat = result.get("category", "")
        predicted_prio = result.get("priority", "")
        confidence = result.get("confidence", 0.0)
        needs_review = confidence < CONFIDENCE_THRESHOLD

        cat_ok = predicted_cat == expected_cat
        prio_ok = predicted_prio == expected_prio

        if cat_ok:
            correct_category += 1
        if prio_ok:
            correct_priority += 1
        if needs_review:
            sent_to_review += 1

        status_cat = "✅" if cat_ok else "❌"
        status_prio = "✅" if prio_ok else "❌"
        review_flag = " ⚠️  needs_review" if needs_review else ""

        print(
            f"[{i:02d}] {subject[:45]:<45} | "
            f"cat {status_cat} ({predicted_cat:<14} / {expected_cat:<14}) | "
            f"prio {status_prio} ({predicted_prio:<7} / {expected_prio:<7}) | "
            f"conf={confidence:.2f}{review_flag}"
        )

    # Résumé
    evaluated = total - len(errors)
    acc_cat = correct_category / evaluated * 100 if evaluated else 0
    acc_prio = correct_priority / evaluated * 100 if evaluated else 0
    review_pct = sent_to_review / evaluated * 100 if evaluated else 0

    print(f"\n{'='*60}")
    print(f"  RÉSULTATS SUR {evaluated}/{total} tickets évalués")
    print(f"{'='*60}")
    print(f"  Précision catégorie  : {correct_category}/{evaluated} = {acc_cat:.1f}%")
    print(f"  Précision priorité   : {correct_priority}/{evaluated} = {acc_prio:.1f}%")
    print(f"  Envoyés en révision  : {sent_to_review}/{evaluated} = {review_pct:.1f}%")
    if errors:
        print(f"  Erreurs              : {len(errors)}")
    print(f"{'='*60}\n")

    # Sauvegarder les résultats dans un fichier JSON
    output = {
        "model": OLLAMA_MODEL,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "total_tickets": total,
        "evaluated": evaluated,
        "accuracy_category": round(acc_cat, 1),
        "accuracy_priority": round(acc_prio, 1),
        "sent_to_review": sent_to_review,
        "errors": errors
    }
    output_path = Path(__file__).parent / "tests" / "eval_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"  Résultats sauvegardés dans : {output_path}\n")


if __name__ == "__main__":
    run_evaluation()
