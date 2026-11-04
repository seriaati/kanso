"""The one-time code in a verification mail (sign-in, 2FA, confirming an address), offered for
copying in the thread list. Plain rules, not AI: the code is wanted the moment the mail arrives,
and this works without an API key."""

import re
from datetime import timedelta

# Phrases that say a mail carries a one-time code. A code counts only near one of them.
_KEYWORDS = re.compile(
    r"verif|one[- ]time|\botp\b|passcode|pass code|security code|confirmation code|access code"
    r"|(?:sign|log)[- ]?in code|launch code|auth(?:entication)? code|2fa|two[- ]factor"
    r"|enter (?:this|the following|the) code|code de (?:vérification|connexion|sécurité)"
    r"|código de (?:verificación|verificação|seguridad|segurança|acceso)|bestätigungscode"
    r"|sicherheitscode|anmeldecode|認証|確認コード|ワンタイム|验证码|驗證碼|校验码|动态码|인증",
    re.IGNORECASE,
)
# 4 to 8 digits ("482913", "482 913", "482-913"), or 5 to 8 capitals and digits mixing both
# ("K7P2QX"). Not part of a longer token, an address, a URL, an amount or a date.
_CODE = re.compile(
    r"(?<![\w.,/#$€£¥@+-])"
    r"(\d{4,8}|\d{3}[ -]\d{3}|(?=[A-Z]*\d)(?=\d*[A-Z])[A-Z\d]{5,8})"
    r"(?![\w/@%€$:+]|[.,-]\w)",
    re.ASCII,  # so CJK text may touch a code: "验证码739201"
)
# How far a code may be from its phrase, in characters.
_NEAR = 80
# Codes come near the top; this also bounds the work on long mails.
HEAD = 4000
# Codes expire within minutes; past this the list shows a mail's code no more.
FRESH = timedelta(days=1)


def find_code(subject: str, text: str) -> str | None:
    content = f"{subject}\n{text[:HEAD]}"
    phrases = [m.span() for m in _KEYWORDS.finditer(content)]
    if not phrases:
        return None
    best: tuple[int, str] | None = None
    for match in _CODE.finditer(content):
        start, end = match.span(1)
        distance = min(max(p_start - end, start - p_end, 0) for p_start, p_end in phrases)
        if distance <= _NEAR and (best is None or distance < best[0]):
            best = (distance, re.sub(r"[ -]", "", match.group(1)))
    return best[1] if best else None
