import re
from typing import Optional


ANSI_RE = re.compile(
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


def strip_ansi(text: str) -> str:
    if not text:
        return ""

    text = ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def normalize_terminal_text(text: str) -> str:
    text = strip_ansi(text)

    # Remove common terminal drawing/control fragments.
    for ch in ["▮", "█", "▒", "░", "□", "■", "▯", "▰"]:
        text = text.replace(ch, "")

    return text.strip()


def is_terminal_noise(text: str) -> bool:
    """
    Return True if this terminal output should not affect avatar state.
    """

    if not text:
        return True

    t = normalize_terminal_text(text)
    lower = t.lower()

    if not t:
        return True

    noise_keywords = [
        "connecting to hermes",
        "welcome to hermes",
        "hermes agent",
        "type your message",
        "/help",
        "/queue",
        "/bg",
        "/steer",
        "ctrl+c cancel",
        "ctx --",
        "hy3-preview",
        "preview:free",
        "msg=interrupt",
        "tools",
        "skills",
        "preloaded skills",
        "what can i help you with today",
    ]

    if any(k in lower for k in noise_keywords):
        return True

    # Pure numbers / timer fragments / percentage fragments.
    if re.fullmatch(r"\d+", lower):
        return True

    if re.fullmatch(r"\d+\s+\d+", lower):
        return True

    if re.fullmatch(r"\d+\s+\d+s", lower):
        return True

    if re.search(r"\d+(\.\d+)?k\s*/\s*\d+(\.\d+)?k", lower):
        return True

    if re.search(r"\b\d+%\b", lower) and re.search(r"\b\d+s\b", lower):
        return True

    # Mostly punctuation / borders.
    visible = re.sub(r"[\s\-\_=|:;,.·/\\[\](){}<>›»$#%+]+", "", t)
    if len(visible) <= 1:
        return True

    return False

def detect_avatar_state_from_terminal(text: str) -> Optional[str]:
    """
    Convert terminal output into an avatar state.

    Priority:
        warning > searching > coding > success > thinking > explain > happy > talking > None

    Return:
        state name if avatar should change
        None if this output should not affect avatar
    """

    if not text:
        return None

    raw = text
    t = normalize_terminal_text(text)
    lower = t.lower()

    if not t:
        return None

    # 1. Warning / error: highest priority
    warning_keywords = [
        "error",
        "failed",
        "failure",
        "warning",
        "dangerous",
        "permission denied",
        "denied",
        "not found",
        "cannot",
        "can't",
        "unable",
        "http 500",
        "internal server error",
        "api call failed",
        "exception",
        "traceback",
        "crash",
        "invalid",
        "timeout",
        "timed out",
        "refused",
        "forbidden",
        "unauthorized",
        "ssl",
        "certificate",
        "报错",
        "错误",
        "失败",
        "无法",
        "不能",
        "危险",
        "权限",
        "崩溃",
    ]

    if any(k in lower for k in warning_keywords):
        return "warning"

    # 2. Searching / tools / web / command execution
    # Put this BEFORE thinking, because Hermes often shows thinking words while using tools.
    searching_keywords = [
        "curl",
        "wget",
        "fetch",
        "request",
        "requests",
        "endpoint",
        "api",
        "openrouter",
        "browser",
        "website",
        "web",
        "http://",
        "https://",
        "search",
        "searching",
        "query",
        "tool",
        "tool_call",
        "function_call",
        "shell",
        "terminal",
        "command",
        "executing",
        "running",
        "subprocess",
        "GET ",
        "POST ",
        "查询",
        "搜索",
        "请求",
        "网站",
        "网页",
        "命令",
        "终端",
        "工具",
        "执行",
        "运行",
    ]

    if any(k.lower() in lower for k in searching_keywords):
        return "searching"

    # 3. Coding / debugging
    coding_patterns = [
        r"```",
        r"\bimport\s+\w+",
        r"\bfrom\s+\w+\s+import\b",
        r"\bdef\s+\w+\s*\(",
        r"\bclass\s+\w+",
        r"\bsubprocess\b",
        r"\bthreading\b",
        r"\bqueue\b",
        r"\bqthread\b",
        r"\bqtimer\b",
        r"\bfunction\b",
        r"\bvariable\b",
        r"\bsyntax\b",
        r"\bdebug\b",
        r"\bbug\b",
        r"\bpython\b",
        r"\bjavascript\b",
        r"\bhtml\b",
        r"\bcss\b",
        r"\bsql\b",
        r"\bjson\b",
        r"\bcode\b",
    ]

    if any(re.search(p, lower) for p in coding_patterns):
        return "coding"

    chinese_coding_keywords = [
        "代码",
        "函数",
        "变量",
        "类",
        "调试",
        "脚本",
        "程序",
    ]

    if any(k in lower for k in chinese_coding_keywords):
        return "coding"

    # 4. Success / completion
    success_keywords = [
        "completed",
        "finished",
        "success",
        "successful",
        "created",
        "saved",
        "updated",
        "done",
        "fixed",
        "resolved",
        "generated",
        "完成",
        "已完成",
        "成功",
        "保存好了",
        "生成好了",
        "修改好了",
        "修好了",
        "解决了",
    ]

    if any(k in lower for k in success_keywords):
        return "success"

    # 5. Thinking / loading
    thinking_keywords = [
        "musing",
        "cogitating",
        "ruminating",
        "deliberating",
        "mulling",
        "reflecting",
        "thinking",
        "initializing agent",
    ]

    thinking_symbols = [
        "⚕",
        "◉",
        "◎",
        "◌",
        "◷",
        "⏱",
    ]

    if any(k in lower for k in thinking_keywords):
        return "thinking"

    if any(s in raw for s in thinking_symbols):
        return "thinking"

    # 6. Ignore terminal noise after strong state detection.
    if is_terminal_noise(text):
        return None

    # 7. Explanation
    explain_keywords = [
        "first",
        "second",
        "third",
        "finally",
        "step",
        "solution",
        "because",
        "therefore",
        "for example",
        "the reason",
        "you need to",
        "you should",
        "recommend",
        "suggest",
        "首先",
        "其次",
        "然后",
        "最后",
        "步骤",
        "方案",
        "建议",
        "原因",
        "具体来说",
        "也就是说",
        "换句话说",
    ]

    if any(k in lower for k in explain_keywords):
        return "explain"

    # 8. Short positive response
    happy_keywords = [
        "ok",
        "okay",
        "sure",
        "great",
        "nice",
        "no problem",
        "没问题",
        "好的",
        "可以",
        "好滴",
    ]

    if len(lower) <= 80 and any(k in lower for k in happy_keywords):
        return "happy"

    # 9. Generic talking, conservative
    if len(t) >= 30:
        return "talking"

    return None