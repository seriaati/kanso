"""MIME parsing fixtures: raw messages as mailers really send them."""

import base64

from kanso.gmail import mime


def raw(headers: str, body: bytes | str) -> bytes:
    head = headers.strip().replace("\n", "\r\n").encode()
    return head + b"\r\n\r\n" + (body.encode() if isinstance(body, str) else body)


def test_multipart_alternative_with_addresses_and_threading_headers() -> None:
    message = raw(
        """
From: "Reyes, Daniel" <daniel@example.com>
To: me@example.com, Priya Shah <priya@example.com>
Cc: =?UTF-8?B?VG9tw6FzIFNpbHZh?= <tomas@example.com>
Reply-To: replies@example.com
Subject: Re: Dinner on
 Saturday?
Message-ID: <abc@example.com>
In-Reply-To: <xyz@example.com>
References: <first@example.com>
 <xyz@example.com>
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="b1"
""",
        """--b1
Content-Type: text/plain; charset=utf-8

Works for me. 7pm?
--b1
Content-Type: text/html; charset=utf-8

<p>Works for me. <b>7pm?</b></p>
--b1--
""",
    )
    parsed = mime.parse(message)
    assert parsed.subject == "Re: Dinner on Saturday?"
    assert (parsed.from_name, parsed.from_email) == ("Reyes, Daniel", "daniel@example.com")
    assert parsed.to == [
        {"name": "", "email": "me@example.com"},
        {"name": "Priya Shah", "email": "priya@example.com"},
    ]
    assert parsed.cc == [{"name": "Tomás Silva", "email": "tomas@example.com"}]
    assert parsed.reply_to == [{"name": "", "email": "replies@example.com"}]
    assert parsed.message_id == "<abc@example.com>"
    assert parsed.in_reply_to == "<xyz@example.com>"
    assert parsed.references == "<first@example.com> <xyz@example.com>"
    assert parsed.body_text == "Works for me. 7pm?"
    assert parsed.body_html == "<p>Works for me. <b>7pm?</b></p>"
    assert parsed.attachments == []
    assert parsed.lang is None


def test_html_only_is_sanitized_and_gets_a_text_version() -> None:
    message = raw(
        """
From: news@example.com
Subject: News
Content-Type: text/html; charset=utf-8
""",
        """<html><head><title>T</title><style>p { color: red }</style></head>
<body><p onclick="steal()" style="margin:0">Hello <a href="javascript:alert(1)">there</a></p>
<script>alert(1)</script><img src="cid:logo@x" onerror="x()"><iframe src="https://evil"></iframe>
<table bgcolor="#fff"><tr><td>Cell</td></tr></table><form><input name="q"></form></body></html>""",
    )
    parsed = mime.parse(message)
    html = parsed.body_html or ""
    for gone in ("script", "onclick", "onerror", "javascript:", "iframe", "<form", "<input", "T</"):
        assert gone not in html
    for kept in ('<p style="margin:0">', 'src="cid:logo@x"', 'bgcolor="#fff"', "<style>"):
        assert kept in html
    assert parsed.body_text == "Hello there\n\nCell"


def test_gb2312_label_with_gbk_characters_and_8bit_headers() -> None:
    # The body says GB2312 but uses GBK-only characters (镕, 堃); the From name is raw 8-bit.
    body = "朱镕基和王堃开会。"
    message = raw(
        "Subject: =?gb2312?B?"
        + base64.b64encode("会议通知".encode("gbk")).decode()
        + "?=\nContent-Type: text/plain; charset=gb2312\nContent-Transfer-Encoding: 8bit",
        body.encode("gbk"),
    )
    message = "From: 王芳 <wang@example.cn>\r\n".encode("gbk") + message
    parsed = mime.parse(message)
    assert parsed.subject == "会议通知"
    assert parsed.from_name == "王芳"
    assert parsed.body_text == body
    assert parsed.lang == "zh-Hans"


def test_big5_is_traditional_chinese() -> None:
    message = raw(
        "Subject: =?big5?Q?"
        + "".join(f"={b:02X}" for b in "搬遷通知".encode("big5"))
        + "?=\nContent-Type: text/plain; charset=big5\nContent-Transfer-Encoding: base64",
        base64.b64encode("台北辦公室搬遷".encode("big5")).decode(),
    )
    parsed = mime.parse(message)
    assert parsed.subject == "搬遷通知"
    assert parsed.body_text == "台北辦公室搬遷"
    assert parsed.lang == "zh-Hant"


