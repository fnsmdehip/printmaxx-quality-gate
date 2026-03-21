#!/usr/bin/env python3

from __future__ import annotations
"""
PRINTMAXX Quality Gate System
=============================
Quant-level quality scoring across all PRINTMAXX outputs.
Scores 0-100 across multiple dimensions. BLOCKS anything below threshold.

Usage:
    python quality_gate.py --score-all
    python quality_gate.py --score-apps
    python quality_gate.py --score-content
    python quality_gate.py --score-emails
    python quality_gate.py --score-listings
    python quality_gate.py --score-scripts
    python quality_gate.py --gate          # exit code 1 if ANY dimension < 60
    python quality_gate.py --report        # detailed markdown report
    python quality_gate.py --api-json      # JSON output for webapp
"""

import argparse
import json
import os
import re
import sys
import ast
import csv
import glob as globmod
import textwrap
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent  # PRINTMAXX_STARTER_KITttttt

THRESHOLDS = {
    "BLOCK": 40,
    "WARN": 60,
    "PASS": 80,
    "EXCELLENT": 100,
}

BANNED_AI_SLOP = [
    "leverage", "utilize", "delve", "comprehensive", "robust", "innovative",
    "seamless", "game-changer", "game changer", "unlock", "empower",
    "cutting-edge", "cutting edge", "furthermore", "moreover", "additionally",
    "testament", "landscape", "paradigm", "streamlined", "frictionless",
    "elevate", "foster", "journey",  # journey exempted for travel context
    "revolutionary", "unprecedented", "holistic", "synergy", "synergize",
    "dive into", "unpack", "in today's", "rapidly evolving",
    "it's not just", "it is not just",
]

# Extended banned vocabulary from copy-style.md
EXTRA_BANNED = [
    "in order to", "due to the fact that", "at this point in time",
    "in terms of", "it's important to note", "it goes without saying",
    "i hope this helps", "let me know if you have questions",
    "happy to assist", "as of my last update", "great question",
    "such an insightful", "breathtaking", "nestled",
]

SPAM_TRIGGER_WORDS = [
    "act now", "apply now", "buy now", "call now", "click here",
    "click below", "deal ending", "do it today", "don't delete",
    "don't hesitate", "exclusive deal", "expire", "free access",
    "free consultation", "free gift", "free info", "free trial",
    "get it now", "great offer", "guarantee", "hurry", "immediately",
    "increase your", "incredible deal", "info you requested",
    "instant access", "limited time", "lowest price", "make money",
    "million dollars", "no catch", "no cost", "no fees",
    "no obligation", "no purchase necessary", "no strings attached",
    "not spam", "offer expires", "once in a lifetime", "open immediately",
    "order now", "please read", "promise you", "pure profit",
    "risk free", "risk-free", "satisfaction guaranteed", "save big",
    "special promotion", "this isn't junk", "this isn't spam",
    "time limited", "urgent", "winner", "you've been selected",
    "100% free", "100% satisfied", "dear friend", "congratulations",
    "double your", "earn extra cash", "extra income",
]

IOS_REJECTION_REASONS = [
    "minimum functionality",
    "web clip / wrapper without native features",
    "missing privacy policy",
    "no app review information",
    "broken links",
    "placeholder content",
    "lorem ipsum",
    "test data visible",
    "missing onboarding",
    "no value beyond website",
    "hidden features behind paywall without free functionality",
    "crash on launch",
    "missing required device capabilities",
    "no error handling for network failures",
]

# Directories to scan
APP_DIRS = [
    ROOT / "ralph" / "loops" / "app_factory" / "output",
    ROOT / "MONEY_METHODS" / "APP_FACTORY" / "builds",
    ROOT / "MONEY_METHODS" / "APP_FACTORY",
    ROOT / "builds",
    ROOT / "app factory",
]

CONTENT_DIRS = [
    ROOT / "CONTENT" / "social",
    ROOT / "CONTENT",
    ROOT / "AUTOMATIONS" / "content_posting",
    ROOT / "04_CONTENT",
]

EMAIL_DIRS = [
    ROOT / "AUTOMATIONS" / "outreach",
    ROOT / "EMAIL",
    ROOT / "AUTOMATIONS" / "email_templates",
]

LISTING_DIRS = [
    ROOT / "PRODUCTS",
    ROOT / "08_PRODUCTS",
    ROOT / "DIGITAL_PRODUCTS",
]

SCRIPT_DIRS = [
    ROOT / "AUTOMATIONS",
]


# ---------------------------------------------------------------------------
# UTILITIES
# ---------------------------------------------------------------------------

def rating(score: int) -> str:
    if score < THRESHOLDS["BLOCK"]:
        return "BLOCK"
    elif score < THRESHOLDS["WARN"]:
        return "WARN"
    elif score < THRESHOLDS["PASS"]:
        return "PASS"
    else:
        return "EXCELLENT"


def rating_symbol(score: int) -> str:
    r = rating(score)
    return {"BLOCK": "[BLOCK]", "WARN": "[WARN]", "PASS": "[PASS]", "EXCELLENT": "[EXCELLENT]"}[r]


EXCLUDED_DIRS = {
    "node_modules", ".next", ".expo", ".git", "__pycache__", ".venv",
    "dist", "build", ".cache", ".turbo", "coverage", ".nuxt",
    "vendor", "pods", "Pods", ".gradle", ".swiftpm",
    "DerivedData", ".build", "xcuserdata",
}

def collect_files(dirs: List[Path], extensions: Optional[List[str]] = None, recursive: bool = True) -> List[Path]:
    """Collect files from multiple directories, handling missing dirs gracefully.
    Excludes node_modules, .next, build artifacts, and other non-source directories."""
    files = []
    for d in dirs:
        if not d.exists():
            continue
        if recursive:
            for ext in (extensions or ["*"]):
                pattern = f"**/*.{ext}" if ext != "*" else "**/*"
                for f in d.glob(pattern):
                    if f.is_file() and not f.name.startswith("."):
                        # Skip files inside excluded directories
                        parts = set(f.relative_to(d).parts)
                        if parts & EXCLUDED_DIRS:
                            continue
                        files.append(f)
        else:
            for f in d.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    if extensions is None or f.suffix.lstrip(".") in extensions:
                        files.append(f)
    return list(set(files))


