import re
from typing import Optional


# =========================
# ANSI / Terminal Control
# =========================

ANSI_ESCAPE_RE = re.compile(
    r"""
    \x1B
    (?:
        [@-Z\\-_]
        |
        \[
        [0-?]*
        [ -/]*
        [@-~]
    )
    """,
    re.VERBOSE,
)

OSC_ESCAPE_RE = re.compile(
    r"""
    \x1B
    \]
    .*?
    (?:\x07|\x1B\\)
    """,
    re.VERBOSE | re.DOTALL,
)


# =========================
# Strong Hermes TUI Noise Filter
# =========================
HERMES_STATUS_WORDS = [
    "pondering",
    "contemplating",
    "musing",
    "cogitating",
    "ruminating",
    "deliberating",
    "mulling",
    "reflecting",
    "processing",
    "reasoning",
    "analyzing",
    "computing",
    "synthesizing",
    "formulating",
    "brainstorming",
    "💻",
    "●",
    "🐍",
    "❌",
    "✅",
    "⚠️",
    "📌",
    "👉",
    "🔎",
    "⚡",
    "grep",
    "⊙",
    "(｡◕‿◕｡)",
    "(◕‿◕✿)",
    "٩(◕‿◕｡)۶",
    "(✿◠‿◠)",
    "( ˘▽˘)っ",
    "♪(´ε` )",
    "(◕ᴗ◕✿)",
    "ヾ(＾∇＾)",
    "(≧◡≦)",
    "(★ω★)",
    "(｡•́︿•̀｡)",
    "(◔_◔)",
    "(¬‿¬)",
    "( •_•)>⌐■-■",
    "(⌐■_■)",
    "(´･_･`)",
    "◉_◉",
    "(°ロ°)",
    "( ˘⌣˘)♡",
    "ヽ(>∀<☆)☆",
    "٩(๑❛ᴗ❛๑)۶",
    "(⊙_⊙)",
    "(¬_¬)",
    "( ͡° ͜ʖ ͡°)",
    "ಠ_ಠ",

]

NOISE_PATTERNS = [
    # empty / prompt only
    r"^\s*$",
    r"^\s*[>$#]\s*$",
    r"^\s*[›»]\s*$",

    # pure UI border / separator
    r"^[\s\-\_=|│┃┌┐└┘├┤┬┴┼╭╮╰╯╔╗╚╝╠╣╦╩╬─━═]+$",

    # repeated title
    r"^\s*Hermes\s*$",
    r"^\s*Hermes\s+Agent\s*$",

    # startup intro
    r"^Welcome\s+to\s+Hermes\s+Agent.*$",
    r"^Hermes\s+Agent,\s+your\s+CLI\s+AI\s+assistant.*$",
    r"^.*What\s+can\s+I\s+help\s+you\s+with\s+today\??\s*$",

    # tips / warnings
    r"^.*Type\s+your\s+message\s+or\s+/help\s+for\s+commands.*$",
    r"^.*Tip:\s*HERMES_IGNORE_RULES.*$",
    r"^.*AGENTS\.md.*$",
    r"^.*SOUL\.md.*$",
    r"^.*\.cursorrules.*$",
    r"^.*preloaded\s+skills.*$",
    r"^Warning:\s*Input\s+is\s+not\s+a\s+terminal.*$",

    # tool / skill / command summaries
    r"^.*\d+\s+tools\s*·\s*\d+\s+skills.*$",
    r"^.*\/help\s+for\s+commands.*$",
    r"^\s*commands\s*$",

    # TUI command/status lines
    r"^.*msg\s*=\s*interrupt.*$",
    r"^.*\/queue.*\/bg.*\/steer.*$",
    r"^.*Ctrl\+C\s+cancel.*$",

    # model / token / progress status
    r"^.*hy3-preview.*$",
    r"^.*preview:free.*$",
    r"^.*ctx\s*--.*$",
    r"^.*\d+(\.\d+)?K\s*/\s*\d+(\.\d+)?K.*$",
    r"^.*\[\s*\]\s*\d+%.*$",
    r"^.*\d+%\s+\d+s.*$",
    r"^.*◷.*$",
    r"^.*⏱.*$",
    r"^.*\|\s*\d+s\s*.*$",

    # skill/category lines
    r"^\s*\|\s*[\w\-]+:\s*[\w\-]+.*\|\s*$",
    r"^\s*\|\s*[\w\-]+.*\|\s*$",

    # common debug/loading lines
    r"^\s*Loading.*$",
    r"^\s*Thinking.*$",
    r"^\s*\[debug\].*$",
    r"^\s*\[info\].*$",
    r"^\s*\[warning\].*$",
    r"^\s*debug\s*:.*$",
    r"^\s*info\s*:.*$",
    r"^\s*warning\s*:.*$",
    # numeric progress fragments
    r"^\s*\d+\s*$",
    r"^\s*\d+\s+\d+\s*$",
    r"^\s*\d+\s+\d+s\s*$",
    r"^\s*\d+s\s*$",
    r"^\s*\d+\s*/\s*\d+\s*$",
    r"^\s*\d+(\.\d+)?K\s*$",

    # Hermes runtime states
    r"^Initializing\s+agent.*$",
    r"^.*ruminating.*$",
    r"^.*Retrying\s+in\s+\d+(\.\d+)?s.*$",
    # API / backend logs
    r"^.*API\s+call\s+failed.*$",
    r"^.*InternalServerError.*$",
    r"^.*HTTP\s+500.*$",
    r"^.*Internal\s+Server\s+Error.*$",
    r"^.*Endpoint:\s*https?://.*$",
    r"^.*openrouter\.ai.*$",
    r"^.*Retrying\s+in.*$",

]


