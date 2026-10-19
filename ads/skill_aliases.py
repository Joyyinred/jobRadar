"""
One name per skill: "Microsoft Azure" and "Azure" are the same thing.

The LLM reads the ad; CODE decides what a skill is called (DESIGN_DOC §6.2:
"the model never decides what the canonical name is"). The same rule then
applies to every ad, today and in a year — the LLM wouldn't promise that.

Kept in code, not a database table: every change is reviewed in a PR and
visible in git history, and it's trivially testable. Move it to a table the
day someone who doesn't write code needs to edit it.

How to grow it: look at the market statistics (or run
`python manage.py merge_skills --dry-run`), spot two names for one thing,
add a line, run `python manage.py merge_skills`.

Deliberately NOT merged: a more specific thing into its parent
("AWS Lambda" -> "AWS", "Printer troubleshooting" -> "Troubleshooting").
That's a hierarchy, not another name for the same skill — merging would
throw information away.

Matching ignores case and extra spaces.
"""

# canonical name -> other names that mean exactly the same skill
SKILL_ALIASES = {
    ".NET": [".NET Core", ".NET 8", ".NET Framework", "dotnet"],
    "Agile": ["Agile methodologies", "Agile methods", "Agile development"],
    "AWS": ["Amazon Web Services"],
    "Azure": ["Microsoft Azure", "MS Azure"],
    "CI/CD": ["CI/CD pipelines", "Continuous Integration", "Continuous Integration/Continuous Deployment"],
    "GCP": ["Google Cloud", "Google Cloud Platform"],
    "JavaScript": ["JS"],
    "Kubernetes": ["k8s"],
    "LLM": ["LLMs", "Large language models", "Large language models (LLMs)"],
    "Machine Learning": ["ML"],
    "Node.js": ["NodeJS", "Node"],
    "PostgreSQL": ["Postgres", "psql"],
    "React": ["React.js", "ReactJS"],
    "REST API": ["REST", "REST APIs", "RESTful API", "RESTful APIs"],
}

# one name that is really several skills
SKILL_SPLITS = {
    "C/C++": ["C", "C++"],
}

# Names where case DOES matter: different things that differ only in case.
# Everywhere else "Cloud" and "cloud" are one skill; these are never merged.
#   LoRA — Low-Rank Adaptation, a way to fine-tune LLMs
#   LoRa — Long Range, a radio protocol for IoT devices
# (Found by `merge_skills --dry-run`, which wanted to merge them.)
CASE_SENSITIVE_NAMES = {"LoRA", "LoRa"}


def _key(name):
    return " ".join(name.split()).lower()


_ALIAS_TO_CANONICAL = {
    _key(alias): canonical for canonical, aliases in SKILL_ALIASES.items() for alias in aliases
}
_ALIAS_TO_CANONICAL.update({_key(canonical): canonical for canonical in SKILL_ALIASES})
_SPLITS = {_key(name): parts for name, parts in SKILL_SPLITS.items()}

CANONICAL_NAMES = set(SKILL_ALIASES) | {part for parts in SKILL_SPLITS.values() for part in parts}
_CASE_SENSITIVE_KEYS = {name.lower() for name in CASE_SENSITIVE_NAMES}


def case_matters(name):
    """True for names like LoRA / LoRa that must not be matched ignoring case."""
    return " ".join(name.split()).lower() in _CASE_SENSITIVE_KEYS


def canonical_names(name):
    """The skill name(s) to store for a name the LLM produced.

    "Microsoft Azure" -> ["Azure"];  "C/C++" -> ["C", "C++"];
    "  machine   learning " -> ["Machine Learning"];  anything else -> [the name, trimmed].
    """
    key = _key(name)
    if key in _SPLITS:
        return list(_SPLITS[key])
    if key in _ALIAS_TO_CANONICAL:
        return [_ALIAS_TO_CANONICAL[key]]
    return [" ".join(name.split())]