def safe_read(path: Path, max_bytes: int = 500_000) -> str:
    """Read file content safely, handling encoding errors."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        return content[:max_bytes]
    except Exception:
        try:
            content = path.read_bytes().decode("latin-1", errors="replace")
            return content[:max_bytes]
        except Exception:
            return ""


def count_pattern(text: str, pattern: str, case_insensitive: bool = True) -> int:
    flags = re.IGNORECASE if case_insensitive else 0
    return len(re.findall(pattern, text, flags))


def find_slop_words(text: str) -> List[Tuple[str, int]]:
    """Find banned AI slop words in text. Returns (word, count) pairs."""
    results = []
    lower = text.lower()
    for word in BANNED_AI_SLOP + EXTRA_BANNED:
        count = lower.count(word.lower())
        if count > 0:
            # Journey exemption for travel context
            if word == "journey":
                travel_ctx = count_pattern(text, r"(travel|flight|trip|destination|airport|road)\s.{0,50}journey|journey.{0,50}(travel|flight|trip|destination|airport|road)", True)
                count = max(0, count - travel_ctx)
            if count > 0:
                results.append((word, count))
    return results


def find_spam_words(text: str) -> List[Tuple[str, int]]:
    """Find spam trigger words in text."""
    results = []
    lower = text.lower()
    for word in SPAM_TRIGGER_WORDS:
        count = lower.count(word.lower())
        if count > 0:
            results.append((word, count))
    return results


def count_em_dashes(text: str) -> int:
    return text.count("\u2014") + text.count("--")


# ---------------------------------------------------------------------------
# DIMENSION 1: APP QUALITY
# ---------------------------------------------------------------------------

class AppQualityScorer:
    """Score app builds against top-10-in-category benchmarks."""

    def __init__(self):
        self.results = []

    def score_all(self) -> Dict[str, Any]:
        app_files = collect_files(APP_DIRS, ["html", "tsx", "ts", "jsx", "js", "json", "vue", "svelte", "css", "swift", "xml"])

        if not app_files:
            return {
                "dimension": "APP_QUALITY",
                "score": 0,
                "rating": "BLOCK",
                "items": [],
                "summary": "No app files found in expected directories.",
                "fix": "Build app outputs should exist in ralph/loops/app_factory/output/ or MONEY_METHODS/APP_FACTORY/builds/",
            }

        # Group files by app (parent directory)
        apps = defaultdict(list)
        for f in app_files:
            app_name = f.parent.name
            # go up if it's a nested structure
            if app_name in ("src", "components", "pages", "public", "assets", "lib", "app", "screens"):
                app_name = f.parent.parent.name
            apps[app_name].append(f)

        items = []
        total_score = 0
        for app_name, files in apps.items():
            score_data = self._score_app(app_name, files)
            items.append(score_data)
            total_score += score_data["score"]

        avg_score = total_score // max(len(items), 1)
        return {
            "dimension": "APP_QUALITY",
            "score": avg_score,
            "rating": rating(avg_score),
            "items": items,
            "summary": f"Scored {len(items)} apps. Average: {avg_score}/100.",
            "fix": self._aggregate_fixes(items),
        }

    def _score_app(self, name: str, files: List[Path]) -> Dict[str, Any]:
        all_content = ""
        file_names = [f.name.lower() for f in files]
        file_suffixes = [f.suffix.lower() for f in files]
        for f in files[:100]:  # limit to prevent memory issues
            all_content += safe_read(f, 100_000) + "\n"

        checks = {}
        fixes = []

        # 1. Onboarding flow (4+ screens)
        onboarding_patterns = [
            r"onboard", r"welcome", r"intro", r"tutorial", r"walkthrough",
            r"get.?started", r"step.?\d", r"slide", r"swiper",
        ]
        onboarding_hits = sum(count_pattern(all_content, p) for p in onboarding_patterns)
        onboarding_screens = min(onboarding_hits // 2, 10)  # rough estimate
        onboarding_score = min(100, onboarding_screens * 25)  # 4 screens = 100
        checks["onboarding"] = onboarding_score
        if onboarding_score < 100:
            fixes.append(f"Add onboarding flow: need 4+ screens, found ~{onboarding_screens}. Add welcome/tutorial/walkthrough screens.")

        # 2. Monetization integration
        monetization_patterns = [
            r"revenuecat", r"paywall", r"subscription", r"purchase",
            r"in.?app.?purchase", r"iap", r"stripe", r"affiliate",
            r"premium", r"pro.?version", r"upgrade", r"price",
            r"offering", r"entitlement",
        ]
        monetization_hits = sum(count_pattern(all_content, p) for p in monetization_patterns)
        monetization_score = min(100, monetization_hits * 10)
        checks["monetization"] = monetization_score
        if monetization_score < 60:
            fixes.append("Add monetization: RevenueCat/paywall/affiliate links must exist. No free-only apps ship.")

        # 3. Native plugin count (Capacitor)
        plugin_patterns = [
            r"@capacitor/", r"capacitor-", r"cordova-plugin",
            r"@ionic-native", r"native-plugin", r"expo-",
            r"react-native-", r"@react-native",
        ]
        plugins_found = set()
        for p in plugin_patterns:
            matches = re.findall(p, all_content, re.IGNORECASE)
            plugins_found.update(matches)
        plugin_count = len(plugins_found)
        plugin_score = min(100, plugin_count * 25)  # 4 plugins = 100
        checks["native_plugins"] = plugin_score
        if plugin_score < 100:
            fixes.append(f"Add native plugins: found {plugin_count}, need 4+. Add camera/push-notifications/haptics/share at minimum.")

        # 4. Accessibility basics
        a11y_patterns = {
            "font_size": r"font.?size[:\s]*(\d+)",
            "aria_label": r"aria-label",
            "alt_text": r'alt=["\']',
            "touch_target": r"(padding|min.?height|min.?width)[:\s]*(\d+)",
            "contrast": r"(contrast|a11y|accessibility|wcag)",
            "role": r'role=["\']',
        }
        a11y_score = 0
        a11y_found = []
        for check_name, pattern in a11y_patterns.items():
            if re.search(pattern, all_content, re.IGNORECASE):
                a11y_score += 17
                a11y_found.append(check_name)
        a11y_score = min(100, a11y_score)
        checks["accessibility"] = a11y_score
        if a11y_score < 60:
            missing_a11y = set(a11y_patterns.keys()) - set(a11y_found)
            fixes.append(f"Fix accessibility: missing {', '.join(missing_a11y)}. Add aria-labels, alt text, min touch targets 44px, font-size 16px+.")

        # 5. iOS rejection risk
        rejection_risks = []
        lower_content = all_content.lower()
        if "lorem ipsum" in lower_content:
            rejection_risks.append("placeholder content (lorem ipsum)")
        if "todo" in lower_content and lower_content.count("todo") > 5:
            rejection_risks.append(f"excessive TODOs ({lower_content.count('todo')} found)")
        if "privacy" not in lower_content and "privacypolicy" not in lower_content.replace(" ", ""):
            rejection_risks.append("missing privacy policy reference")
        if not any(p in lower_content for p in ["error", "catch", "try", "fallback", "offline"]):
            rejection_risks.append("no error handling visible")
        if not any(p in lower_content for p in ["onboard", "welcome", "tutorial", "intro"]):
            rejection_risks.append("no onboarding flow")
        if "webview" in lower_content and plugin_count < 2:
            rejection_risks.append("web wrapper with insufficient native features")

        rejection_score = max(0, 100 - len(rejection_risks) * 20)
        checks["ios_rejection_risk"] = rejection_score
        if rejection_risks:
            fixes.append(f"iOS rejection risks: {'; '.join(rejection_risks)}")

        # 6. Lighthouse score extraction (check for build outputs)
        lighthouse_score = 50  # default if no lighthouse data
        lighthouse_pattern = r"performance[:\s]*(\d+)|lighthouse[:\s]*(\d+)|score[:\s]*(\d+)/100"
        lh_match = re.search(lighthouse_pattern, all_content, re.IGNORECASE)
        if lh_match:
            for g in lh_match.groups():
                if g and g.isdigit():
                    lighthouse_score = int(g)
                    break
        checks["lighthouse"] = lighthouse_score

        # 7. AI slop in app content
        slop = find_slop_words(all_content)
        slop_count = sum(c for _, c in slop)
        slop_score = max(0, 100 - slop_count * 5)
        checks["ai_slop_free"] = slop_score
        if slop:
            top_offenders = sorted(slop, key=lambda x: -x[1])[:5]
            fixes.append(f"Remove AI slop from app copy: {', '.join(f'{w}({c}x)' for w,c in top_offenders)}")

        # Weighted average
        weights = {
            "onboarding": 0.15,
            "monetization": 0.20,
            "native_plugins": 0.15,
            "accessibility": 0.15,
            "ios_rejection_risk": 0.20,
            "lighthouse": 0.10,
            "ai_slop_free": 0.05,
        }
        final_score = int(sum(checks[k] * weights[k] for k in weights))

        return {
            "name": name,
            "score": final_score,
            "rating": rating(final_score),
            "checks": checks,
            "fixes": fixes,
            "file_count": len(files),
        }

    def _aggregate_fixes(self, items: List[Dict]) -> str:
        all_fixes = []
        for item in items:
            if item["score"] < 80:
                for fix in item["fixes"]:
                    all_fixes.append(f"[{item['name']}] {fix}")
        return "\n".join(all_fixes[:20]) if all_fixes else "All apps passing quality gate."


# ---------------------------------------------------------------------------
# DIMENSION 2: CONTENT QUALITY
# ---------------------------------------------------------------------------

class ContentQualityScorer:
    """Score content against top-performer benchmarks."""

    def __init__(self):
        self.results = []

    def score_all(self) -> Dict[str, Any]:
        content_files = collect_files(CONTENT_DIRS, ["md", "txt", "csv", "json"])

        if not content_files:
            return {
                "dimension": "CONTENT_QUALITY",
                "score": 0,
                "rating": "BLOCK",
                "items": [],
                "summary": "No content files found.",
                "fix": "Content should exist in CONTENT/social/, AUTOMATIONS/content_posting/",
            }

        items = []
        total_score = 0
        for f in content_files:
            score_data = self._score_content_file(f)
            items.append(score_data)
            total_score += score_data["score"]

        avg_score = total_score // max(len(items), 1)
        return {
            "dimension": "CONTENT_QUALITY",
            "score": avg_score,
            "rating": rating(avg_score),
            "items": items,
            "summary": f"Scored {len(items)} content files. Average: {avg_score}/100.",
            "fix": self._aggregate_fixes(items),
        }

    def _score_content_file(self, path: Path) -> Dict[str, Any]:
        content = safe_read(path)
        if not content.strip():
            return {"name": str(path.relative_to(ROOT)), "score": 0, "rating": "BLOCK",
                    "checks": {}, "fixes": ["File is empty."]}

        checks = {}
        fixes = []

        # 1. Hook strength
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        first_lines = lines[:3] if lines else [""]
        hook_text = " ".join(first_lines)

        hook_score = 50  # baseline
        # Consequence-first (starts with result/number/action)
        if re.search(r"^\d|^\$|^i (built|made|sold|closed|monitor|tested|found)", hook_text, re.IGNORECASE):
            hook_score += 20
        # Specific numbers
        if re.search(r"\d+[kKmM%\$]|\$\d+|\d+ (people|users|clients|sales|deals)", hook_text):
            hook_score += 15
        # Vague/weak openers
        if re.search(r"^(in today|have you ever|did you know|are you tired|let me tell)", hook_text, re.IGNORECASE):
            hook_score -= 30
        hook_score = max(0, min(100, hook_score))
        checks["hook_strength"] = hook_score
        if hook_score < 60:
            fixes.append("Weak hook. Lead with consequence/result/specific number, not a question or setup.")

        # 2. AI slop detection
        slop = find_slop_words(content)
        slop_count = sum(c for _, c in slop)
        word_count = len(content.split())
        slop_density = slop_count / max(word_count, 1) * 1000  # per 1000 words
        slop_score = max(0, int(100 - slop_density * 20))
        checks["ai_slop"] = slop_score
        if slop:
            top = sorted(slop, key=lambda x: -x[1])[:5]
            fixes.append(f"AI slop detected ({slop_count} instances): {', '.join(f'{w}({c}x)' for w,c in top)}")

        # 3. Copy style compliance
        style_score = 100
        em_dashes = count_em_dashes(content)
        if em_dashes > 0:
            style_score -= min(40, em_dashes * 5)
            fixes.append(f"Remove {em_dashes} em dashes. Use commas or periods instead.")

        # Promotional adjectives
        promo_adj = count_pattern(content, r"\b(breathtaking|revolutionary|unprecedented|holistic|cutting.edge|game.changing)\b")
        if promo_adj > 0:
            style_score -= min(30, promo_adj * 10)
            fixes.append(f"Remove {promo_adj} promotional adjectives.")

        # Excessive hedging
        hedging = count_pattern(content, r"\b(might possibly|perhaps maybe|could potentially|somewhat)\b")
        if hedging > 0:
            style_score -= min(20, hedging * 10)
            fixes.append(f"Reduce hedging: {hedging} double-hedge phrases found. One qualifier per sentence max.")

        # Rule of three abuse
        rule_of_three = count_pattern(content, r"\b\w+,\s+\w+,\s+and\s+\w+\b")
        if rule_of_three > 3:
            style_score -= 10

        style_score = max(0, style_score)
        checks["copy_style"] = style_score

        # 4. Engagement prediction (structure matching)
        engagement_score = 50
        # Short paragraphs (good for engagement)
        paragraphs = content.split("\n\n")
        if paragraphs:
            avg_para_len = sum(len(p.split()) for p in paragraphs) / len(paragraphs)
            if avg_para_len < 40:
                engagement_score += 15
            elif avg_para_len > 100:
                engagement_score -= 15
        # Has lists/bullets
        if re.search(r"^[\-\*\d]+[\.\)]\s", content, re.MULTILINE):
            engagement_score += 10
        # Specific tools/names mentioned
        if re.search(r"(\.io|\.com|\.app|@\w+)", content):
            engagement_score += 10
        # Lowercase casual energy
        if content[:200] == content[:200].lower() or content.count("i ") > content.count("I "):
            engagement_score += 5
        engagement_score = max(0, min(100, engagement_score))
        checks["engagement"] = engagement_score

        # 5. CTA presence and quality
        cta_patterns = [
            r"(link in bio|check it out|grab it|get it|sign up|subscribe|dm me|reply)",
            r"(go to|visit|download|try it|start here|join|buy)",
        ]
        cta_count = sum(count_pattern(content, p) for p in cta_patterns)
        cta_score = min(100, cta_count * 30) if cta_count <= 3 else max(50, 100 - (cta_count - 3) * 15)
        checks["cta_quality"] = cta_score
        if cta_count == 0:
            fixes.append("No CTA found. Add one clear call-to-action.")
        elif cta_count > 4:
            fixes.append(f"Too many CTAs ({cta_count}). Pick one primary CTA per piece.")

        # Weighted average
        weights = {"hook_strength": 0.25, "ai_slop": 0.25, "copy_style": 0.20,
                    "engagement": 0.15, "cta_quality": 0.15}
        final_score = int(sum(checks.get(k, 50) * weights[k] for k in weights))

        return {
            "name": str(path.relative_to(ROOT)),
            "score": final_score,
            "rating": rating(final_score),
            "checks": checks,
            "fixes": fixes,
        }

    def _aggregate_fixes(self, items: List[Dict]) -> str:
        # Aggregate most common issues
        issue_counts = defaultdict(int)
        for item in items:
            for fix in item.get("fixes", []):
                key = fix.split(":")[0] if ":" in fix else fix[:50]
                issue_counts[key] += 1
        top_issues = sorted(issue_counts.items(), key=lambda x: -x[1])[:10]
        return "\n".join(f"({count}x) {issue}" for issue, count in top_issues) if top_issues else "All content passing."


# ---------------------------------------------------------------------------
# DIMENSION 3: EMAIL QUALITY
# ---------------------------------------------------------------------------

class EmailQualityScorer:
    """Score emails against 3%+ reply rate benchmarks."""

    def __init__(self):
        self.results = []

    def score_all(self) -> Dict[str, Any]:
        email_files = collect_files(EMAIL_DIRS, ["md", "txt", "csv", "html", "json"])

        if not email_files:
            return {
                "dimension": "EMAIL_QUALITY",
                "score": 0,
                "rating": "BLOCK",
                "items": [],
                "summary": "No email files found.",
                "fix": "Email templates should exist in AUTOMATIONS/outreach/ or EMAIL/",
            }

        items = []
        total_score = 0
        for f in email_files:
            score_data = self._score_email_file(f)
            items.append(score_data)
            total_score += score_data["score"]

        avg_score = total_score // max(len(items), 1)
        return {
            "dimension": "EMAIL_QUALITY",
            "score": avg_score,
            "rating": rating(avg_score),
            "items": items,
            "summary": f"Scored {len(items)} email files. Average: {avg_score}/100.",
            "fix": self._aggregate_fixes(items),
        }

    def _score_email_file(self, path: Path) -> Dict[str, Any]:
        content = safe_read(path)
        if not content.strip():
            return {"name": str(path.relative_to(ROOT)), "score": 0, "rating": "BLOCK",
                    "checks": {}, "fixes": ["File is empty."]}

        checks = {}
        fixes = []

        # Split into individual emails if CSV or multi-email file
        emails = self._extract_emails(content, path)
        if not emails:
            emails = [content]

        email_scores = []
        for email_text in emails[:50]:  # cap at 50
            email_scores.append(self._score_single_email(email_text))

        # Average across emails in file
        if email_scores:
            for key in email_scores[0]:
                checks[key] = sum(e.get(key, 50) for e in email_scores) // len(email_scores)

        # Collect fixes from worst dimension
        for key, val in checks.items():
            if val < 60:
                fixes.extend(self._fix_for_dimension(key, val, content))

        weights = {
            "personalization": 0.25,
            "spam_score": 0.20,
            "length": 0.15,
            "cta_clarity": 0.20,
            "ai_slop": 0.20,
        }
        final_score = int(sum(checks.get(k, 50) * weights.get(k, 0.1) for k in checks if k in weights))

        return {
            "name": str(path.relative_to(ROOT)),
            "score": final_score,
            "rating": rating(final_score),
            "checks": checks,
            "fixes": fixes,
        }

    def _extract_emails(self, content: str, path: Path) -> List[str]:
        """Try to split file into individual emails."""
        if path.suffix == ".csv":
            try:
                rows = list(csv.reader(content.splitlines()))
                # Find body/content column
                if rows:
                    header = [h.lower().strip() for h in rows[0]]
                    body_idx = None
                    for i, h in enumerate(header):
                        if any(k in h for k in ["body", "content", "message", "email", "text", "copy"]):
                            body_idx = i
                            break
                    if body_idx is not None:
                        return [row[body_idx] for row in rows[1:] if len(row) > body_idx and row[body_idx].strip()]
            except Exception:
                pass
        # Split by common email delimiters
        if "Subject:" in content or "---" in content:
            parts = re.split(r"\n---+\n|\nSubject:", content)
            if len(parts) > 1:
                return [p.strip() for p in parts if len(p.strip()) > 20]
        return [content]

    def _score_single_email(self, email_text: str) -> Dict[str, int]:
        scores = {}
        words = email_text.split()
        word_count = len(words)

        # 1. Personalization depth
        personal_score = 30  # baseline
        # Basic {name} personalization
        if re.search(r"\{(name|first_name|company|business)\}", email_text, re.IGNORECASE):
            personal_score += 15
        # Business-specific hooks
        if re.search(r"\{(industry|niche|product|website|city|state|revenue|pain_point)\}", email_text, re.IGNORECASE):
            personal_score += 25
        # Mentions specific observation
        if re.search(r"(noticed|saw|found|your (website|store|product|profile|post))", email_text, re.IGNORECASE):
            personal_score += 20
        # Generic opener penalty
        if re.search(r"(hope this (finds|email)|reaching out|wanted to connect)", email_text, re.IGNORECASE):
            personal_score -= 20
        scores["personalization"] = max(0, min(100, personal_score))

        # 2. Spam trigger word scan
        spam = find_spam_words(email_text)
        spam_count = sum(c for _, c in spam)
        scores["spam_score"] = max(0, 100 - spam_count * 15)

        # 3. Length check (50-125 words optimal for cold email)
        if 50 <= word_count <= 125:
            scores["length"] = 100
        elif 30 <= word_count < 50 or 125 < word_count <= 175:
            scores["length"] = 70
        elif word_count < 30:
            scores["length"] = 30
        else:
            scores["length"] = max(20, 100 - (word_count - 175) // 5)

        # 4. CTA clarity (single, specific, low-friction)
        cta_matches = re.findall(r"(reply|respond|schedule|book|call|click|sign|visit|check out|try|demo|meeting)", email_text, re.IGNORECASE)
        unique_ctas = set(m.lower() for m in cta_matches)
        if len(unique_ctas) == 1:
            scores["cta_clarity"] = 100
        elif len(unique_ctas) == 2:
            scores["cta_clarity"] = 75
        elif len(unique_ctas) == 0:
            scores["cta_clarity"] = 20
        else:
            scores["cta_clarity"] = max(20, 80 - len(unique_ctas) * 15)

        # 5. AI slop
        slop = find_slop_words(email_text)
        slop_count = sum(c for _, c in slop)
        scores["ai_slop"] = max(0, 100 - slop_count * 15)

        return scores

    def _fix_for_dimension(self, dim: str, score: int, content: str) -> List[str]:
        fixes_map = {
            "personalization": "Add business-specific personalization: mention their website/product/recent post, not just {name}. Use {industry}, {pain_point}, {website} variables.",
            "spam_score": f"Remove spam triggers. Found: {', '.join(w for w,_ in find_spam_words(content)[:5])}. Rewrite without urgency/hype words.",
            "length": f"Adjust email length. Current ~{len(content.split())} words. Optimal cold email: 50-125 words. Cut fluff ruthlessly.",
            "cta_clarity": "Fix CTA: use exactly ONE clear, specific, low-friction ask. 'Reply with YES if interested' beats 'Schedule a call to discuss how we can help optimize your workflow'.",
            "ai_slop": f"Remove AI slop: {', '.join(w for w,_ in find_slop_words(content)[:5])}",
        }
        return [fixes_map.get(dim, f"Improve {dim} (score: {score}/100)")]

    def _aggregate_fixes(self, items: List[Dict]) -> str:
        all_fixes = []
        for item in items:
            if item["score"] < 60:
                for fix in item.get("fixes", []):
                    all_fixes.append(f"[{item['name']}] {fix}")
        return "\n".join(all_fixes[:15]) if all_fixes else "All emails passing."


# ---------------------------------------------------------------------------
# DIMENSION 4: LISTING QUALITY
# ---------------------------------------------------------------------------

class ListingQualityScorer:
    """Score product listings against top seller benchmarks."""

    def __init__(self):
        self.results = []

    def score_all(self) -> Dict[str, Any]:
        listing_files = collect_files(LISTING_DIRS, ["md", "txt", "csv", "json", "html"])

        if not listing_files:
            return {
                "dimension": "LISTING_QUALITY",
                "score": 0,
                "rating": "BLOCK",
                "items": [],
                "summary": "No listing files found.",
                "fix": "Product listings should exist in PRODUCTS/",
            }

        items = []
        total_score = 0
        for f in listing_files:
            score_data = self._score_listing(f)
            items.append(score_data)
            total_score += score_data["score"]

        avg_score = total_score // max(len(items), 1)
        return {
            "dimension": "LISTING_QUALITY",
            "score": avg_score,
            "rating": rating(avg_score),
            "items": items,
            "summary": f"Scored {len(items)} listing files. Average: {avg_score}/100.",
            "fix": self._aggregate_fixes(items),
        }

    def _score_listing(self, path: Path) -> Dict[str, Any]:
        content = safe_read(path)
        if not content.strip():
            return {"name": str(path.relative_to(ROOT)), "score": 0, "rating": "BLOCK",
                    "checks": {}, "fixes": ["File is empty."]}

        checks = {}
        fixes = []

        # 1. Title keyword optimization
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        title = lines[0] if lines else ""
        title_score = 50
        # Has keywords (not just generic title)
        if re.search(r"\d+|\b(template|kit|bundle|pack|guide|system|tool)\b", title, re.IGNORECASE):
            title_score += 20
        # Title length (50-80 chars optimal)
        if 30 <= len(title) <= 100:
            title_score += 15
        elif len(title) < 15:
            title_score -= 20
        # SEO-friendly (contains target keywords)
        if re.search(r"\b(digital|download|instant|printable|editable)\b", title, re.IGNORECASE):
            title_score += 15
        title_score = max(0, min(100, title_score))
        checks["title_optimization"] = title_score
        if title_score < 60:
            fixes.append(f"Optimize title: '{title[:60]}...' Add specific keywords, keep 50-80 chars. Include product type + benefit.")

        # 2. Description completeness
        desc_score = 0
        word_count = len(content.split())
        if word_count >= 100:
            desc_score += 25
        if word_count >= 200:
            desc_score += 15
        # Has bullet points
        if re.search(r"^[\-\*\u2022]", content, re.MULTILINE):
            desc_score += 15
        # Has what's included
        if re.search(r"(what.?s included|you.?ll get|includes|features|contents)", content, re.IGNORECASE):
            desc_score += 15
        # Has use case/who it's for
        if re.search(r"(perfect for|ideal for|great for|designed for|who this is for|use case)", content, re.IGNORECASE):
            desc_score += 15
        # Has specs/details
        if re.search(r"(format|size|pages|dimensions|resolution|file type|compatible)", content, re.IGNORECASE):
            desc_score += 15
        desc_score = min(100, desc_score)
        checks["description"] = desc_score
        if desc_score < 60:
            fixes.append("Flesh out description: add what's-included bullets, who-it's-for section, file format/specs, and use cases.")

        # 3. Pricing strategy
        pricing_score = 30
        # Has price mentioned
        prices = re.findall(r"\$(\d+\.?\d*)", content)
        if prices:
            pricing_score += 20
            price_vals = [float(p) for p in prices]
            # Psychological pricing (.97, .99, .95)
            if any(str(p).endswith(("97", "99", "95", "9")) for p in prices):
                pricing_score += 20
            # Tiered pricing
            if len(set(price_vals)) >= 2:
                pricing_score += 15
            # Crossed out / was price
            if re.search(r"(was|~~\$|original|regular|normally|retail)", content, re.IGNORECASE):
                pricing_score += 15
        pricing_score = min(100, pricing_score)
        checks["pricing"] = pricing_score
        if pricing_score < 60:
            fixes.append("Add pricing strategy: use psychological pricing ($X.97), show tiered options, include anchor/was price.")

        # 4. Social proof elements
        proof_score = 0
        proof_patterns = {
            "testimonial": r"(said|told me|feedback|review|\".*\")",
            "numbers": r"(\d+\+?\s*(customers|users|sold|downloads|reviews|ratings|stars))",
            "authority": r"(featured|as seen|trusted by|used by|endorsed)",
            "rating": r"(\d+\.?\d*\s*/\s*5|★|⭐|\d+\s*stars?)",
        }
        for name, pattern in proof_patterns.items():
            if re.search(pattern, content, re.IGNORECASE):
                proof_score += 25
        proof_score = min(100, proof_score)
        checks["social_proof"] = proof_score
        if proof_score < 40:
            fixes.append("Add social proof: testimonials, download counts, star ratings, 'trusted by X users' callout.")

        # 5. AI slop
        slop = find_slop_words(content)
        slop_count = sum(c for _, c in slop)
        slop_score = max(0, 100 - slop_count * 10)
        checks["ai_slop"] = slop_score
        if slop:
            fixes.append(f"AI slop in listing: {', '.join(w for w,_ in slop[:5])}. Rewrite in direct, specific language.")

        weights = {"title_optimization": 0.20, "description": 0.25, "pricing": 0.20,
                    "social_proof": 0.15, "ai_slop": 0.20}
        final_score = int(sum(checks.get(k, 50) * weights[k] for k in weights))

        return {
            "name": str(path.relative_to(ROOT)),
            "score": final_score,
            "rating": rating(final_score),
            "checks": checks,
            "fixes": fixes,
        }

    def _aggregate_fixes(self, items: List[Dict]) -> str:
        all_fixes = []
        for item in items:
            if item["score"] < 60:
                for fix in item.get("fixes", []):
                    all_fixes.append(f"[{item['name']}] {fix}")
        return "\n".join(all_fixes[:15]) if all_fixes else "All listings passing."


# ---------------------------------------------------------------------------
# DIMENSION 5: SAAS/TOOL/SCRIPT QUALITY
# ---------------------------------------------------------------------------

class ScriptQualityScorer:
    """Score Python scripts against indie SaaS best practices."""

    def __init__(self):
        self.results = []

    def score_all(self) -> Dict[str, Any]:
        script_files = collect_files(SCRIPT_DIRS, ["py"])

        if not script_files:
            return {
                "dimension": "SCRIPT_QUALITY",
                "score": 0,
                "rating": "BLOCK",
                "items": [],
                "summary": "No Python scripts found.",
                "fix": "Scripts should exist in AUTOMATIONS/",
            }

        items = []
        total_score = 0
        for f in script_files[:200]:  # cap to prevent slowness
            score_data = self._score_script(f)
            items.append(score_data)
            total_score += score_data["score"]

        avg_score = total_score // max(len(items), 1)
        return {
            "dimension": "SCRIPT_QUALITY",
            "score": avg_score,
            "rating": rating(avg_score),
            "items": items,
            "summary": f"Scored {len(items)} scripts. Average: {avg_score}/100.",
            "fix": self._aggregate_fixes(items),
        }

    def _score_script(self, path: Path) -> Dict[str, Any]:
        content = safe_read(path)
        if not content.strip():
            return {"name": str(path.relative_to(ROOT)), "score": 0, "rating": "BLOCK",
                    "checks": {}, "fixes": ["File is empty."]}

        checks = {}
        fixes = []
        lines = content.split("\n")

        # 1. Error handling coverage
        error_score = 0
        has_try = "try:" in content
        has_except = "except" in content
        catches_specific = bool(re.search(r"except\s+\w+", content))
        has_logging = bool(re.search(r"\b(logging\.|logger\.|log\.|print\(.*(error|fail|exception))", content, re.IGNORECASE))
        has_finally = "finally:" in content

        if has_try:
            error_score += 20
        if catches_specific:
            error_score += 25
        elif has_except:
            error_score += 10
        if has_logging:
            error_score += 20
        if has_finally:
            error_score += 10
        # Bare except is bad
        bare_excepts = len(re.findall(r"except\s*:", content))
        if bare_excepts > 0:
            error_score -= bare_excepts * 5
        # Check ratio of try blocks to total function count
        func_count = len(re.findall(r"^def\s+", content, re.MULTILINE))
        try_count = content.count("try:")
        if func_count > 0:
            coverage = try_count / func_count
            if coverage >= 0.5:
                error_score += 25
            elif coverage >= 0.25:
                error_score += 15
        else:
            error_score += 10  # scripts without functions

        error_score = max(0, min(100, error_score))
        checks["error_handling"] = error_score
        if error_score < 60:
            fixes.append(f"Add error handling: {func_count} functions, {try_count} try blocks. Wrap I/O, network, and file operations in try/except with specific exception types.")

        # 2. Input validation
        validation_score = 30  # baseline
        # argparse or click usage
        if re.search(r"(argparse|click\.|typer\.|sys\.argv)", content):
            validation_score += 20
        # Type hints
        type_hints = len(re.findall(r"def\s+\w+\(.*:\s*\w+", content))
        if type_hints > 0:
            validation_score += min(25, type_hints * 5)
        # Explicit validation
        validation_patterns = [
            r"if\s+not\s+\w+",
            r"isinstance\(",
            r"assert\s+",
            r"raise\s+(ValueError|TypeError|ValidationError)",
            r"\.strip\(\)",
            r"(is None|is not None)",
        ]
        for p in validation_patterns:
            if re.search(p, content):
                validation_score += 5
        validation_score = min(100, validation_score)
        checks["input_validation"] = validation_score
        if validation_score < 60:
            fixes.append("Add input validation: type hints on functions, validate arguments with isinstance/assertions, handle None/empty inputs.")

        # 3. Rate limiting
        rate_limit_score = 30  # baseline - not all scripts need it
        needs_rate_limiting = bool(re.search(r"(requests\.|httpx\.|aiohttp|urllib|fetch|api|endpoint|scrape|crawl)", content, re.IGNORECASE))
        if needs_rate_limiting:
            rate_limit_patterns = [
                r"(time\.sleep|asyncio\.sleep|sleep\()",
                r"(rate.?limit|throttl|backoff|retry)",
                r"(semaphore|concurrent|max.?workers|pool)",
                r"(ratelimit|tenacity|backoff)",
            ]
            rl_found = sum(1 for p in rate_limit_patterns if re.search(p, content, re.IGNORECASE))
            rate_limit_score = min(100, 30 + rl_found * 25)
            if rate_limit_score < 60:
                fixes.append("Add rate limiting: this script makes HTTP requests but has no sleep/backoff/retry. Add time.sleep() between requests, use tenacity for retries.")
        else:
            rate_limit_score = 80  # N/A gets a pass
        checks["rate_limiting"] = rate_limit_score

        # 4. Documentation completeness
        doc_score = 0
        # Module docstring
        if re.search(r'^"""[\s\S]*?"""', content) or re.search(r"^'''[\s\S]*?'''", content):
            doc_score += 30
        elif re.search(r'^#.*\n#', content):
            doc_score += 15
        # Function docstrings
        func_docs = len(re.findall(r'def\s+\w+[^:]+:\s*\n\s+"""', content))
        if func_docs > 0:
            doc_score += min(30, func_docs * 10)
        # Inline comments
        comments = len(re.findall(r"#\s+\w+", content))
        if comments >= 5:
            doc_score += 15
        elif comments >= 2:
            doc_score += 10
        # Usage/help text
        if re.search(r"(usage|--help|argparse|if __name__)", content, re.IGNORECASE):
            doc_score += 15
        # Constants/config at top
        if re.search(r"^[A-Z_]+\s*=", content, re.MULTILINE):
            doc_score += 10
        doc_score = min(100, doc_score)
        checks["documentation"] = doc_score
        if doc_score < 60:
            fixes.append("Add documentation: module docstring explaining what the script does, function docstrings for key functions, inline comments for non-obvious logic.")

        # 5. Code quality signals
        quality_score = 50
        # Hardcoded secrets
        if re.search(r'(api_key|password|secret|token)\s*=\s*["\'][^"\']{8,}["\']', content, re.IGNORECASE):
            quality_score -= 30
            fixes.append("SECURITY: Hardcoded secrets detected. Move to .env file and use os.getenv().")
        # Uses env vars for config
        if "os.getenv" in content or "os.environ" in content or "dotenv" in content:
            quality_score += 15
        # Has main guard
        if "if __name__" in content:
            quality_score += 10
        # Uses pathlib over os.path
        if "pathlib" in content or "Path(" in content:
            quality_score += 5
        # f-strings over .format
        fstrings = len(re.findall(r'f"[^"]*\{', content))
        formats = len(re.findall(r'\.format\(', content))
        if fstrings > formats:
            quality_score += 5
        # File is reasonable length
        if len(lines) > 1000:
            quality_score -= 10
            fixes.append(f"Script is {len(lines)} lines. Consider splitting into modules.")
        quality_score = max(0, min(100, quality_score))
        checks["code_quality"] = quality_score

        weights = {"error_handling": 0.25, "input_validation": 0.20, "rate_limiting": 0.15,
                    "documentation": 0.20, "code_quality": 0.20}
        final_score = int(sum(checks.get(k, 50) * weights[k] for k in weights))

        return {
            "name": str(path.relative_to(ROOT)),
            "score": final_score,
            "rating": rating(final_score),
            "checks": checks,
            "fixes": fixes,
        }

    def _aggregate_fixes(self, items: List[Dict]) -> str:
        issue_counts = defaultdict(int)
        for item in items:
            for fix in item.get("fixes", []):
                key = fix.split(":")[0] if ":" in fix else fix[:60]
                issue_counts[key] += 1
        top = sorted(issue_counts.items(), key=lambda x: -x[1])[:10]
        return "\n".join(f"({count}x) {issue}" for issue, count in top) if top else "All scripts passing."


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------

