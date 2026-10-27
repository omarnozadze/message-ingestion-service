from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

_ENV_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_AGENT_CODE = re.compile(r"^(?:PS\s*)?(\d{3})$", re.IGNORECASE)


def normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("00995"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("05"):
        digits = "995" + digits[1:]
    elif len(digits) == 9 and digits.startswith("5"):
        digits = "995" + digits
    if not digits:
        raise ValueError(f"sender contains no phone digits: {value!r}")
    return digits


def normalize_agent_codes(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        match = _AGENT_CODE.fullmatch(str(raw).strip())
        if match is None:
            raise ValueError(f"invalid agent code: {raw!r}")
        code = match.group(1)
        if code not in result:
            result.append(code)
    if len(result) < 2:
        raise ValueError("a multi-agent sender must have at least two agent codes")
    return result


def _load_json_object(raw: str, *, key: str) -> dict:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{key} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a JSON object")
    return value


def _mapping_key_phone(key: str) -> str | None:
    value = str(key).strip()
    lower = value.casefold()
    if lower.startswith("name:"):
        return None
    if lower.startswith("id:"):
        value = value[3:]
    try:
        return normalize_phone(value)
    except ValueError:
        return None


def update_env_text(
    text: str,
    *,
    sender: str,
    agent_codes: Iterable[str],
    processing_timeout_seconds: int | None = 1200,
) -> str:
    sender_digits = normalize_phone(sender)
    requested_codes = normalize_agent_codes(agent_codes)

    newline = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(("\n", "\r"))
    lines = text.splitlines()
    indices: dict[str, int] = {}
    values: dict[str, str] = {}

    for index, line in enumerate(lines):
        match = _ENV_KEY.match(line)
        if match:
            indices[match.group(1)] = index
            values[match.group(1)] = match.group(2)

    sender_map = _load_json_object(values.get("AGENT_SENDER_MAP", "{}"), key="AGENT_SENDER_MAP")
    multi_map = _load_json_object(
        values.get("AGENT_MULTI_SENDER_MAP", "{}"),
        key="AGENT_MULTI_SENDER_MAP",
    )

    merged_codes: list[str] = []

    # A fixed mapping takes precedence in AgentResolver. Move any matching fixed
    # sender entry into the multi map so message prefixes such as 202 can route.
    for key in list(sender_map):
        if _mapping_key_phone(key) == sender_digits:
            match = _AGENT_CODE.fullmatch(str(sender_map.pop(key)).strip())
            if match and match.group(1) not in merged_codes:
                merged_codes.append(match.group(1))

    for key in list(multi_map):
        if _mapping_key_phone(key) == sender_digits:
            existing = multi_map.pop(key)
            if not isinstance(existing, list):
                raise ValueError(
                    f"AGENT_MULTI_SENDER_MAP entry {key!r} must be a JSON list"
                )
            for code in normalize_agent_codes(existing):
                if code not in merged_codes:
                    merged_codes.append(code)

    for code in requested_codes:
        if code not in merged_codes:
            merged_codes.append(code)

    canonical_key = f"id:+{sender_digits}"
    multi_map[canonical_key] = merged_codes

    replacements = {
        "AGENT_SENDER_MAP": json.dumps(
            sender_map,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "AGENT_MULTI_SENDER_MAP": json.dumps(
            multi_map,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    if processing_timeout_seconds is not None:
        if processing_timeout_seconds < 60:
            raise ValueError("processing timeout must be at least 60 seconds")
        replacements["MESSAGE_PROCESSING_TIMEOUT_SECONDS"] = str(
            processing_timeout_seconds
        )

    for key, value in replacements.items():
        rendered = f"{key}={value}"
        if key in indices:
            lines[indices[key]] = rendered
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(rendered)

    result = newline.join(lines)
    if trailing_newline or not result:
        result += newline
    return result


def update_env_file(
    path: Path,
    *,
    sender: str,
    agent_codes: Iterable[str],
    processing_timeout_seconds: int | None = 1200,
    backup: bool = True,
) -> Path | None:
    if not path.is_file():
        raise FileNotFoundError(path)

    original = path.read_text(encoding="utf-8-sig")
    updated = update_env_text(
        original,
        sender=sender,
        agent_codes=agent_codes,
        processing_timeout_seconds=processing_timeout_seconds,
    )

    backup_path: Path | None = None
    if backup and updated != original:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.backup-{stamp}")
        shutil.copy2(path, backup_path)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    return backup_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely add a WhatsApp sender to AGENT_MULTI_SENDER_MAP.",
    )
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--sender", required=True)
    parser.add_argument("--agents", nargs="+", required=True)
    parser.add_argument("--processing-timeout", type=int, default=1200)
    parser.add_argument("--no-backup", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    backup_path = update_env_file(
        args.env,
        sender=args.sender,
        agent_codes=args.agents,
        processing_timeout_seconds=args.processing_timeout,
        backup=not args.no_backup,
    )
    print(f"Updated {args.env}")
    if backup_path is not None:
        print(f"Backup: {backup_path}")
    rendered_agents = ", ".join(normalize_agent_codes(args.agents))
    print(f"Sender +{normalize_phone(args.sender)} agents: {rendered_agents}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
