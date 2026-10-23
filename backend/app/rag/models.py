from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    filename: str
    published_at: date
    source_url: str | None = None
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1)
    origin: Literal["bank", "fixture"] = "bank"
    extraction_profile: str = "legacy"


class Chunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    document_id: str
    page_number: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    extraction_method: Literal["text", "ocr", "vlm", "manual_transcription"]
    reviewed: bool = False


class PageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    page_number: int = Field(ge=1)
    method: Literal["text", "ocr", "vlm"]
    quality_ok: bool
    issues: list[str] = Field(default_factory=list)
    image_path: str | None = None


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    documents: list[Document]
    chunks: list[Chunk]
    pages: list[PageExtraction] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_references(self):
        docs = {d.id: d for d in self.documents}
        if len(docs) != len(self.documents) or len({c.id for c in self.chunks}) != len(self.chunks):
            raise ValueError("Повторяющиеся ID в корпусе")
        positions = set()
        for chunk in self.chunks:
            doc = docs.get(chunk.document_id)
            if doc is None or chunk.page_number > doc.page_count:
                raise ValueError("Неверный документ или страница чанка")
            key = (chunk.document_id, chunk.page_number, chunk.chunk_index)
            if key in positions:
                raise ValueError("Повторяющаяся позиция чанка")
            positions.add(key)
        if len({(p.document_id, p.page_number) for p in self.pages}) != len(self.pages):
            raise ValueError("Повторяющиеся страницы")
        for page in self.pages:
            if page.document_id not in docs or page.page_number > docs[page.document_id].page_count:
                raise ValueError("Неверная ссылка страницы на документ")
        return self
