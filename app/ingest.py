"""Индексация базы знаний: читает data/*.md, режет на чанки, считает эмбеддинги, кладёт в pgvector.

Запуск: python -m app.ingest
Файлы, начинающиеся с "_", пропускаются (например, сырой вывод скрапера на ревью).
"""

import glob
import os
import time

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.db import DocumentChunk, FaqStat, SessionLocal, init_db
from app.rag import embeddings

DATA_DIR = "data"


def load_documents() -> list[tuple[str, str]]:
    docs = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.md"))):
        name = os.path.basename(path)
        if name.startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            docs.append((name, f.read()))
    return docs


def embed_with_retry(texts: list[str], attempts: int = 3) -> list[list[float]]:
    """Считает эмбеддинги с экспоненциальным бэкоффом — устойчиво к разовым сетевым сбоям."""
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return embeddings.embed_documents(texts)
        except Exception as exc:  # noqa: BLE001
            if attempt == attempts:
                raise
            print(f"  попытка {attempt}/{attempts} не удалась ({exc}); повтор через {delay:.0f}с")
            time.sleep(delay)
            delay *= 2
    return []  # недостижимо


def main() -> None:
    init_db()

    # На перезапусках (особенно в облаке) не переиндексируем зря: это экономит квоту
    # эмбеддингов и не сбрасывает кэш FAQ. Принудительно: REINGEST=1.
    force = os.getenv("REINGEST", "").lower() in ("1", "true", "yes")
    with SessionLocal() as session:
        existing = session.query(DocumentChunk).count()
    if existing and not force:
        print(f"База уже содержит {existing} чанков — пропускаю индексацию "
              f"(REINGEST=1 чтобы переиндексировать).")
        return

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=120,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
    )

    documents = load_documents()
    if not documents:
        print("Нет файлов в data/*.md — нечего индексировать.")
        return

    with SessionLocal() as session:
        # Полная переиндексация: чистим таблицу перед загрузкой.
        session.query(DocumentChunk).delete()
        # Инвалидация кэша ответов FAQ (счётчики нажатий сохраняем).
        session.query(FaqStat).update({FaqStat.answer: None})
        session.commit()

        total = 0
        for source, text in documents:
            chunks = splitter.split_text(text)
            vectors = embed_with_retry(chunks)
            for chunk, vector in zip(chunks, vectors):
                session.add(DocumentChunk(source=source, content=chunk, embedding=vector))
            total += len(chunks)
            print(f"  {source}: {len(chunks)} чанков")

        session.commit()

    print(f"Готово. Проиндексировано чанков: {total}")


if __name__ == "__main__":
    main()
