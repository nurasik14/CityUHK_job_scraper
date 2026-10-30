"""The student's profile: CV text, projects, skills. Used to estimate how hard each job is
for them (core/fit.py) and to write cover letters (core/letters.py).

Stored in .data/profile.json with file mode 600: it is personal data. It is sent to the
AI only when estimating fit or writing a letter (with Claude: to Anthropic).

CVs can be uploaded as PDF, Word (.docx), LaTeX (.tex), Markdown or plain text; the text is
extracted here and can then be edited on the Profile page.
"""

import hashlib
import html
import io
import json
import os
import re
import zipfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime

from cityu_jobs import config


class CVError(ValueError):
    """The uploaded file could not be read as a CV."""


@dataclass
class Profile:
    name: str = ""
    email: str = ""
    phone: str = ""
    education: str = ""   # e.g. "BSc Computer Science, CityU, Year 1 (2026-2030)"
    skills: str = ""      # e.g. "Python, PyTorch, FastAPI, TypeScript"
    projects: str = ""    # one project per paragraph: what, stack, result, link
    experience: str = ""  # jobs, research, competitions
    extra: str = ""       # languages, awards, visa / availability, links
    cv_text: str = ""     # extracted from the uploaded CV (editable)
    cv_filename: str = ""
    updated_at: str = ""

    @classmethod
    def load(cls) -> "Profile":
        try:
            data = json.loads(config.PROFILE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known and isinstance(v, str)})

    def save(self) -> None:
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        config.PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = config.PROFILE_FILE.with_suffix(".tmp")
        # Create readable by this user only, before any personal data is written.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, config.PROFILE_FILE)
        os.chmod(config.PROFILE_FILE, 0o600)

    def is_empty(self) -> bool:
        return not any(getattr(self, f).strip() for f in ("education", "skills", "projects", "experience", "extra", "cv_text"))

    def as_text(self) -> str:
        """The profile as one text block for the AI, capped at PROFILE_MAX_CHARS."""
        parts = [
            ("Name", self.name),
            ("Education", self.education),
            ("Skills", self.skills),
            ("Projects", self.projects),
            ("Experience", self.experience),
            ("Other (languages, awards, availability, visa)", self.extra),
            ("CV", self.cv_text),
        ]
        text = "\n\n".join(f"## {label}\n{value.strip()}" for label, value in parts if value.strip())
        return text[: config.PROFILE_MAX_CHARS]

    def fingerprint(self) -> str:
        """Changes whenever the profile's content changes, so fit estimates are redone."""
        return hashlib.sha256(self.as_text().encode("utf-8")).hexdigest()[:16]


# --- CV text extraction ------------------------------------------------------------


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # imported here: only needed for PDF uploads

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as e:  # pypdf raises many kinds of errors on broken files
        raise CVError(f"could not read the PDF ({type(e).__name__})") from e


def _docx_text(data: bytes) -> str:
    """Word files are zip archives; the text is in word/document.xml, one <w:p> per paragraph."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as e:
        raise CVError("not a valid .docx file") from e
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.S)
    lines = ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paragraphs]
    return "\n".join(html.unescape(line) for line in lines)


def _tex_text(text: str) -> str:
    """Rough LaTeX -> text: keep command arguments, drop the commands, preamble and comments."""
    if "\\begin{document}" in text:
        text = text.split("\\begin{document}", 1)[1]
    text = text.split("\\end{document}", 1)[0]
    text = re.sub(r"(?<!\\)%.*", "", text)                                   # comments
    text = re.sub(r"\\href\{([^}]*)\}\{([^}]*)\}", r"\2 (\1)", text)          # links
    text = text.replace("---", "—").replace("--", "–").replace("\\cdot", "·")
    text = re.sub(r"\}\s*\{", "} · {", text)  # \entry{a}{b}{c}: keep the arguments apart
    text = re.sub(r"\\(?:section|subsection|cvsection)\*?\{([^}]*)\}", r"\n\n\1\n", text)
    text = re.sub(r"\\item\b", "\n• ", text)
    text = re.sub(r"\\\\(\[[^\]]*\])?", "\n", text)                          # line breaks
    text = re.sub(r"\\(?:begin|end)\{[^}]*\}(\{[^}]*\}|\[[^\]]*\])*", "", text)
    text = re.sub(r"\\(?:vspace|hspace|setlength|addtolength)\*?\{[^}]*\}(\{[^}]*\})?", "", text)
    for _ in range(3):                                                        # nested \textbf{\emph{x}}
        text = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)
    text = text.replace("~", " ").replace("\\&", "&").replace("\\%", "%").replace("\\$", "$")
    text = re.sub(r"[{}$]", "", text)
    return re.sub(r"(?m)^[ \t]*·[ \t]*", "", text)  # separators left at line starts


def extract_cv_text(filename: str, data: bytes) -> str:
    """Plain text of an uploaded CV. Raises CVError for unsupported, empty or unreadable files."""
    name = filename.lower()
    if not name.endswith(config.CV_EXTENSIONS):
        raise CVError(f"unsupported file type; use one of {', '.join(config.CV_EXTENSIONS)}")
    if len(data) > config.CV_MAX_BYTES:
        raise CVError(f"file is larger than {config.CV_MAX_BYTES // (1024 * 1024)} MB")
    if name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith(".docx"):
        text = _docx_text(data)
    else:
        text = data.decode("utf-8", errors="replace")
        if name.endswith(".tex"):
            text = _tex_text(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if len(text) < 40:
        raise CVError("no text found (is it a scanned image? Paste the text instead)")
    return text[: config.PROFILE_MAX_CHARS]
