import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Boolean, Text, DateTime, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    data_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    data_consent_date: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    credits: Mapped[list["Credit"]] = relationship(back_populates="user")
    jobs: Mapped[list["Job"]] = relationship(back_populates="user")


class Credit(Base):
    __tablename__ = "credits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped["User"] = relationship(back_populates="credits")

    @property
    def remaining(self) -> int:
        return self.total - self.used


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    input_image_url: Mapped[str | None] = mapped_column(Text)
    input_description: Mapped[str | None] = mapped_column(Text)
    input_format: Mapped[str | None] = mapped_column(String(20))
    generated_prompt: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(Text)
    result_type: Mapped[str | None] = mapped_column(String(10))
    kie_task_id: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(back_populates="jobs")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    wompi_reference: Mapped[str | None] = mapped_column(String(100), unique=True)
    wompi_status: Mapped[str | None] = mapped_column(String(20))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="COP")
    package_type: Mapped[str] = mapped_column(String(50), nullable=False)
    credits_granted: Mapped[int] = mapped_column(Integer, nullable=False)
    service_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