def test_iso_2022_jp_with_nec_extras() -> None:
    # "①" is a NEC extension Windows mailers put in ISO-2022-JP (row 13 of JIS X 0208).
    body = "\x1b$B-!\x1b(B " + "打ち合わせ".encode("iso2022_jp").decode("ascii")
    message = raw(
        "Subject: =?ISO-2022-JP?B?"
        + base64.b64encode("来週の予定".encode("iso2022_jp")).decode()
        + "?=\nContent-Type: text/plain; charset=ISO-2022-JP\nContent-Transfer-Encoding: 7bit",
        body,
    )
    parsed = mime.parse(message)
    assert parsed.subject == "来週の予定"
    assert parsed.body_text == "① 打ち合わせ"
    assert parsed.lang == "ja"


def test_shift_jis_and_content_language() -> None:
    message = raw(
        "Subject: hello\nContent-Language: ja-JP\nContent-Type: text/plain; charset=Shift_JIS",
        "髙橋さん、①よろしく".encode("cp932"),  # 髙 and ① exist only in Windows' Shift_JIS
    )
    parsed = mime.parse(message)
    assert parsed.body_text == "髙橋さん、①よろしく"
    assert parsed.lang == "ja"


def test_han_only_text_without_hints_is_chinese() -> None:
    message = raw("Subject: 明天开会\nContent-Language: en-US\nContent-Type: text/plain", "好")
    assert mime.parse(message).lang == "zh"


def test_attachments_inline_images_and_attached_message() -> None:
    message = raw(
        """
From: a@example.com
Subject: Files
Content-Type: multipart/mixed; boundary="outer"
""",
        """--outer
Content-Type: multipart/related; boundary="rel"

--rel
Content-Type: text/html; charset=utf-8

<p>Logo: <img src="cid:logo@example.com"></p>
--rel
Content-Type: image/png
Content-ID: <logo@example.com>
Content-Disposition: inline
Content-Transfer-Encoding: base64

iVBORw0KGgo=
--rel--
--outer
Content-Type: application/pdf
Content-Disposition: attachment; filename*=UTF-8''%E5%90%88%E5%90%8C.pdf
Content-Transfer-Encoding: base64

JVBERi0xLjQ=
--outer
Content-Type: application/octet-stream; name="=?UTF-8?B?5aCx5ZGK?=.zip"
Content-Disposition: attachment
Content-Transfer-Encoding: base64

UEsDBA==
--outer
Content-Type: message/rfc822

From: b@example.com
Subject: Forwarded

Inner body
--outer--
""",
    )
    parsed = mime.parse(message)
    assert 'src="cid:logo@example.com"' in (parsed.body_html or "")
    assert [
        (a.filename, a.mime_type, a.size, a.content_id, a.inline) for a in parsed.attachments
    ] == [
        ("attachment-1.png", "image/png", 8, "logo@example.com", True),
        ("合同.pdf", "application/pdf", 8, None, False),
        ("報告.zip", "application/octet-stream", 4, None, False),
        (
            "message.eml",
            "message/rfc822",
            len(b"From: b@example.com\nSubject: Forwarded\n\nInner body"),
            None,
            False,
        ),
    ]
    assert mime.attachment_content(message, parsed.attachments[1].part_index) == b"%PDF-1.4"
    assert mime.attachment_content(message, parsed.attachments[0].part_index) == (
        b"\x89PNG\r\n\x1a\n"
    )
    assert mime.attachment_content(message, 99) is None


def test_malformed_messages_still_parse() -> None:
    # Boundary never appears, unknown charset, broken encoded word, no headers to speak of.
    broken = raw(
        'Subject: =?bogus?Q?=E4=BD?=\nContent-Type: multipart/mixed; boundary="nope"',
        "just some text",
    )
    parsed = mime.parse(broken)
    assert parsed.body_text == "just some text"
    unknown_charset = raw(
        "Content-Type: text/plain; charset=x-made-up", "caf\xe9".encode("latin-1")
    )
    assert mime.parse(unknown_charset).body_text == "caf�"
    assert mime.parse(b"").body_text == ""
    assert mime.parse(b"\xff\xfe\x00garbage").subject == ""
