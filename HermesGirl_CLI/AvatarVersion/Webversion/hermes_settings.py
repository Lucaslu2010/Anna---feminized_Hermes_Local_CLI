import json
import os
from typing import Dict, List

from hermes_locator import (
    find_hermes_config_path,
    find_hermes_memory_paths,
    get_installed_hermes_skills_text,
)

try:
    import yaml
except Exception:
    yaml = None


DEFAULT_HERMES_CONFIG = {
    "provider": "",
    "api_key": "",
    "base_url": "",
    "temperature": "",
}


def load_hermes_config() -> Dict[str, str]:
    path = find_hermes_config_path(create=False)
    config = dict(DEFAULT_HERMES_CONFIG)

    if not os.path.exists(path):
        return config

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return config

    if path.endswith(".json"):
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                model = data.get("model", {})
                if not isinstance(model, dict):
                    model = data

                config["provider"] = str(model.get("provider", config["provider"]))
                config["api_key"] = str(
                    model.get("api_key", model.get("apiKey", config["api_key"]))
                )
                config["base_url"] = str(
                    model.get("base_url", model.get("baseUrl", config["base_url"]))
                )
                config["temperature"] = str(model.get("temperature", config["temperature"]))
        except Exception:
            pass
        return config

    if yaml is not None:
        try:
            data = yaml.safe_load(content) or {}
            model = data.get("model", {})
            if isinstance(model, dict):
                config["provider"] = str(model.get("provider", ""))
                config["api_key"] = str(model.get("api_key", ""))
                config["base_url"] = str(model.get("base_url", ""))
                temperature = model.get("temperature", "")
                config["temperature"] = "" if temperature is None else str(temperature)
        except Exception:
            pass

    return config


def save_hermes_config(values: Dict[str, str]) -> str:
    path = find_hermes_config_path(create=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = ""

    if path.endswith(".json"):
        try:
            data = json.loads(content) if content.strip() else {}
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}

        model = data.setdefault("model", {})
        if not isinstance(model, dict):
            model = {}
            data["model"] = model

        model["provider"] = values.get("provider", "")
        model["api_key"] = values.get("api_key", "")
        model["base_url"] = values.get("base_url", "")
        temperature = _coerce_temperature(values.get("temperature", ""))
        if temperature == "":
            model.pop("temperature", None)
        else:
            model["temperature"] = temperature

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        _protect_file(path)
        return path

    if yaml is None:
        raise RuntimeError("PyYAML is required to edit Hermes config.yaml.")

    data = yaml.safe_load(content) if content.strip() else {}
    if not isinstance(data, dict):
        data = {}

    model = data.setdefault("model", {})
    if not isinstance(model, dict):
        model = {}
        data["model"] = model

    model["provider"] = values.get("provider", "")
    model["api_key"] = values.get("api_key", "")
    model["base_url"] = values.get("base_url", "")

    temperature = _coerce_temperature(values.get("temperature", ""))
    if temperature == "":
        model.pop("temperature", None)
    else:
        model["temperature"] = temperature

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    _protect_file(path)
    return path


def read_hermes_memory() -> str:
    paths = find_hermes_memory_paths()
    if not paths:
        return "No Hermes memory files found."

    sections = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read().strip()
        except Exception as e:
            content = f"Could not read memory file: {e}"

        sections.append(f"# {path}\n\n{content}")

    return "\n\n---\n\n".join(sections)


def list_hermes_skills() -> List[str]:
    text = read_hermes_skills_text()
    if not text:
        return ["No Hermes skills found."]

    return text.splitlines()


def read_hermes_skills_text() -> str:
    text = get_installed_hermes_skills_text()
    if not text:
        return "No Hermes skills found."

    return text


def _coerce_temperature(value: str):
    value = str(value or "").strip()
    if not value:
        return ""
    return float(value)


def _protect_file(path: str):
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
