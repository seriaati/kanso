import pytest

from kanso.codes import find_code


@pytest.mark.parametrize(
    ("subject", "text", "code"),
    [
        ("Your verification code", "Your verification code is 482913. Valid 10 min.", "482913"),
        ("482913 is your Slack confirmation code", "Confirm your email address.", "482913"),
        ("Here is your GitHub launch code", "Launch code: 12345678", "12345678"),
        ("Security code", "Your Microsoft account security code: K7P2QX", "K7P2QX"),
        # Split codes are copied whole; the nearest number wins over an address further down.
        ("Sign in", "Enter this code to sign in: 482 913\n© 2026 Example, CA 94107", "482913"),
        ("Dein Bestätigungscode", "Dein Bestätigungscode lautet 123-456.", "123456"),
        ("認証コードのお知らせ", "認証コード：583920\nこのコードは10分間有効です。", "583920"),
        ("验证码", "您的验证码739201，5分钟内有效。", "739201"),
        # Not verification mails, or no code near the phrase.
        ("Verify your email", "Click to verify your email: https://example.com/v/123456", None),
        ("Your order has shipped", "Tracking number 1Z999AA10123456784.", None),
        ("Use code SAVE20", "Use code SAVE20 for 20% off.", None),
        ("Your bill", "Your bill is $64.20, due 2026-11-15. Verify the amount.", None),
        ("Lunch?", "Are you free at 1230 tomorrow?", None),
    ],
)
def test_find_code(subject: str, text: str, code: str | None) -> None:
    assert find_code(subject, text) == code