class QualityGate:
    """Main orchestrator that runs all quality dimensions."""

    def __init__(self):
        self.scorers = {
            "apps": AppQualityScorer(),
            "content": ContentQualityScorer(),
            "emails": EmailQualityScorer(),
            "listings": ListingQualityScorer(),
            "scripts": ScriptQualityScorer(),
        }
        self.results = {}

    def score_dimension(self, dim: str) -> Dict[str, Any]:
        scorer = self.scorers.get(dim)
        if not scorer:
            return {"error": f"Unknown dimension: {dim}"}
        result = scorer.score_all()
        self.results[dim] = result
        return result

    def score_all(self) -> Dict[str, Dict[str, Any]]:
        for dim in self.scorers:
            self.score_dimension(dim)
        return self.results

    def gate_check(self) -> Tuple[bool, Dict[str, Any]]:
        """Returns (passed, results). passed=False if ANY dimension < 60."""
        if not self.results:
            self.score_all()

        passed = True
        failures = {}
        for dim, result in self.results.items():
            score = result.get("score", 0)
            if score < THRESHOLDS["WARN"]:
                passed = False
                failures[dim] = {
                    "score": score,
                    "rating": result.get("rating", "BLOCK"),
                    "fix": result.get("fix", ""),
                }
        return passed, failures

    def overall_score(self) -> int:
        if not self.results:
            self.score_all()
        scores = [r.get("score", 0) for r in self.results.values()]
        return sum(scores) // max(len(scores), 1)

    # --- Output Formatters ---

    def print_summary(self):
        """Print a colored summary to terminal."""
        if not self.results:
            self.score_all()

        overall = self.overall_score()
        print(f"\n{'='*60}")
        print(f"  PRINTMAXX QUALITY GATE  |  Overall: {overall}/100 [{rating(overall)}]")
        print(f"{'='*60}\n")

        for dim, result in self.results.items():
            score = result.get("score", 0)
            r = rating(score)
            bar = self._score_bar(score)
            print(f"  {dim.upper():20s}  {bar}  {score:3d}/100  [{r}]")
            if result.get("items"):
                # Show worst 3 items
                worst = sorted(result["items"], key=lambda x: x.get("score", 0))[:3]
                for item in worst:
                    if item.get("score", 100) < 60:
                        print(f"    BLOCK  {item.get('name', '?')}: {item.get('score', 0)}/100")

        print(f"\n{'='*60}")
        passed, failures = self.gate_check()
        if passed:
            print("  GATE: PASSED - All dimensions above threshold.")
        else:
            print("  GATE: FAILED - Dimensions below 60/100:")
            for dim, info in failures.items():
                print(f"    {dim}: {info['score']}/100 [{info['rating']}]")
        print(f"{'='*60}\n")

    def _score_bar(self, score: int, width: int = 20) -> str:
        filled = int(score / 100 * width)
        empty = width - filled
        if score >= 80:
            char = "#"
        elif score >= 60:
            char = "="
        elif score >= 40:
            char = "-"
        else:
            char = "!"
        return f"[{char * filled}{'.' * empty}]"

    def generate_report(self) -> str:
        """Generate detailed markdown report."""
        if not self.results:
            self.score_all()

        overall = self.overall_score()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# PRINTMAXX Quality Gate Report",
            f"",
            f"Generated: {timestamp}",
            f"Overall Score: **{overall}/100** [{rating(overall)}]",
            f"",
        ]

        for dim, result in self.results.items():
            score = result.get("score", 0)
            lines.append(f"## {dim.upper()} - {score}/100 [{rating(score)}]")
            lines.append(f"")
            lines.append(result.get("summary", ""))
            lines.append("")

            # Show sub-checks for worst items
            items = result.get("items", [])
            if items:
                # Sort worst first
                sorted_items = sorted(items, key=lambda x: x.get("score", 0))

                # Worst items detail
                blocked = [i for i in sorted_items if i.get("score", 0) < 40]
                warned = [i for i in sorted_items if 40 <= i.get("score", 0) < 60]

                if blocked:
                    lines.append(f"### BLOCKED ({len(blocked)} items)")
                    for item in blocked[:10]:
                        lines.append(f"- **{item.get('name', '?')}**: {item.get('score', 0)}/100")
                        for fix in item.get("fixes", [])[:3]:
                            lines.append(f"  - FIX: {fix}")
                    lines.append("")

                if warned:
                    lines.append(f"### WARNINGS ({len(warned)} items)")
                    for item in warned[:10]:
                        lines.append(f"- **{item.get('name', '?')}**: {item.get('score', 0)}/100")
                        for fix in item.get("fixes", [])[:2]:
                            lines.append(f"  - FIX: {fix}")
                    lines.append("")

                # Stats
                scores_list = [i.get("score", 0) for i in items]
                if scores_list:
                    lines.append(f"**Stats:** min={min(scores_list)}, max={max(scores_list)}, "
                                 f"median={sorted(scores_list)[len(scores_list)//2]}, "
                                 f"items={len(scores_list)}")
                    lines.append("")

            # Aggregate fix
            agg_fix = result.get("fix", "")
            if agg_fix and agg_fix != "All content passing." and agg_fix != "All apps passing quality gate.":
                lines.append(f"### Top Fixes")
                lines.append(f"```")
                lines.append(agg_fix)
                lines.append(f"```")
                lines.append("")

            lines.append("---")
            lines.append("")

        # Gate result
        passed, failures = self.gate_check()
        lines.append(f"## Gate Result: {'PASSED' if passed else 'FAILED'}")
        if not passed:
            lines.append("")
            lines.append("Failing dimensions:")
            for dim, info in failures.items():
                lines.append(f"- {dim}: {info['score']}/100 - must be >= 60")
        lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Generate JSON output for webapp consumption."""
        if not self.results:
            self.score_all()

        passed, failures = self.gate_check()
        output = {
            "timestamp": datetime.now().isoformat(),
            "overall_score": self.overall_score(),
            "overall_rating": rating(self.overall_score()),
            "gate_passed": passed,
            "dimensions": {},
            "failures": failures,
        }

        for dim, result in self.results.items():
            dim_data = {
                "score": result.get("score", 0),
                "rating": result.get("rating", "BLOCK"),
                "summary": result.get("summary", ""),
                "item_count": len(result.get("items", [])),
                "items_below_60": len([i for i in result.get("items", []) if i.get("score", 0) < 60]),
                "items_below_40": len([i for i in result.get("items", []) if i.get("score", 0) < 40]),
                "top_fixes": [],
            }
            # Include worst items in JSON
            worst = sorted(result.get("items", []), key=lambda x: x.get("score", 0))[:5]
            dim_data["worst_items"] = [
                {"name": i.get("name", "?"), "score": i.get("score", 0),
                 "fixes": i.get("fixes", [])[:3]}
                for i in worst
            ]
            output["dimensions"][dim] = dim_data

        return json.dumps(output, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PRINTMAXX Quality Gate - Quant-level quality scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Thresholds:
              BLOCK:     0-39   (cannot ship)
              WARN:     40-59   (needs fixes)
              PASS:     60-79   (shippable)
              EXCELLENT: 80-100 (top tier)

            Gate mode returns exit code 1 if ANY dimension < 60.
        """),
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--score-all", action="store_true", help="Score all dimensions")
    group.add_argument("--score-apps", action="store_true", help="Score app quality only")
    group.add_argument("--score-content", action="store_true", help="Score content quality only")
    group.add_argument("--score-emails", action="store_true", help="Score email quality only")
    group.add_argument("--score-listings", action="store_true", help="Score listing quality only")
    group.add_argument("--score-scripts", action="store_true", help="Score script quality only")
    group.add_argument("--gate", action="store_true", help="Gate check (exit 1 if failing)")
    group.add_argument("--report", action="store_true", help="Generate detailed markdown report")
    group.add_argument("--api-json", action="store_true", help="JSON output for webapp")

    args = parser.parse_args()
    gate = QualityGate()

    if args.score_all:
        gate.score_all()
        gate.print_summary()

    elif args.score_apps:
        gate.score_dimension("apps")
        gate.print_summary()

    elif args.score_content:
        gate.score_dimension("content")
        gate.print_summary()

    elif args.score_emails:
        gate.score_dimension("emails")
        gate.print_summary()

    elif args.score_listings:
        gate.score_dimension("listings")
        gate.print_summary()

    elif args.score_scripts:
        gate.score_dimension("scripts")
        gate.print_summary()

    elif args.gate:
        gate.score_all()
        gate.print_summary()
        passed, failures = gate.gate_check()
        if not passed:
            print("GATE FAILED. Fix the above issues before shipping.")
            sys.exit(1)
        else:
            print("GATE PASSED. All dimensions above threshold.")
            sys.exit(0)

    elif args.report:
        gate.score_all()
        report = gate.generate_report()
        print(report)

    elif args.api_json:
        gate.score_all()
        print(gate.to_json())


if __name__ == "__main__":
    main()
