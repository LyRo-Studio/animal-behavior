from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CutMediaInfo(Base):
    """A Cut's cached probed media info (duration, resolution, codec) —
    ticket #22, see CONTEXT.md's "Media browser — Cut media-info caching"
    decision.

    Primary-keyed on `s3_key` alone (not the (key, etag) pair) so a later
    probe of the same Cut after its underlying object changes overwrites
    the stale row in place, rather than accumulating one row per ETag ever
    seen at that key. `etag` is what a lookup compares against the Cut's
    *current* S3 ETag (see app/services/cut_media_info.py) to decide
    whether the cached row is still fresh or needs re-probing.
    """

    __tablename__ = "cut_media_info"

    s3_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    etag: Mapped[str] = mapped_column(String(255), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    codec: Mapped[str] = mapped_column(String(100), nullable=False)
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
