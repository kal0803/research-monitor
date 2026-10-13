from urllib.parse import urlsplit, parse_qs
import re


def public_article_url(url):
    """사용자에게 표시할 원문 기사 주소. 내부 수집 URL은 변경하지 않는다."""
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw

    if (
        parsed.hostname == "m.dnews.co.kr"
        and parsed.path == "/m_home/view.jsp"
    ):
        idxno = parse_qs(parsed.query).get("idxno", [""])[0]
        if re.fullmatch(r"[0-9]+", idxno):
            return (
                "https://www.dnews.co.kr/uhtml/view.jsp"
                f"?idxno={idxno}"
            )

    return raw