def remove_ansi(text: str) -> str:
    if not text:
        return ""

    text = OSC_ESCAPE_RE.sub("", text)
    text = ANSI_ESCAPE_RE.sub("", text)
    return text


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.replace("\x00", "")
    text = text.replace("\u200b", "")

    # Remove block/progress characters commonly used in terminal UI.
    for ch in ["▮", "█", "▒", "░", "□", "■", "▯", "▰"]:
        text = text.replace(ch, "")

    return text


def remove_box_chars(text: str) -> str:
    box_chars = "─━═│┃┌┐└┘├┤┬┴┼╭╮╰╯╔╗╚╝╠╣╦╩╬"
    for ch in box_chars:
        text = text.replace(ch, "")
    return text


def strip_wrapping_pipes(line: str) -> str:
    line = line.strip()

    if line.startswith("|") and line.endswith("|"):
        return line[1:-1].strip()

    return line


def is_noise_line(line: str) -> bool:
    stripped = line.strip()

    if not stripped:
        return True
    if "⚕" in stripped:
        return True
    # Remove leading terminal prompt markers before checking.
    normalized = stripped
    normalized = re.sub(r"^\s*[$#>›»]\s*", "", normalized).strip()

    # Pure number / countdown / progress fragments.
    if re.fullmatch(r"\d+", normalized):
        return True

    if re.fullmatch(r"\d+\s+\d+", normalized):
        return True

    if re.fullmatch(r"\d+\s+\d+s", normalized):
        return True

    if re.fullmatch(r"\d+s", normalized):
        return True

    if re.fullmatch(r"\d+(\.\d+)?K", normalized, flags=re.IGNORECASE):
        return True

    # Examples:
    # 4 1
    # 5 2
    # 3 10s
    # Hermes
    # $ Hermes
    if normalized.lower() == "hermes":
        return True

    for pattern in NOISE_PATTERNS:
        if re.match(pattern, stripped, flags=re.IGNORECASE):
            return True

    lowered = stripped.lower()
    if any(word in lowered for word in HERMES_STATUS_WORDS):
        return True
    hard_keywords = [
        "msg=interrupt",
        "/queue",
        "/bg",
        "/steer",
        "ctrl+c cancel",
        "hy3-preview",
        "preview:free",
        "ctx --",
        "initializing agent",
        "ruminating",
    ]

    if any(keyword in lowered for keyword in hard_keywords):
        return True

    # Filter token progress lines like:
    # hy3-preview:free 13.6K/262.1K [] 5% 25s
    if re.search(r"\d+(\.\d+)?k\s*/\s*\d+(\.\d+)?k", lowered):
        return True

    if re.search(r"\b\d+%\b", lowered) and re.search(r"\b\d+s\b", lowered):
        return True

    # Remove lines that are mostly punctuation / UI fragments.
    visible = re.sub(r"[\s\-\_=|:;,.·/\\[\](){}<>›»$#%+]+", "", stripped)
    if len(visible) <= 1:
        return True

    return False
def clean_hermes_output(
    text: str,
    remove_noise: bool = True,
) -> Optional[str]:
    """
    Clean raw Hermes CLI/TUI output.

    Returns:
        str  -> displayable dialogue text
        None -> should be ignored
    """

    if not text:
        return None

    text = remove_ansi(text)
    text = normalize_text(text)
    text = remove_box_chars(text)

    lines = text.split("\n")
    cleaned_lines = []

    for line in lines:
        line = strip_wrapping_pipes(line)
        line = line.rstrip()

        if remove_noise and is_noise_line(line):
            continue

        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines).strip()

    if not cleaned:
        return None

    return cleaned


def clean_user_input(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()
def is_loading_signal(text: str) -> bool:
    """
    Detect Hermes TUI/status output that means the agent is working,
    even if this line should not be displayed as chat content.
    """

    if not text:
        return False

    text = remove_ansi(text)
    text = normalize_text(text)

    lowered = text.lower()

    loading_keywords = [
        "initializing agent",
        "musing",
        "cogitating",
        "ruminating",
        "deliberating",
        "mulling",
        "reflecting",
        "thinking",
        "retrying",
        "msg=interrupt",
        "hy3-preview",
        "preview:free",
        "ctx --",
    ]

    loading_symbols = [
        "⚕",
        "◉",
        "◎",
        "◌",
        "◷",
        "⏱",
    ]

    if any(keyword in lowered for keyword in loading_keywords):
        return True

    if any(symbol in text for symbol in loading_symbols):
        return True

    # Token/progress fragments, e.g.:
    # 13.6K/262.1K [] 5% 25s
    if re.search(r"\d+(\.\d+)?k\s*/\s*\d+(\.\d+)?k", lowered):
        return True

    if re.search(r"\b\d+%\b", lowered) and re.search(r"\b\d+s\b", lowered):
        return True

    # Countdown fragments, e.g.:
    # 4 1
    # 5 2
    # 3 10s
    stripped = text.strip()
    stripped = re.sub(r"^\s*[$#>›»]\s*", "", stripped).strip()

    if re.fullmatch(r"\d+\s+\d+", stripped):
        return True

    if re.fullmatch(r"\d+\s+\d+s", stripped):
        return True

    return False