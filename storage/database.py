import json
import sqlite3

from pathlib import Path

from models import Article


DB_PATH = Path(
    "data/articles.db"
)


def get_connection():

    DB_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    return sqlite3.connect(
        DB_PATH
    )


def initialize_database():

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            reporter TEXT,
            published_at TEXT,
            image_caption TEXT,
            paragraphs TEXT,
            url TEXT NOT NULL UNIQUE
        )
        """
    )

    connection.commit()
    connection.close()


def article_exists(url):

    connection = get_connection()

    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT 1
        FROM articles
        WHERE url = ?
        LIMIT 1
        """,
        (url,),
    )

    result = cursor.fetchone()

    connection.close()

    return result is not None


def save_article(
    article: Article
):

    connection = get_connection()

    cursor = connection.cursor()

    paragraphs_json = json.dumps(
        article.paragraphs,
        ensure_ascii=False
    )

    cursor.execute(
        """
        INSERT OR IGNORE INTO articles (
            source,
            title,
            reporter,
            published_at,
            image_caption,
            paragraphs,
            url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article.source,
            article.title,
            article.reporter,
            article.published_at,
            article.image_caption,
            paragraphs_json,
            article.url,
        ),
    )

    connection.commit()

    inserted = cursor.rowcount > 0

    connection.close()

    return inserted


def save_articles(articles):

    new_articles = []

    for article in articles:

        if save_article(article):
            new_articles.append(article)

    return new_articles
