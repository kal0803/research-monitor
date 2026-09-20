import requests
from models import Document


OPENALEX_URL = "https://api.openalex.org/works"


def collect_openalex(limit=5):
    params = {
        "per-page": limit,
        "sort": "publication_date:desc"
    }

    response = requests.get(OPENALEX_URL, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    documents = []

    for item in data["results"]:
        authors = [
            authorship["author"]["display_name"]
            for authorship in item.get("authorships", [])
        ]

        document = Document(
            source="openalex",
            source_id=item.get("id", ""),
            title=item.get("display_name", ""),
            authors=authors,
            published_at=item.get("publication_date"),
            url=item.get("doi") or item.get("id", ""),
            abstract=None
        )

        documents.append(document)

    return documents
