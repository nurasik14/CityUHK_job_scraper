"""CityU department codes and names, as listed in the AIMS job search form (~210 entries).

Extracted once from the AIMS page into data/aims_departments.json, so the UI needs no login
to show the list.
"""

import json
from functools import cache
from importlib import resources

from cityu_jobs import config


@cache
def load_departments() -> list[dict[str, str]]:
    """[{"code": "CS", "name": "Computer Science"}, ...], the default department first."""
    text = resources.files("cityu_jobs").joinpath("data/aims_departments.json").read_text(encoding="utf-8")
    departments = json.loads(text)
    # sorted() is stable: the default goes first and the rest keep the AIMS order.
    return sorted(departments, key=lambda d: d["code"] != config.DEFAULT_DEPARTMENT)


def department_name(code: str | None) -> str | None:
    return next((d["name"] for d in load_departments() if d["code"] == code), None)
